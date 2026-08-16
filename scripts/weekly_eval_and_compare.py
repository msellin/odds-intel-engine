"""WEEKLY-EVAL — called by job_weekly_retrain after the new bundle is trained.

Runs offline held-out evaluation of CANDIDATE vs PRODUCTION on the last 14
days of settled MFV rows, writes results to model_versions.cv_metrics for
both versions, and prints a comparison summary. Replaces the legacy
compare_models.py call which structurally returned 0-overlap (candidate
bundles never produce predictions, so the predictions-table-overlap path
returned nothing).

Usage:
    python3 scripts/weekly_eval_and_compare.py <candidate> <production>
    python3 scripts/weekly_eval_and_compare.py v20260524 v14
"""
from __future__ import annotations
import os, sys, math, json, argparse
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
import psycopg2.extras
import joblib
import numpy as np
from datetime import date, timedelta
from rich.console import Console

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"


def _ensure_local(version: str) -> bool:
    bp = MODELS_DIR / version
    if (bp / "feature_cols.pkl").exists():
        return True
    from workers.model.storage import ensure_local_bundle
    return ensure_local_bundle(version, MODELS_DIR)


def _load_bundle(version: str) -> dict | None:
    bp = MODELS_DIR / version
    if not (bp / "feature_cols.pkl").exists():
        return None
    bundle = {
        "feature_cols": joblib.load(bp / "feature_cols.pkl"),
        "result_1x2":   joblib.load(bp / "result_1x2.pkl"),
        "over_under":   joblib.load(bp / "over_under.pkl"),
    }
    # MARKET-EVAL-BTTS-AH (2026-05-24): also load the Poisson goal regressors
    # so we can derive BTTS / AH probabilities from the joint goal distribution —
    # same path production uses for those markets. Missing files are OK on
    # legacy v9* bundles; the BTTS/AH eval branches skip them in that case.
    for fname, key in [("home_goals.pkl", "home_goals"), ("away_goals.pkl", "away_goals")]:
        fp = bp / fname
        bundle[key] = joblib.load(fp) if fp.exists() else None
    return bundle


def _truth_1x2(sh, sa):
    if sh > sa: return [1, 0, 0]
    if sh == sa: return [0, 1, 0]
    return [0, 0, 1]


def _truth_ou25(sh, sa):
    return [1, 0] if (sh + sa) > 2.5 else [0, 1]


def _truth_btts(sh, sa):
    """[btts_yes, btts_no] one-hot truth."""
    return [1, 0] if (sh > 0 and sa > 0) else [0, 1]


# AH lines we score. Half-lines only — they never push, so the binary truth
# label is well-defined and log-loss is interpretable. Integer lines (0/1/2)
# are skipped here to avoid the half-win/push complication.
_AH_LINES = [
    ("ah_home_-0.5", -0.5),  # home -0.5: covers iff home wins
    ("ah_home_+0.5",  0.5),  # home +0.5: covers iff home wins or draws
    ("ah_home_-1.5", -1.5),  # home -1.5: covers iff home wins by 2+
    ("ah_home_+1.5",  1.5),  # home +1.5: covers iff home loses by ≤1 (or wins/draws)
]


def _ah_truth_home(sh: int, sa: int, line: float) -> int:
    """1 if home covers the AH line, 0 otherwise. Half-lines never push."""
    margin = sh - sa  # positive = home wins by N
    return 1 if (margin + line) > 0 else 0


def _ah_prob_home(matrix, line: float) -> float:
    """P(home covers AH `line`) from the joint goal matrix."""
    n = matrix.shape[0]
    h_grid, a_grid = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    margin = h_grid - a_grid
    mask = (margin + line) > 0
    return float(matrix[mask].sum())


def _safe_log(p, eps=1e-7):
    return math.log(max(eps, min(1 - eps, p)))


def _build_row(raw: dict, feature_cols, tier: int) -> dict:
    row = {}
    for col in feature_cols:
        if col == "tier":
            row[col] = tier
            continue
        if col.endswith("_missing"):
            base = col[: -len("_missing")]
            row[col] = 1 if (raw.get(base) is None) else 0
            continue
        v = raw.get(col)
        try:
            row[col] = 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            row[col] = 0.0
    return row


def evaluate(version: str, rows: list) -> dict | None:
    """Return per-market metrics for one version against the held-out rows."""
    if not _ensure_local(version):
        console.print(f"[red]Bundle {version} not in local or Storage — skip[/red]")
        return None
    bundle = _load_bundle(version)
    if not bundle:
        return None
    fc = bundle["feature_cols"]

    # MARKET-EVAL-BTTS-AH: import the same joint-matrix builder production uses
    # for BTTS / AH derivation, so the offline eval matches inference path.
    from workers.model.joint_probability import build_joint_matrix

    can_score_goals = bundle.get("home_goals") is not None and bundle.get("away_goals") is not None

    metrics = defaultdict(lambda: {"ll": 0.0, "brier": 0.0, "hits": 0, "n": 0, "pred_sum": 0.0})
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
        # DRAW-CALIBRATION-EVAL-PARITY-2026-08-16: apply the same shrink the
        # live inference path applies (workers/model/xgboost_ensemble.py).
        # Without this, rigorous_eval bypasses the shrink and reports
        # uncalibrated numbers while the bots run on calibrated ones — the
        # eval would show "no change" and we'd never know if the shrink
        # was actually helping. classes_ order is [A, D, H] typically.
        from workers.model.draw_calibration import apply_draw_calibration
        classes = list(bundle["result_1x2"].classes_)
        if "H" in classes:
            idx_h, idx_d, idx_a = classes.index("H"), classes.index("D"), classes.index("A")
        else:
            # train.py outcome_map: home=0, draw=1, away=2. classes_ = [0,1,2].
            idx_h, idx_d, idx_a = 0, 1, 2
        _hp, _dp, _ap = apply_draw_calibration(
            float(probs_1x2[idx_h]), float(probs_1x2[idx_d]), float(probs_1x2[idx_a])
        )
        # Splat back into the same slots; downstream metrics loop uses
        # index 0=home, 1=draw, 2=away — same order as _truth_1x2, so we
        # rebuild probs_1x2 in that fixed order regardless of bundle
        # classes_ ordering.
        probs_1x2 = (_hp, _dp, _ap)
        for i, mkt in enumerate(["home", "draw", "away"]):
            p = float(probs_1x2[i])
            m = metrics[f"1x2_{mkt}"]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_3[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_3[i]) ** 2
            m["hits"] += truth_3[i]
        for i, mkt in enumerate(["over25", "under25"]):
            p = float(probs_ou[i])
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_2[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_2[i]) ** 2
            m["hits"] += truth_2[i]

        # MARKET-EVAL-BTTS-AH — score the Poisson-derived markets.
        # Production builds the DC-corrected joint matrix from
        # (home_goals, away_goals) regressors → derives BTTS + every AH line
        # from that matrix. We mirror that here exactly so a bundle's
        # BTTS/AH log-loss in eval reflects what it would produce in prod.
        if not can_score_goals:
            continue
        try:
            exp_h = max(0.05, float(bundle["home_goals"].predict(X)[0]))
            exp_a = max(0.05, float(bundle["away_goals"].predict(X)[0]))
            matrix = build_joint_matrix(exp_h, exp_a)
        except Exception:
            continue

        # BTTS: P(both teams score). Truth = score_home > 0 AND score_away > 0.
        n = matrix.shape[0]
        h_grid, a_grid = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        p_btts_yes = float(matrix[(h_grid >= 1) & (a_grid >= 1)].sum())
        p_btts_no = 1.0 - p_btts_yes
        for i, (mkt, p) in enumerate([("btts_yes", p_btts_yes), ("btts_no", p_btts_no)]):
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p
            m["ll"] += -_safe_log(p) if truth_btts[i] else -_safe_log(1 - p)
            m["brier"] += (p - truth_btts[i]) ** 2
            m["hits"] += truth_btts[i]

        # AH half-lines: no pushes, clean binary outcome. Only score the home
        # side of each line — the away-side probability is 1 - P(home covers)
        # and would just duplicate the log-loss signal.
        for mkt, line in _AH_LINES:
            p_home_covers = _ah_prob_home(matrix, line)
            truth = _ah_truth_home(sh, sa, line)
            m = metrics[mkt]
            m["n"] += 1
            m["pred_sum"] += p_home_covers
            m["ll"] += -_safe_log(p_home_covers) if truth else -_safe_log(1 - p_home_covers)
            m["brier"] += (p_home_covers - truth) ** 2
            m["hits"] += truth
    # Aggregate
    out = {}
    for mkt, m in metrics.items():
        if m["n"] == 0:
            continue
        out[mkt] = {
            "n": m["n"],
            "log_loss": round(m["ll"] / m["n"], 4),
            "brier": round(m["brier"] / m["n"], 4),
            "hit_rate": round(m["hits"] / m["n"], 4),
            "pred_rate": round(m["pred_sum"] / m["n"], 4),
        }
    return out


def _persist_metrics(version: str, holdout_window: tuple[str, str], n_matches: int, metrics: dict, note: str):
    """Write metrics back to model_versions.cv_metrics for audit + history."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    payload = {
        "holdout_window": f"{holdout_window[0]}..{holdout_window[1]}",
        "holdout_n_matches": n_matches,
        "metrics": metrics,
        "source": "scripts/weekly_eval_and_compare.py",
        "note": note,
    }
    cur.execute("UPDATE model_versions SET cv_metrics = %s::jsonb WHERE version = %s",
                (json.dumps(payload), version))
    conn.commit()
    cur.close(); conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("candidate", help="New candidate model version (e.g. v20260524)")
    p.add_argument("production", help="Currently-promoted version (e.g. v14)")
    p.add_argument("--days", type=int, default=14, help="Held-out window in days (default 14)")
    args = p.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    console.print(f"[bold]Held-out window: {start} → {end}[/bold]")

    # Load held-out matches
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.cursor().execute("SET statement_timeout='180s'")
    dc = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dc.execute("""
        SELECT mfv.*, m.score_home, m.score_away, l.tier
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE mfv.match_date >= %s AND mfv.match_date <= %s
          AND m.status='finished' AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
    """, (start.isoformat(), end.isoformat()))
    rows = dc.fetchall()
    conn.close()
    console.print(f"  {len(rows):,} settled MFV rows")
    if not rows:
        console.print("[red]No held-out data — exiting[/red]"); sys.exit(0)

    # Evaluate both
    console.print(f"\n[bold]Evaluating {args.candidate}[/bold]")
    cand_metrics = evaluate(args.candidate, rows)
    console.print(f"[bold]Evaluating {args.production}[/bold]")
    prod_metrics = evaluate(args.production, rows)

    if not cand_metrics or not prod_metrics:
        console.print("[red]Could not evaluate one of the versions[/red]"); sys.exit(1)

    # Persist
    cand_note = f"OFFLINE eval vs production={args.production} (n={len(rows)})"
    prod_note = f"OFFLINE eval baseline vs candidate={args.candidate} (n={len(rows)})"
    _persist_metrics(args.candidate, (start.isoformat(), end.isoformat()), len(rows), cand_metrics, cand_note)
    _persist_metrics(args.production, (start.isoformat(), end.isoformat()), len(rows), prod_metrics, prod_note)
    console.print(f"\n[green]Persisted cv_metrics for both versions[/green]")

    # Print comparison
    console.print(f"\n[bold]CANDIDATE {args.candidate} vs PRODUCTION {args.production}[/bold]\n")
    print(f"  {'market':<16}{'log_loss_cand':>14}{'log_loss_prod':>15}{'Δll%':>8}{'Δbrier%':>10}{'verdict':>12}")
    print("  " + "-" * 76)
    market_verdicts = {}
    eval_markets = [
        "1x2_home", "1x2_draw", "1x2_away",
        "over25", "under25",
        "btts_yes", "btts_no",
        "ah_home_-0.5", "ah_home_+0.5", "ah_home_-1.5", "ah_home_+1.5",
    ]
    for mkt in eval_markets:
        c = cand_metrics.get(mkt); p = prod_metrics.get(mkt)
        if not c or not p:
            continue
        ll_delta = 100 * (c["log_loss"] - p["log_loss"]) / p["log_loss"]
        br_delta = 100 * (c["brier"] - p["brier"]) / p["brier"]
        verdict = "BETTER" if ll_delta < -1 else ("WORSE" if ll_delta > 1 else "TIE")
        market_verdicts[mkt] = verdict
        print(f"  {mkt:<16}{c['log_loss']:>14.4f}{p['log_loss']:>15.4f}{ll_delta:>+7.1f}%{br_delta:>+9.1f}%{verdict:>12}")

    # Headline summary for cron output
    better = sum(1 for v in market_verdicts.values() if v == "BETTER")
    worse = sum(1 for v in market_verdicts.values() if v == "WORSE")
    ties = sum(1 for v in market_verdicts.values() if v == "TIE")
    console.print(f"\n[bold]Headline:[/bold] candidate is BETTER on {better} / WORSE on {worse} / TIED on {ties} markets")
    # Return JSON summary for downstream consumers (email digest)
    print()
    print("SUMMARY_JSON:", json.dumps({
        "candidate": args.candidate, "production": args.production,
        "holdout_window": f"{start}..{end}", "n_matches": len(rows),
        "market_verdicts": market_verdicts,
        "candidate_metrics": cand_metrics, "production_metrics": prod_metrics,
    }))


if __name__ == "__main__":
    main()
