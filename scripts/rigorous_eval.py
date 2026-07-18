"""RIGOROUS-EVAL — statistically defensible model comparison.

Fixes three flaws in scripts/weekly_eval_and_compare.py that make a
scientific reviewer wince:

  1. **In-sample contamination.** The legacy script uses a rolling
     "last N days" window that OVERLAPS the training window when a
     candidate was trained recently. We force the caller to pass each
     bundle's training cutoff and evaluate only on rows AFTER
     max(cutoff_cand, cutoff_prod) — strictly OOS for both models.

  2. **No confidence intervals.** "Candidate log_loss 0.585 vs
     production 0.632" is meaningless without knowing the sampling
     noise. We compute 95% bootstrap CIs on log_loss + Brier so the
     verdict is BETTER / WORSE / TIE with statistical backing, not
     eyeballed thresholds.

  3. **No baseline.** Beating another version isn't the same as beating
     the MARKET. We also score two baselines:
       - Pinnacle-implied probability (the sharpest available line —
         "does our model add anything over Pinnacle?")
       - Opening implied (naive: what the book said at open)
     If the model doesn't beat opening-implied, we don't have a model,
     we have expensive noise.

Usage:
    python3 scripts/rigorous_eval.py \\
        --candidate v20260712 --candidate-cutoff 2026-07-11 \\
        --production v20260705 --production-cutoff 2026-07-05 \\
        --eval-end 2026-07-18 --bootstrap-n 1000

Prints per-market log_loss with 95% CIs, Δlog_loss with p-value,
and a "beats-Pinnacle" flag. Also breaks down by tier and per market
subgroup (1X2, OU, BTTS, AH).

Rollback signal: if the strictly-OOS eval shows candidate WORSE than
production with statistical significance, revert `MODEL_VERSION` on
VPS to production and open a real investigation.
"""
from __future__ import annotations
import os
import sys
import math
import json
import argparse
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
import joblib
import numpy as np
from rich.console import Console
from rich.table import Table

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"

# ── Import evaluation primitives from weekly_eval so we stay in sync ──────
from scripts.weekly_eval_and_compare import (
    _ensure_local, _load_bundle, _truth_1x2, _truth_ou25, _truth_btts,
    _AH_LINES, _ah_truth_home, _ah_prob_home, _safe_log, _build_row,
)


# ── Bootstrap ────────────────────────────────────────────────────────────
def _bootstrap_metric(values: np.ndarray, n_resamples: int = 1000,
                      ci_pct: float = 95.0, rng_seed: int = 42) -> tuple[float, float, float]:
    """Return (mean, ci_low, ci_high) for the mean of `values` via
    non-parametric bootstrap. `values` is the per-row contribution
    (e.g. per-row log-loss, per-row squared error)."""
    rng = np.random.default_rng(rng_seed)
    n = len(values)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    means = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        means[i] = values[idx].mean()
    alpha = (100 - ci_pct) / 2
    return float(values.mean()), float(np.percentile(means, alpha)), float(np.percentile(means, 100 - alpha))


def _paired_p_value(deltas: np.ndarray, n_resamples: int = 1000, rng_seed: int = 42) -> float:
    """One-sided bootstrap p-value: P(mean(delta) >= 0 | H0: mean = 0).
    Positive delta = candidate WORSE (higher log-loss). Small p → candidate
    reliably better."""
    if len(deltas) == 0:
        return float("nan")
    rng = np.random.default_rng(rng_seed)
    centered = deltas - deltas.mean()
    n = len(centered)
    boot_means = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        boot_means[i] = centered[idx].mean()
    return float((boot_means >= deltas.mean()).mean())


# ── Row-level scoring ────────────────────────────────────────────────────
def _score_rows_with_bundle(version: str, rows: list) -> dict:
    """Return dict market → list of (pred_prob, truth_bool, row_id).

    We collect per-row scores (not aggregated) so bootstrap can resample.
    """
    if not _ensure_local(version):
        console.print(f"[red]Bundle {version} not in local or Storage[/red]")
        return {}
    bundle = _load_bundle(version)
    if not bundle:
        return {}
    fc = bundle["feature_cols"]
    can_score_goals = bundle.get("home_goals") is not None and bundle.get("away_goals") is not None
    from workers.model.joint_probability import build_joint_matrix

    per_market: dict[str, list] = defaultdict(list)
    for r in rows:
        sh, sa = int(r["score_home"]), int(r["score_away"])
        truth_3 = _truth_1x2(sh, sa)
        truth_2 = _truth_ou25(sh, sa)
        truth_btts = _truth_btts(sh, sa)
        tier = r.get("tier") or 1
        row = _build_row(dict(r), fc, tier)
        X = np.array([[row[c] for c in fc]], dtype=float)
        try:
            probs_1x2 = bundle["result_1x2"].predict_proba(X)[0]
            probs_ou = bundle["over_under"].predict_proba(X)[0]
        except Exception:
            continue
        rid = r.get("match_id")
        for i, mkt in enumerate(["1x2_home", "1x2_draw", "1x2_away"]):
            per_market[mkt].append((float(probs_1x2[i]), truth_3[i], rid, tier))
        for i, mkt in enumerate(["over25", "under25"]):
            per_market[mkt].append((float(probs_ou[i]), truth_2[i], rid, tier))
        if not can_score_goals:
            continue
        try:
            exp_h = max(0.05, float(bundle["home_goals"].predict(X)[0]))
            exp_a = max(0.05, float(bundle["away_goals"].predict(X)[0]))
            matrix = build_joint_matrix(exp_h, exp_a)
        except Exception:
            continue
        n = matrix.shape[0]
        h_grid, a_grid = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        p_btts_yes = float(matrix[(h_grid >= 1) & (a_grid >= 1)].sum())
        p_btts_no = 1.0 - p_btts_yes
        per_market["btts_yes"].append((p_btts_yes, truth_btts[0], rid, tier))
        per_market["btts_no"].append((p_btts_no, truth_btts[1], rid, tier))
        for name, line in _AH_LINES:
            p_home = _ah_prob_home(matrix, line)
            truth = _ah_truth_home(sh, sa, line)
            per_market[name].append((p_home, truth, rid, tier))
    return per_market


def _score_baseline(rows: list, baseline: str) -> dict:
    """Score a baseline using odds from MFV. `baseline` in
    {"pinnacle", "opening_implied"} — Pinnacle uses `pinnacle_implied_over25`
    for OU and derives 1X2 from `opening_implied_* × pinnacle_drift_*` (MFV
    doesn't store post-drift Pinnacle 1X2 probs directly). Opening baseline
    uses `opening_implied_*` for 1X2 only — no opening OU column."""
    per_market: dict[str, list] = defaultdict(list)
    for r in rows:
        sh, sa = int(r["score_home"]), int(r["score_away"])
        tier = r.get("tier") or 1
        rid = r.get("match_id")
        truth_3 = _truth_1x2(sh, sa)
        truth_2 = _truth_ou25(sh, sa)
        if baseline == "pinnacle":
            # 1X2: opening + drift → sharper closing estimate. Fallback to
            # opening_implied if drift missing.
            oh = r.get("opening_implied_home"); od_ = r.get("opening_implied_draw"); oa = r.get("opening_implied_away")
            dh = r.get("pinnacle_drift_home") or 0
            dd = r.get("pinnacle_drift_draw") or 0
            da = r.get("pinnacle_drift_away") or 0
            if oh is not None and od_ is not None and oa is not None:
                ph = float(oh) + float(dh)
                pd_ = float(od_) + float(dd)
                pa = float(oa) + float(da)
                # Renormalise to sum-to-1 in case drifts + opening don't add up
                s = ph + pd_ + pa
                if s > 0:
                    ph, pd_, pa = ph/s, pd_/s, pa/s
                    for i, (mkt, p) in enumerate([("1x2_home", ph), ("1x2_draw", pd_), ("1x2_away", pa)]):
                        per_market[mkt].append((max(0.001, min(0.999, p)), truth_3[i], rid, tier))
            pou = r.get("pinnacle_implied_over25")
            if pou is not None:
                p = float(pou)
                per_market["over25"].append((p, truth_2[0], rid, tier))
                per_market["under25"].append((1 - p, truth_2[1], rid, tier))
        else:  # opening_implied — 1X2 only
            ph = r.get("opening_implied_home"); pd_ = r.get("opening_implied_draw"); pa = r.get("opening_implied_away")
            if ph is not None and pd_ is not None and pa is not None:
                for i, (mkt, p) in enumerate([("1x2_home", ph), ("1x2_draw", pd_), ("1x2_away", pa)]):
                    per_market[mkt].append((float(p), truth_3[i], rid, tier))
    return per_market


def _summarize_market(per_market: dict, bootstrap_n: int) -> dict:
    """Return market → {n, log_loss, ll_ci, brier, brier_ci, hit_rate, pred_rate}."""
    out = {}
    for mkt, tuples in per_market.items():
        if not tuples:
            continue
        preds = np.array([t[0] for t in tuples])
        truths = np.array([t[1] for t in tuples])
        ll_per_row = -np.array([
            _safe_log(preds[i]) if truths[i] else _safe_log(1 - preds[i])
            for i in range(len(preds))
        ])
        brier_per_row = (preds - truths) ** 2
        ll_mean, ll_lo, ll_hi = _bootstrap_metric(ll_per_row, bootstrap_n)
        br_mean, br_lo, br_hi = _bootstrap_metric(brier_per_row, bootstrap_n)
        out[mkt] = {
            "n": int(len(tuples)),
            "log_loss": ll_mean,
            "log_loss_ci": (ll_lo, ll_hi),
            "brier": br_mean,
            "brier_ci": (br_lo, br_hi),
            "hit_rate": float(truths.mean()),
            "pred_rate": float(preds.mean()),
            "_ll_per_row": ll_per_row,
            "_row_ids": [t[2] for t in tuples],
        }
    return out


def _paired_delta(cand_summary: dict, prod_summary: dict, bootstrap_n: int) -> dict:
    """For each market present in both, compute Δlog_loss + p-value on
    the paired difference (candidate - production).

    Positive Δ = candidate WORSE. Small p → candidate reliably better.
    """
    out = {}
    for mkt in cand_summary:
        if mkt not in prod_summary:
            continue
        c_ll = cand_summary[mkt]["_ll_per_row"]
        p_ll = prod_summary[mkt]["_ll_per_row"]
        # Align by row_id — both lists SHOULD be in the same order since
        # we iterate `rows` deterministically, but a bundle can drop rows
        # on predict_proba exceptions. Intersection by row_id is safe.
        c_ids = cand_summary[mkt]["_row_ids"]
        p_ids = prod_summary[mkt]["_row_ids"]
        c_map = {rid: c_ll[i] for i, rid in enumerate(c_ids)}
        p_map = {rid: p_ll[i] for i, rid in enumerate(p_ids)}
        shared = [rid for rid in c_ids if rid in p_map]
        if len(shared) < 30:
            continue  # too few for meaningful test
        deltas = np.array([c_map[rid] - p_map[rid] for rid in shared])
        p_val = _paired_p_value(deltas, bootstrap_n)
        # Also compute Δ%: (cand - prod) / prod × 100
        c_mean = np.mean([c_map[rid] for rid in shared])
        p_mean = np.mean([p_map[rid] for rid in shared])
        d_pct = 100 * (c_mean - p_mean) / p_mean if p_mean else float("nan")
        out[mkt] = {
            "n_shared": len(shared),
            "delta_ll": float(c_mean - p_mean),
            "delta_ll_pct": d_pct,
            "p_value": p_val,
        }
    return out


def _print_summary_table(title: str, summary: dict, mkts_order: list):
    tbl = Table(title=title, show_lines=False)
    tbl.add_column("market")
    tbl.add_column("n", justify="right")
    tbl.add_column("log_loss  [95% CI]", justify="right")
    tbl.add_column("brier  [95% CI]", justify="right")
    tbl.add_column("hit%", justify="right")
    tbl.add_column("pred%", justify="right")
    for mkt in mkts_order:
        r = summary.get(mkt)
        if not r:
            continue
        ll_lo, ll_hi = r["log_loss_ci"]
        br_lo, br_hi = r["brier_ci"]
        tbl.add_row(
            mkt, str(r["n"]),
            f"{r['log_loss']:.4f}  [{ll_lo:.4f}, {ll_hi:.4f}]",
            f"{r['brier']:.4f}  [{br_lo:.4f}, {br_hi:.4f}]",
            f"{r['hit_rate']*100:.1f}",
            f"{r['pred_rate']*100:.1f}",
        )
    console.print(tbl)


def _paired_delta_by_tier(cand_raw: dict, prod_raw: dict, bootstrap_n: int) -> dict:
    """Per-tier paired delta. Returns {tier: {market: {n_shared, delta_ll,
    delta_ll_pct, p_value}}}.

    Filters each market's per-row tuples to only rows in tier T, then
    runs the same paired bootstrap machinery on that slice. Useful for
    "is the improvement concentrated in top tiers or uniform?"
    """
    out: dict[int, dict] = {}
    all_tiers = sorted({
        t for mkt_tuples in cand_raw.values() for _, _, _, t in mkt_tuples
        if t is not None
    })
    for tier in all_tiers:
        tier_out: dict = {}
        for mkt in cand_raw:
            if mkt not in prod_raw:
                continue
            c_pairs = [(pred, tr, rid) for pred, tr, rid, t in cand_raw[mkt] if t == tier]
            p_pairs = [(pred, tr, rid) for pred, tr, rid, t in prod_raw[mkt] if t == tier]
            if not c_pairs or not p_pairs:
                continue
            c_map = {rid: (pred, tr) for pred, tr, rid in c_pairs}
            p_map = {rid: (pred, tr) for pred, tr, rid in p_pairs}
            shared = [rid for rid in c_map if rid in p_map]
            if len(shared) < 30:
                continue
            c_ll = np.array([
                -_safe_log(c_map[rid][0]) if c_map[rid][1] else -_safe_log(1 - c_map[rid][0])
                for rid in shared
            ])
            p_ll = np.array([
                -_safe_log(p_map[rid][0]) if p_map[rid][1] else -_safe_log(1 - p_map[rid][0])
                for rid in shared
            ])
            deltas = c_ll - p_ll
            p_val = _paired_p_value(deltas, bootstrap_n)
            d_pct = 100 * deltas.mean() / p_ll.mean() if p_ll.mean() else float("nan")
            tier_out[mkt] = {
                "n_shared": len(shared),
                "delta_ll": float(deltas.mean()),
                "delta_ll_pct": float(d_pct),
                "p_value": p_val,
            }
        if tier_out:
            out[tier] = tier_out
    return out


def _print_per_tier_table(title: str, per_tier_deltas: dict, mkts_order: list):
    tbl = Table(title=title, show_lines=False)
    tbl.add_column("tier", justify="right")
    tbl.add_column("market")
    tbl.add_column("n", justify="right")
    tbl.add_column("Δ log-loss", justify="right")
    tbl.add_column("Δ %", justify="right")
    tbl.add_column("p", justify="right")
    tbl.add_column("verdict", justify="left")
    for tier in sorted(per_tier_deltas.keys()):
        for mkt in mkts_order:
            d = per_tier_deltas[tier].get(mkt)
            if not d:
                continue
            verdict = "→ tie"
            if d["delta_ll"] < 0 and d["p_value"] > 0.95:
                verdict = "[green]✓ BETTER[/green]"
            elif d["delta_ll"] > 0 and d["p_value"] < 0.05:
                verdict = "[red]✗ WORSE[/red]"
            elif d["delta_ll"] < 0:
                verdict = "→ better"
            elif d["delta_ll"] > 0:
                verdict = "→ worse"
            tbl.add_row(
                str(tier), mkt, str(d["n_shared"]),
                f"{d['delta_ll']:+.4f}", f"{d['delta_ll_pct']:+.1f}%",
                f"{d['p_value']:.3f}", verdict,
            )
    console.print(tbl)


def _print_delta_table(title: str, deltas: dict, mkts_order: list):
    tbl = Table(title=title, show_lines=False)
    tbl.add_column("market")
    tbl.add_column("n paired", justify="right")
    tbl.add_column("Δ log_loss", justify="right")
    tbl.add_column("Δ %", justify="right")
    tbl.add_column("p-value", justify="right")
    tbl.add_column("verdict", justify="left")
    # The p_value in `deltas` is one-sided: P(centered bootstrap mean ≥
    # observed delta | H0). Interpretation:
    #   - delta < 0 (candidate better) AND p > 0.95 → reject H0 in favour
    #     of "candidate reliably better"
    #   - delta > 0 (candidate worse) AND p < 0.05 → reject H0 in favour
    #     of "candidate reliably worse"
    for mkt in mkts_order:
        d = deltas.get(mkt)
        if not d:
            continue
        verdict = "→ TIE"
        if d["delta_ll"] < 0 and d["p_value"] > 0.95:
            verdict = "[green]✓ BETTER (p<0.05)[/green]"
        elif d["delta_ll"] > 0 and d["p_value"] < 0.05:
            verdict = "[red]✗ WORSE (p<0.05)[/red]"
        elif d["delta_ll"] < 0:
            verdict = "→ better (not sig)"
        elif d["delta_ll"] > 0:
            verdict = "→ worse (not sig)"
        tbl.add_row(
            mkt, str(d["n_shared"]),
            f"{d['delta_ll']:+.4f}",
            f"{d['delta_ll_pct']:+.1f}%",
            f"{d['p_value']:.3f}",
            verdict,
        )
    console.print(tbl)


def main():
    ap = argparse.ArgumentParser(description="Rigorous OOS eval with bootstrap CIs + baselines.")
    ap.add_argument("--candidate", required=True, help="Candidate model version")
    ap.add_argument("--candidate-cutoff", required=True,
                    help="Candidate training cutoff YYYY-MM-DD. Eval starts strictly after this.")
    ap.add_argument("--production", required=True, help="Production model version")
    ap.add_argument("--production-cutoff", required=True,
                    help="Production training cutoff YYYY-MM-DD.")
    ap.add_argument("--eval-end", default=date.today().isoformat(), help="Eval end date (default today)")
    ap.add_argument("--bootstrap-n", type=int, default=1000, help="Bootstrap resamples (default 1000)")
    ap.add_argument("--min-n", type=int, default=100, help="Minimum matches to run (default 100)")
    args = ap.parse_args()

    from datetime import date as _d
    cand_cut = _d.fromisoformat(args.candidate_cutoff)
    prod_cut = _d.fromisoformat(args.production_cutoff)
    eval_start = max(cand_cut, prod_cut) + timedelta(days=1)
    eval_end = _d.fromisoformat(args.eval_end)
    if eval_start > eval_end:
        console.print(f"[red]Eval window empty: start {eval_start} > end {eval_end}[/red]")
        sys.exit(1)
    console.print(f"[bold]Strict OOS eval window[/bold]: {eval_start} → {eval_end}")
    console.print(f"  candidate={args.candidate} (cutoff {cand_cut}), "
                  f"production={args.production} (cutoff {prod_cut})")

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.cursor().execute("SET statement_timeout='180s'")
    dc = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dc.execute("""
        SELECT mfv.*, m.score_home, m.score_away, l.tier
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE mfv.match_date >= %s AND mfv.match_date <= %s
          AND m.status='finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
    """, (eval_start.isoformat(), eval_end.isoformat()))
    rows = dc.fetchall()
    conn.close()
    console.print(f"  {len(rows):,} settled matches in strict OOS window")
    if len(rows) < args.min_n:
        console.print(f"[yellow]Too few matches ({len(rows)} < {args.min_n}) — widen --eval-end or wait.[/yellow]")
        sys.exit(2)

    # Score candidate + production + baselines
    console.print(f"\n[bold]Scoring {args.candidate}[/bold]")
    cand_raw = _score_rows_with_bundle(args.candidate, rows)
    console.print(f"[bold]Scoring {args.production}[/bold]")
    prod_raw = _score_rows_with_bundle(args.production, rows)
    console.print(f"[bold]Scoring Pinnacle baseline[/bold]")
    pin_raw = _score_baseline(rows, "pinnacle")
    console.print(f"[bold]Scoring opening-implied baseline[/bold]")
    open_raw = _score_baseline(rows, "opening_implied")

    console.print(f"\n[bold]Aggregating + bootstrapping (n={args.bootstrap_n})[/bold]")
    cand = _summarize_market(cand_raw, args.bootstrap_n)
    prod = _summarize_market(prod_raw, args.bootstrap_n)
    pinn = _summarize_market(pin_raw, args.bootstrap_n)
    openi = _summarize_market(open_raw, args.bootstrap_n)

    mkts_order = [
        "1x2_home", "1x2_draw", "1x2_away",
        "over25", "under25",
        "btts_yes", "btts_no",
        "ah_home_-0.5", "ah_home_+0.5", "ah_home_-1.5", "ah_home_+1.5",
    ]

    console.print()
    _print_summary_table(f"{args.candidate} — per-market metrics with 95% CIs", cand, mkts_order)
    console.print()
    _print_summary_table(f"{args.production} — per-market metrics with 95% CIs", prod, mkts_order)
    console.print()
    _print_summary_table("Pinnacle-close baseline (sharpest market)", pinn, mkts_order)
    console.print()
    _print_summary_table("Opening-implied baseline", openi, mkts_order)

    console.print(f"\n[bold]Paired comparisons (positive Δ = candidate WORSE)[/bold]")
    console.print()
    _print_delta_table(
        f"{args.candidate} vs {args.production}",
        _paired_delta(cand, prod, args.bootstrap_n), mkts_order,
    )
    console.print()
    _print_delta_table(
        f"{args.candidate} vs Pinnacle-close (does the model add anything?)",
        _paired_delta(cand, pinn, args.bootstrap_n), mkts_order,
    )
    console.print()
    _print_delta_table(
        f"{args.production} vs Pinnacle-close",
        _paired_delta(prod, pinn, args.bootstrap_n), mkts_order,
    )

    # Per-tier breakdown — same paired methodology, sliced by league.tier.
    console.print()
    _print_per_tier_table(
        f"{args.candidate} vs {args.production} — by tier (n≥30 required per cell)",
        _paired_delta_by_tier(cand_raw, prod_raw, args.bootstrap_n), mkts_order,
    )

    # Emit machine-readable summary for downstream tooling
    summary = {
        "eval_window": [eval_start.isoformat(), eval_end.isoformat()],
        "n_matches": len(rows),
        "bootstrap_n": args.bootstrap_n,
        "candidate": args.candidate,
        "production": args.production,
        "candidate_metrics": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                              for k, v in cand.items()},
        "production_metrics": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                               for k, v in prod.items()},
        "pinnacle_metrics": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                             for k, v in pinn.items()},
        "delta_cand_vs_prod": _paired_delta(cand, prod, args.bootstrap_n),
        "delta_cand_vs_pinnacle": _paired_delta(cand, pinn, args.bootstrap_n),
        "delta_prod_vs_pinnacle": _paired_delta(prod, pinn, args.bootstrap_n),
    }
    print(f"\nSUMMARY_JSON: {json.dumps(summary, default=float)}")


if __name__ == "__main__":
    main()
