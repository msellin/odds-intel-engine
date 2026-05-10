"""
Offline model evaluation — compare ANY number of model bundles on the same
held-out test slice. Answers "did our retraining actually improve the model?"
without waiting for a 14-day shadow deploy.

Each bundle in `data/models/soccer/<version>/` is loaded into its own dict
(not via the global `xgboost_ensemble._model_cache`), so multiple bundles
coexist in one process. Each is run on the same held-out MFV rows.

For v9 (Kaggle schema, can't read MFV), we read predictions from the DB tagged
`model_version='v9a_202425'` for the same match_ids — that's v9's actual lived
behaviour, not a re-simulation.

Metrics per market:
  - log_loss      lower = better. Punishes overconfident wrong calls.
  - Brier         lower = better. Mean squared error on the probability.
  - hit_rate      0/1 accuracy at p ≥ 0.5 cut-off.
  - ECE (Expected Calibration Error) — bin-weighted mean of |bin_avg_p - bin_hit_rate|.

Caveat printed at the bottom: bundles trained on the same MFV may have already
seen many of the matches in the test window. Numbers are upper-bound unless
you train each candidate with an explicit `--cutoff` arg first (separate task).

Usage:
  python3 scripts/offline_eval.py v10_pre_shadow v11_pinnacle
  python3 scripts/offline_eval.py v10_pre_shadow --since 2026-04-26 --include-v9
  python3 scripts/offline_eval.py v10_pre_shadow v11_pinnacle --save dev/active/cmp.md
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import joblib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

ENGINE_DIR = Path(__file__).parent.parent
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"

console = Console()


# ---------------------------------------------------------------------------
# Bundle loading — independent from xgboost_ensemble's module-level cache.
# ---------------------------------------------------------------------------

def _load_bundle(version: str) -> dict:
    """Load every .pkl in `data/models/soccer/<version>/` into a fresh dict."""
    path = MODELS_DIR / version
    if not path.exists():
        raise FileNotFoundError(f"Bundle not found: {path}")

    bundle = {"version": version, "path": path}
    bundle["feature_cols"] = joblib.load(path / "feature_cols.pkl")
    bundle["result_1x2"] = joblib.load(path / "result_1x2.pkl")
    bundle["over_under"] = joblib.load(path / "over_under.pkl")

    # Optional pieces — older bundles (v9*) don't have all of them.
    for opt in ("btts.pkl", "home_goals.pkl", "away_goals.pkl", "platt.pkl"):
        f = path / opt
        if f.exists():
            bundle[opt[:-4]] = joblib.load(f)
    return bundle


def _is_mfv_schema(feature_cols: list) -> bool:
    """v10+ MFV schema has `elo_home`, legacy v9* uses `home_elo`."""
    return "elo_home" in feature_cols and "home_elo" not in feature_cols


# Stage-2a indicator columns — must match training-side
# `train.py:INFORMATIVE_MISSING_COLS` exactly.
_INFORMATIVE_MISSING_COLS = (
    "h2h_win_pct",
    "opening_implied_home", "opening_implied_draw", "opening_implied_away",
    "bookmaker_disagreement",
    "referee_cards_avg", "referee_home_win_pct", "referee_over25_pct",
    "pinnacle_implied_home", "pinnacle_implied_draw", "pinnacle_implied_away",
)


def _build_x_from_mfv(mfv_rows: pd.DataFrame, feature_cols: list, default_tier: int = 1) -> pd.DataFrame:
    """Vectorised version of `_build_row_from_mfv` — emits one X DataFrame for
    a batch of MFV rows in the exact column order the bundle expects.

    Indicator columns (`<col>_missing`) are recomputed from the raw MFV row,
    matching how training built them. Missing values get zero-filled at
    inference (the indicator carries the real signal — same approximation
    `_build_row_from_mfv` makes in production)."""
    out = pd.DataFrame(index=mfv_rows.index)
    for col in feature_cols:
        if col == "tier":
            out[col] = default_tier
            continue
        if col == "league_tier":
            out[col] = pd.to_numeric(mfv_rows.get("league_tier"), errors="coerce").fillna(0).astype(float)
            continue
        if col.endswith("_missing"):
            base = col[:-len("_missing")]
            out[col] = mfv_rows.get(base).isna().astype(int) if base in mfv_rows else 1
            continue
        if col in mfv_rows.columns:
            out[col] = pd.to_numeric(mfv_rows[col], errors="coerce").fillna(0.0).astype(float)
        else:
            # v9-only feature absent from MFV (h_*, a_*, xg_diff, form_diff, etc.).
            # The bundle was trained on these → row is structurally incomplete.
            out[col] = 0.0
    return out[feature_cols]


# ---------------------------------------------------------------------------
# Platt calibration
# ---------------------------------------------------------------------------

def _apply_platt(p: np.ndarray, platt_dict: dict | None, market_key: str) -> np.ndarray:
    """Apply Platt calibration if the bundle shipped one for this market. The
    formula MUST match `scripts/fit_platt_offline.py:_platt` — that script
    fits `sigmoid(a*p + b)` directly on the raw probability (NOT on the
    logit, despite "Platt" usually meaning the latter). Using the wrong form
    here silently destroys v10's calibrated log_loss."""
    if not platt_dict or market_key not in platt_dict:
        return p
    params = platt_dict[market_key]
    if isinstance(params, dict):
        a = float(params.get("a", params.get("A", 1.0)))
        b = float(params.get("b", params.get("B", 0.0)))
    else:
        a, b = float(params[0]), float(params[1])
    z = np.clip(a * p + b, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _predict(bundle: dict, mfv_rows: pd.DataFrame) -> pd.DataFrame:
    """Run a bundle on a batch. Returns DF aligned on mfv_rows.index with
    columns: prob_home, prob_draw, prob_away, prob_over25, prob_btts (NaN if
    the bundle has no btts model)."""
    cols = bundle["feature_cols"]
    if not _is_mfv_schema(cols):
        # Legacy v9* — needs Kaggle CSV cache, not MFV. Caller should use the
        # DB-stored-predictions path for v9 instead.
        raise ValueError(
            f"Bundle {bundle['version']} uses Kaggle schema; cannot offline-eval "
            f"on MFV directly. Use --include-v9 to pull v9's actual DB-stored "
            f"predictions for the same match_ids instead."
        )

    X = _build_x_from_mfv(mfv_rows, cols, default_tier=1)
    platt = bundle.get("platt")

    out = pd.DataFrame(index=mfv_rows.index)
    proba_1x2 = bundle["result_1x2"].predict_proba(X)
    out["prob_home"] = _apply_platt(proba_1x2[:, 0], platt, "1x2_home")
    out["prob_draw"] = _apply_platt(proba_1x2[:, 1], platt, "1x2_draw")
    out["prob_away"] = _apply_platt(proba_1x2[:, 2], platt, "1x2_away")

    proba_ou = bundle["over_under"].predict_proba(X)
    out["prob_over25"] = _apply_platt(proba_ou[:, 1], platt, "over_25")

    if "btts" in bundle:
        proba_btts = bundle["btts"].predict_proba(X)
        out["prob_btts"] = _apply_platt(proba_btts[:, 1], platt, "btts_yes")
    else:
        out["prob_btts"] = np.nan
    return out


# ---------------------------------------------------------------------------
# Test-slice loading
# ---------------------------------------------------------------------------

def _load_test_slice(date_from: str, date_to: str) -> pd.DataFrame:
    """Load MFV rows joined to actual outcomes for the held-out window."""
    sql = """
        SELECT mfv.*,
               m.score_home AS m_score_home,
               m.score_away AS m_score_away,
               m.result     AS m_result,
               m.date       AS m_date,
               m.league_id  AS m_league_id
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s AND m.date <= %s
        ORDER BY m.date ASC
    """
    rows = execute_query(sql, (f"{date_from}T00:00:00", f"{date_to}T23:59:59"))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["score_home"] = pd.to_numeric(df["m_score_home"], errors="coerce")
    df["score_away"] = pd.to_numeric(df["m_score_away"], errors="coerce")
    return df


def _load_v9_db_predictions(match_ids: list[str]) -> pd.DataFrame:
    """v9 cannot run inference on MFV (Kaggle schema). Read its already-tagged
    `predictions` rows from the DB — that's what v9 actually predicted at the
    time, the cleanest baseline available."""
    if not match_ids:
        return pd.DataFrame()
    sql = """
        SELECT match_id, market, model_probability
        FROM predictions
        WHERE match_id = ANY(%s::uuid[])
          AND source = 'ensemble'
          AND model_version = 'v9a_202425'
    """
    rows = execute_query(sql, (list(match_ids),))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["model_probability"] = pd.to_numeric(df["model_probability"], errors="coerce")
    pivot = df.pivot_table(index="match_id", columns="market",
                           values="model_probability", aggfunc="first")
    pivot.columns = [str(c) for c in pivot.columns]
    return pivot


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _safe_log(p: float) -> float:
    return math.log(max(min(p, 1 - 1e-12), 1e-12))


def _market_metrics(probs: np.ndarray, truths: np.ndarray) -> dict:
    """Returns log_loss / brier / hit_rate / ECE for one market.
    `probs` is P(positive class), `truths` is 0/1."""
    mask = ~(np.isnan(probs) | np.isnan(truths))
    if mask.sum() == 0:
        return {"n": 0, "log_loss": None, "brier": None, "hit_rate": None, "ece": None}
    p = probs[mask].astype(float)
    t = truths[mask].astype(int)
    n = len(p)

    log_loss = -np.mean([t[i] * _safe_log(p[i]) + (1 - t[i]) * _safe_log(1 - p[i]) for i in range(n)])
    brier = np.mean((p - t) ** 2)
    hit = np.mean((p >= 0.5) == t.astype(bool))

    bins = np.linspace(0.0, 1.0, 11)
    bucket = np.digitize(p, bins[1:-1])
    ece = 0.0
    for b in range(10):
        idx = bucket == b
        if idx.any():
            ece += (idx.mean()) * abs(p[idx].mean() - t[idx].mean())
    return {"n": int(n), "log_loss": float(log_loss), "brier": float(brier),
            "hit_rate": float(hit), "ece": float(ece)}


def _truths_from_scores(test: pd.DataFrame) -> dict:
    sh = test["score_home"].astype(float)
    sa = test["score_away"].astype(float)
    return {
        "1x2_home": (sh > sa).astype(int).to_numpy(),
        "1x2_draw": (sh == sa).astype(int).to_numpy(),
        "1x2_away": (sh < sa).astype(int).to_numpy(),
        "over_25":  ((sh + sa) > 2.5).astype(int).to_numpy(),
        "btts_yes": ((sh > 0) & (sa > 0)).astype(int).to_numpy(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("versions", nargs="+", help="One or more model versions in data/models/soccer/")
    p.add_argument("--since", default=None, help="ISO date — start of held-out window. Default: today-30d.")
    p.add_argument("--until", default=None, help="ISO date — end of window. Default: yesterday.")
    p.add_argument("--include-v9", action="store_true",
                   help="Also include v9a_202425 baseline from DB-stored predictions.")
    p.add_argument("--save", default=None, help="Write markdown report to this path.")
    args = p.parse_args()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    since = args.since or (date.today() - timedelta(days=30)).isoformat()
    until = args.until or yesterday

    console.print(f"[cyan]Held-out window: {since} → {until}[/cyan]")
    test = _load_test_slice(since, until)
    if test.empty:
        console.print("[red]No finished matches in window — abort.[/red]")
        sys.exit(1)
    console.print(f"[cyan]Test slice: {len(test):,} matches[/cyan]")

    truths = _truths_from_scores(test)
    market_to_prob_col = {
        "1x2_home": "prob_home", "1x2_draw": "prob_draw", "1x2_away": "prob_away",
        "over_25": "prob_over25", "btts_yes": "prob_btts",
    }

    results: dict[str, dict[str, dict]] = {}  # version → market → metrics

    # MFV-schema bundles — run inference directly.
    for v in args.versions:
        try:
            bundle = _load_bundle(v)
        except FileNotFoundError as e:
            console.print(f"[red]Skip {v}: {e}[/red]")
            continue
        if not _is_mfv_schema(bundle["feature_cols"]):
            console.print(
                f"[yellow]Skip {v}: Kaggle-schema bundle, can't offline-eval on MFV. "
                f"Pass --include-v9 to use its DB-stored predictions instead.[/yellow]"
            )
            continue
        console.print(f"[cyan]Running {v} inference on {len(test):,} rows...[/cyan]")
        preds = _predict(bundle, test)
        per_market = {}
        for market, prob_col in market_to_prob_col.items():
            per_market[market] = _market_metrics(preds[prob_col].to_numpy(), truths[market])
        results[v] = per_market

    # v9 baseline — DB-stored predictions for the same matches.
    if args.include_v9:
        console.print("[cyan]Loading v9a_202425 baseline from DB-stored predictions...[/cyan]")
        v9 = _load_v9_db_predictions(test["match_id"].astype(str).tolist())
        if v9.empty:
            console.print("[yellow]v9a_202425 has zero predictions in window — baseline skipped.[/yellow]")
        else:
            test_indexed = test.set_index(test["match_id"].astype(str))
            aligned = test_indexed.join(v9, how="left")
            v9_truths = _truths_from_scores(aligned)
            v9_market_map = {
                "1x2_home": "1x2_home", "1x2_draw": "1x2_draw", "1x2_away": "1x2_away",
                "over_25": "over_under_25", "btts_yes": "btts_yes",
            }
            per_market = {}
            for our_market, db_col in v9_market_map.items():
                if db_col not in aligned.columns:
                    per_market[our_market] = {"n": 0, "log_loss": None, "brier": None,
                                              "hit_rate": None, "ece": None}
                    continue
                probs = pd.to_numeric(aligned[db_col], errors="coerce").to_numpy()
                per_market[our_market] = _market_metrics(probs, v9_truths[our_market])
            results["v9a_202425 (DB)"] = per_market

    # Render
    out_lines: list[str] = []
    out_lines.append(f"# Model comparison — held-out {since} → {until}")
    out_lines.append(f"\nTest slice: **{len(test):,} finished matches** with MFV row + actual score.\n")

    markets = ["1x2_home", "1x2_draw", "1x2_away", "over_25", "btts_yes"]
    for market in markets:
        table = Table(title=f"Market: {market}")
        table.add_column("Version", style="cyan")
        table.add_column("N", justify="right")
        table.add_column("log_loss ↓", justify="right", style="green")
        table.add_column("Brier ↓", justify="right")
        table.add_column("hit_rate", justify="right")
        table.add_column("ECE ↓", justify="right")

        rows = []
        for v, per_market in results.items():
            m = per_market.get(market) or {}
            ll = m.get("log_loss")
            if ll is None:
                table.add_row(v, "0", "—", "—", "—", "—")
                rows.append((v, 0, None, None, None, None))
                continue
            table.add_row(
                v, f"{m['n']:,}",
                f"{m['log_loss']:.4f}", f"{m['brier']:.4f}",
                f"{m['hit_rate']:.3f}", f"{m['ece']:.4f}",
            )
            rows.append((v, m["n"], m["log_loss"], m["brier"], m["hit_rate"], m["ece"]))
        console.print(table)

        out_lines.append(f"\n## {market}\n")
        out_lines.append("| Version | N | log_loss ↓ | Brier ↓ | hit_rate | ECE ↓ |")
        out_lines.append("|---------|--:|-----------:|--------:|---------:|------:|")
        for v, n, ll, br, hr, ec in rows:
            if ll is None:
                out_lines.append(f"| {v} | {n} | — | — | — | — |")
            else:
                out_lines.append(f"| {v} | {n:,} | {ll:.4f} | {br:.4f} | {hr:.3f} | {ec:.4f} |")

    caveat = (
        "\n**Caveat — leakage risk**: bundles trained on the same MFV may have already "
        "seen many matches in the test window. Numbers are upper-bound unless each "
        "candidate is retrained with an explicit `--cutoff <date>` arg first. "
        "v9a_202425 was trained on Kaggle (not MFV), so its baseline IS clean held-out — "
        "use it as the reference for whether retraining beats production.\n"
    )
    console.print(f"[dim]{caveat}[/dim]")
    out_lines.append(caveat)

    if args.save:
        out_path = Path(args.save)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(out_lines))
        console.print(f"[green]Saved markdown report → {out_path}[/green]")


if __name__ == "__main__":
    main()
