"""B-ML3-VALIDATE-ACTIVATION — meta-model real-world validation (2026-05-25).

The activation decision for META_B_ML3_ENABLED=true is data-driven, not
theoretical-AUC-driven. This script answers the question:

  "Does the meta-model's meta_clv_score actually predict which bets beat
   closing line on real settled data?"

Methodology:
  1. Pull every settled simulated_bet since --since (default 2026-05-25,
     when META_B_ML3_ACTIVE wiring shipped).
  2. For each bet, look up its match_id's MFV row.
  3. For EACH meta bundle on disk, re-score that bet with that bundle.
     (We re-score even if simulated_bets.meta_clv_score is populated, so
     we can compare all bundles head-to-head on the same cohort.)
  4. Bin bets by score quintile per bundle.
  5. For each (bundle, bin), compute: n bets, hit rate, mean pseudo_clv,
     CLV-beat-rate (pseudo_clv > 0), actual ROI.
  6. Verdict per bundle:
       PASS  if top quintile CLV-beat-rate ≥ bottom quintile + 5pp
       MARGINAL if separation 2-5pp
       FAIL  if < 2pp (= noise)

Output guides the META_B_ML3_VERSION + ENABLED flip on the VPS.

Run pre-flight when you want to check current data:
    python3 scripts/validate_meta_b_ml3.py --since 2026-05-25

Recommended cadence:
    First run: ~2026-06-10 (after 14d Phase 3.5 + 3d analysis buffer)
    Re-run weekly thereafter while META_B_ML3_ENABLED=false
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import joblib
import json
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "meta"


def _load_bundle(bundle_dir: Path) -> dict | None:
    """Load a meta bundle. Returns None if loadable."""
    try:
        mt_path = bundle_dir / "model_type.txt"
        model_type = mt_path.read_text().strip() if mt_path.exists() else "logistic"
        return {
            "name": bundle_dir.name,
            "model": joblib.load(bundle_dir / "b_ml3.pkl"),
            "scaler": joblib.load(bundle_dir / "scaler.pkl") if model_type == "logistic" else None,
            "feature_cols": joblib.load(bundle_dir / "feature_cols.pkl"),
            "model_type": model_type,
            "threshold": json.loads((bundle_dir / "threshold.json").read_text()).get("chosen_threshold", 0.5)
                         if (bundle_dir / "threshold.json").exists() else 0.5,
        }
    except Exception as e:
        console.print(f"[yellow]Could not load {bundle_dir.name}: {e}[/yellow]")
        return None


def _score_one(bundle: dict, X: pd.DataFrame) -> np.ndarray:
    """Score a feature matrix with a bundle. Handles logistic + xgboost.

    META-EVAL-SEGFAULT-FIX 2026-07-18: batch predict_proba segfaults on
    sklearn/xgboost bundles under Python 3.14 (native crash, uncatchable).
    Production `meta_b_ml3.score_bet` uses row-at-a-time inference and works
    fine — so we mirror that here. Slower but doesn't crash.
    """
    # Align to bundle's expected feature schema (defensive — schemas drift across versions)
    aligned = pd.DataFrame(0.0, index=X.index, columns=bundle["feature_cols"])
    for c in bundle["feature_cols"]:
        if c in X.columns:
            aligned[c] = X[c].values
    # Logistic bundles (StandardScaler + LogisticRegression) reject NaN. MFV-derived
    # features like form_momentum / pinnacle_line_move can legitimately be NaN when
    # upstream data is thin; training imputes to 0, so we mirror that here.
    aligned = aligned.fillna(0.0)
    X_eval = aligned.values if bundle["scaler"] is None else bundle["scaler"].transform(aligned)
    # Row-at-a-time predict_proba to avoid the batch-predict segfault
    scores = np.zeros(len(X_eval))
    model = bundle["model"]
    for i in range(len(X_eval)):
        scores[i] = float(model.predict_proba(X_eval[i:i+1])[0, 1])
    return scores


def _load_settled_bets_with_features(since: str) -> pd.DataFrame:
    """Pull settled bets joined to their MFV row + match data so each bundle can score them."""
    rows = execute_query("""
        SELECT
          sb.id AS bet_id, sb.bot_id, sb.market, sb.selection, sb.result,
          sb.stake, sb.pnl, sb.odds_at_pick,
          sb.calibrated_prob, sb.model_probability,
          sb.meta_clv_score AS stored_score,
          sb.pick_time, sb.clv, sb.clv_pinnacle,
          m.id AS match_id, m.score_home, m.score_away,
          l.tier AS league_tier,
          mfv.*
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
        WHERE sb.pick_time >= %s
          AND sb.result IN ('won', 'lost')
          AND mfv.opening_implied_home IS NOT NULL
        ORDER BY sb.pick_time
    """, (since,))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# META-EVAL-PIPELINE-FIX 2026-07-18: filter non-1X2 bets like training does.
# The meta model is architecturally 1X2-only — `scripts/train_b_ml3.py`
# explicitly purges non-1X2 rows (see B-ML3-BETS-MODE-1X2-FILTER, 2026-06-21).
# The previous version of this function coerced OU/BTTS/AH selections to
# "home" and grabbed 1X2 features for a non-1X2 bet, producing garbage
# scores. Now we mirror training: SEL_MAP → skip anything not 1X2.
_SEL_MAP = {
    "1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away",
    "home": "home", "draw": "draw", "away": "away",
}


def _build_feature_row_for_bet(row: pd.Series) -> dict:
    """Build the feature dict for ONE 1X2 bet. Mirrors both training
    (`scripts/train_b_ml3.py::_build_feature_matrix`) and inference
    (`workers/model/meta_b_ml3.py::_build_feature_row`).

    Returns None for non-1X2 bets (OU/BTTS/AH/DC) — those markets aren't
    in the meta model's training corpus so scoring them is data poisoning.
    """
    raw_sel = (row.get("selection") or "").strip().lower()
    market = (row.get("market") or "").strip().lower()

    # Only accept 1X2 selections. Explicit market check catches "home"
    # values that came from AH markets (ah_home_+0.5 etc.).
    if not (market.startswith("1x2") or market == ""):
        return None
    sel_norm = _SEL_MAP.get(raw_sel)
    if sel_norm is None:
        return None

    ens = row.get(f"ensemble_prob_{sel_norm}")
    opening = row.get(f"opening_implied_{sel_norm}")
    if ens is None or opening is None:
        return None
    ens, opening = float(ens), float(opening)

    feat = {
        # Selection-aware (pivoted per-side)
        "edge_proxy": ens - opening,
        "ensemble_prob": ens,
        "opening_implied": opening,
        "pinnacle_line_move": row.get(f"pinnacle_line_move_{sel_norm}_at_t6h"),
        "sharp_consensus": row.get(f"sharp_consensus_{sel_norm}_at_t6h"),
        "odds_volatility": row.get(f"odds_volatility_{sel_norm}_at_t6h"),
        # Match-level v2.1
        "bookmaker_disagreement": row.get("bookmaker_disagreement"),
        "elo_diff": row.get("elo_diff"),
        "form_ppg_home": row.get("form_ppg_home"),
        "form_ppg_away": row.get("form_ppg_away"),
        "lineup_confirmed": row.get("lineup_confirmed"),
        "rest_days_home": row.get("rest_days_home"),
        "rest_days_away": row.get("rest_days_away"),
        "fixture_importance": row.get("fixture_importance"),
        "league_position_home": row.get("league_position_home"),
        "odds_drift_home_at_t6h": row.get("odds_drift_home_at_t6h"),
        "steam_move_at_t6h": row.get("steam_move_at_t6h"),
        "form_momentum_home": row.get("form_momentum_home"),
        "form_momentum_away": row.get("form_momentum_away"),
        # Extended v3 signals (bets-mode bundle v_20260607+)
        "pinnacle_ah_line_at_t6h": row.get("pinnacle_ah_line_at_t6h"),
        "pinnacle_ah_line_move": row.get("pinnacle_ah_line_move"),
        "league_draw_rate_ytd": row.get("league_draw_rate_ytd"),
        "season_progress": row.get("season_progress"),
        "line_velocity": row.get("line_velocity"),
        "xg_overperf_home": row.get("xg_overperf_home"),
        "xg_overperf_away": row.get("xg_overperf_away"),
        "league_clv_efficiency": row.get("league_clv_efficiency"),
        "injury_severity_score_home": row.get("injury_severity_score_home"),
        "injury_severity_score_away": row.get("injury_severity_score_away"),
        "team_avg_player_rating_home": row.get("team_avg_player_rating_home"),
        "team_avg_player_rating_away": row.get("team_avg_player_rating_away"),
        # Meta / context
        "time_to_kickoff_h": 24.0,  # approximation — not stored per-bet
        "league_tier": int(row.get("league_tier") or 4),
        # Selection one-hot (drop_first = "home" → only draw + away)
        "selection_draw": 1 if sel_norm == "draw" else 0,
        "selection_away": 1 if sel_norm == "away" else 0,
    }
    # Missing indicators (mirror training THIN_FEATURES_FOR_INDICATORS)
    thin = ["bookmaker_disagreement", "fixture_importance", "league_position_home",
            "rest_days_home", "rest_days_away", "pinnacle_line_move",
            "sharp_consensus", "odds_volatility", "odds_drift_home_at_t6h",
            "pinnacle_ah_line_at_t6h", "pinnacle_ah_line_move"]
    for c in thin:
        feat[f"{c}_missing"] = 1 if feat.get(c) is None else 0
    # Cast + numeric coerce, NaN → 0
    for k, v in list(feat.items()):
        if isinstance(v, bool):
            feat[k] = int(v)
        elif v is None:
            feat[k] = 0.0
        else:
            try:
                feat[k] = float(v)
            except (TypeError, ValueError):
                feat[k] = 0.0
    return feat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-25",
                    help="Pull bets settled on/after this date (default: when META_B_ML3_ACTIVE shipped)")
    ap.add_argument("--n-bins", type=int, default=5, help="Quintile (5) or quartile (4) bins")
    # META-EVAL-SEGFAULT-FIX 2026-07-18: v_20260525_* bundles were pickled
    # with sklearn 1.8.0; the pickle loads with an InconsistentVersionWarning
    # under 1.9.0 but predict_proba then segfaults natively (uncatchable).
    # --bundles lets caller pick specific ones; default excludes the crashing
    # historical candidates. Post-vacation follow-up: re-pickle old bundles
    # or run each in a subprocess for full isolation.
    ap.add_argument("--bundles", default="",
                    help="Comma-separated bundle names to score. Empty = auto-skip known-crashing v_20260525_*.")
    args = ap.parse_args()

    console.print(f"\n[bold]B-ML3 validation — pulling settled bets since {args.since}[/bold]")
    df = _load_settled_bets_with_features(args.since)
    console.print(f"  {len(df):,} settled bets joined to MFV (only bets with opening_implied_home)")
    if len(df) < 50:
        console.print(f"[yellow]Too few settled bets ({len(df)}) for meaningful validation. "
                      f"Wait for more data — target ≥200 bets / ≥30 per bundle bin.[/yellow]")
        if len(df) == 0:
            return

    # Build features per bet
    feat_rows = []
    keep_idx = []
    _skip_reasons = {"non_1x2_market": 0, "unknown_selection": 0,
                     "missing_ensemble_or_opening": 0, "other": 0, "kept": 0}
    for i, row in df.iterrows():
        # Diagnostic: track why bets get skipped
        market = (row.get("market") or "").strip().lower()
        raw_sel = (row.get("selection") or "").strip().lower()
        if not (market.startswith("1x2") or market == ""):
            _skip_reasons["non_1x2_market"] += 1
            continue
        if _SEL_MAP.get(raw_sel) is None:
            _skip_reasons["unknown_selection"] += 1
            continue
        sel_norm = _SEL_MAP[raw_sel]
        if row.get(f"ensemble_prob_{sel_norm}") is None or row.get(f"opening_implied_{sel_norm}") is None:
            _skip_reasons["missing_ensemble_or_opening"] += 1
            continue
        f = _build_feature_row_for_bet(row)
        if f is not None:
            feat_rows.append(f)
            keep_idx.append(i)
            _skip_reasons["kept"] += 1
        else:
            _skip_reasons["other"] += 1
    console.print(f"  feature-build skip stats: {_skip_reasons}")
    sys.stdout.flush()
    if not feat_rows:
        console.print("[red]No bets had usable features. Aborting.[/red]")
        return
    # META-EVAL-SEGFAULT-FIX 2026-07-18: `df.loc[keep_idx]` segfaults on
    # pandas 2.x + psycopg2 Decimal-typed columns from simulated_bets.
    # Native crash, no traceback. Rebuild the DataFrame from the raw dict
    # rows we care about — avoids pandas' internal .loc block path.
    keep_rows = [dict(df.iloc[i]) for i in keep_idx]
    df = pd.DataFrame(keep_rows).reset_index(drop=True)
    X = pd.DataFrame(feat_rows).reset_index(drop=True)

    # KNOWN LIMITATION (META-EVAL-SCORING-SEGFAULT 2026-07-18):
    # `_score_one` still segfaults natively on ALL loaded bundles under
    # Python 3.14 + sklearn 1.9 + xgboost. Confirmed for logistic + xgboost
    # bundles, both batch and row-at-a-time predict_proba. Production
    # inference (`workers/model/meta_b_ml3.score_bet`, single-row) works
    # fine — so the meta model is still live-scoring bets normally. Only
    # this offline validation script hits the crash. Root cause not
    # identified in the 2026-07-18 investigation window. Filed for
    # post-vacation work (see PRIORITY_QUEUE.md). Until then this script
    # can build features + load bundles but can't produce comparison
    # numbers. Structural improvements below (1X2 filter, --bundles arg,
    # per-bundle print) are still useful once the scoring crash is fixed.

    # Outcomes
    df["won"] = (df["result"] == "won").astype(int)
    # CLV-beat: prefer pinnacle_clv if present, else simulated_bets.clv. psycopg2
    # returns Decimal for numeric columns; pandas .mean() on a Decimal column
    # raises "unsupported operand type(s) for +: 'Decimal' and 'float'", so we
    # coerce to float here once.
    df["clv_used"] = pd.to_numeric(
        df["clv_pinnacle"].fillna(df["clv"]), errors="coerce"
    ).astype(float)
    df["clv_beat"] = (df["clv_used"].fillna(0) > 0).astype(int)
    df["roi_per_bet"] = pd.to_numeric(df["pnl"], errors="coerce").astype(float) / \
                       pd.to_numeric(df["stake"], errors="coerce").astype(float)

    # Load every bundle. META-EVAL-SEGFAULT-DIAG 2026-07-18: print+flush
    # BEFORE each load so a native crash (uncatchable) still names the offender.
    # Skip list: bundles that segfault under current sklearn (see --bundles arg).
    _default_skip = {"v_20260525_v21", "v_20260525_v22", "v_20260525_v23_xgb"}
    explicit = {b.strip() for b in args.bundles.split(",") if b.strip()}
    bundles = []
    for d in sorted([d for d in MODELS_DIR.iterdir() if d.is_dir() and (d / "b_ml3.pkl").exists()]):
        if explicit and d.name not in explicit:
            console.print(f"[dim]skipping {d.name} (not in --bundles filter)[/dim]")
            continue
        if not explicit and d.name in _default_skip:
            console.print(f"[yellow]skipping {d.name} (known sklearn-1.8→1.9 predict_proba segfault)[/yellow]")
            continue
        console.print(f"[dim]loading bundle {d.name}...[/dim]")
        sys.stdout.flush()
        b = _load_bundle(d)
        if b:
            bundles.append(b)
            console.print(f"[dim]  ok — {b.get('model_type', '?')} model_type[/dim]")
        else:
            console.print(f"[dim]  skipped (no bundle returned)[/dim]")
        sys.stdout.flush()
    console.print(f"\n[bold]Loaded {len(bundles)} bundles[/bold]: {[b['name'] for b in bundles]}\n")

    # Per-bundle quintile analysis. Print+flush BEFORE _score_one so a
    # native crash still names the offender in the log (Python try/except
    # can't catch SIGSEGV — see KNOWN LIMITATION comment above).
    summary_rows = []
    for b in bundles:
        console.print(f"[dim]scoring with {b['name']}...[/dim]")
        sys.stdout.flush()
        try:
            scores = _score_one(b, X)
        except Exception as e:
            console.print(f"[yellow]Could not score with {b['name']}: {e}[/yellow]")
            continue
        df["score"] = scores
        # Quantile bins — handle low-cardinality
        try:
            df["bin"] = pd.qcut(df["score"], q=args.n_bins, labels=False, duplicates="drop")
        except ValueError:
            df["bin"] = pd.cut(df["score"], bins=args.n_bins, labels=False)

        t = Table(title=f"{b['name']} ({b['model_type']}) — score-binned performance")
        for col in ("bin", "n", "score_avg", "hit%", "clv_beat%", "ROI%", "clv_used_avg"):
            t.add_column(col)
        bin_stats = []
        for bin_id, sub in df.groupby("bin", sort=True):
            n = len(sub)
            hit = sub["won"].mean() * 100
            beat = sub["clv_beat"].mean() * 100
            roi = sub["roi_per_bet"].sum() / n * 100 if n else 0
            score_avg = sub["score"].mean()
            clv_avg = sub["clv_used"].mean() if sub["clv_used"].notna().any() else 0
            bin_stats.append((bin_id, n, hit, beat, roi, score_avg, clv_avg))
            t.add_row(str(int(bin_id) if pd.notna(bin_id) else "?"),
                      str(n), f"{score_avg:.3f}", f"{hit:.1f}", f"{beat:.1f}",
                      f"{roi:+.1f}", f"{clv_avg:.3f}" if isinstance(clv_avg, (int, float)) else "—")
        console.print(t)

        # Verdict for this bundle
        if len(bin_stats) >= 2:
            top = bin_stats[-1]
            bot = bin_stats[0]
            beat_delta_pp = top[3] - bot[3]
            if beat_delta_pp >= 5:
                verdict = "PASS"
                advice = f"Top-bin CLV-beat rate exceeds bottom by {beat_delta_pp:.1f}pp — ACTIVATE candidate."
            elif beat_delta_pp >= 2:
                verdict = "MARGINAL"
                advice = f"Separation only {beat_delta_pp:.1f}pp — wait for more data."
            else:
                verdict = "FAIL"
                advice = f"Top-bin barely outperforms bottom ({beat_delta_pp:+.1f}pp) — meta is noise here."
            summary_rows.append({
                "bundle": b["name"], "model_type": b["model_type"],
                "top_clv_beat%": top[3], "bot_clv_beat%": bot[3],
                "delta_pp": beat_delta_pp, "verdict": verdict, "advice": advice,
            })
            console.print(f"  [bold]{verdict}[/bold]: {advice}\n")

    # Final summary
    if summary_rows:
        t = Table(title="Activation verdict per bundle")
        for col in ("bundle", "type", "top CLV-beat%", "bottom %", "Δpp", "verdict"):
            t.add_column(col)
        for r in summary_rows:
            t.add_row(r["bundle"], r["model_type"],
                      f"{r['top_clv_beat%']:.1f}", f"{r['bot_clv_beat%']:.1f}",
                      f"{r['delta_pp']:+.1f}", r["verdict"])
        console.print(t)

        passers = [r for r in summary_rows if r["verdict"] == "PASS"]
        if passers:
            best = max(passers, key=lambda r: r["delta_pp"])
            console.print(f"\n[bold green]Recommend: META_B_ML3_VERSION={best['bundle']} "
                          f"+ META_B_ML3_ENABLED=true[/bold green]")
        elif any(r["verdict"] == "MARGINAL" for r in summary_rows):
            console.print("\n[yellow]No clear PASS bundle. Keep META_B_ML3_ENABLED=false and wait for more data.[/yellow]")
        else:
            console.print("\n[red]All bundles FAIL the 5pp gate. Don't activate — retrain v3 with more data.[/red]")


if __name__ == "__main__":
    main()
