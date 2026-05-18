"""
BACKTEST-HISTORICAL-PREDICT-FORWARD — generate Poisson predictions for the
historical matches we just ingested football-data closing odds for.

Uses our `matches` table directly for team form lookup (last 10 matches per
team prior to the focal kickoff), bypassing the targets_v9 CSV's
football-data-style name mismatches ("Manchester United" in DB vs
"Man United" in CSV). All team-form data comes from the same matches table
that has the matches we're predicting, with strict date < kickoff filter
to keep things lookahead-free at the per-match level.

After BACKTEST-HISTORICAL-CSV-INGEST shipped 113K odds rows across 12,693
matches, the bottleneck on `backtest_pre_match_bots.py` shifted from
odds-coverage to predictions-coverage: only 3,999 of those matches have
stored predictions. This script fills the gap so the backtest can use the
expanded odds pool.

Method: re-run the same Poisson + Dixon-Coles prediction stack the live
pipeline uses (`workers.jobs.daily_pipeline_v2.compute_prediction`),
loading historical team form from the same targets_poisson_history.csv +
targets_global.csv files.

Lookahead caveat: the targets CSVs contain ALL match outcomes including
the matches we're predicting. Pure Poisson uses team form (recent goals
scored/conceded), which is a per-match derived feature and only mildly
biased by training-set overlap. For a perfectly clean walk-forward backtest
we'd retrain the model at each historical date and predict forward — that's
a multi-day project. This script is "directional, not pristine."

Storage: writes one row per (match_id, market) into `predictions` with
source='ensemble' so backtest_pre_match_bots.py picks them up via its
existing `WHERE source = 'ensemble'` filter. The source label is slightly
misleading (we're using Poisson only, not the XGBoost ensemble), but the
backtest doesn't care which source name it is — it just reads the
probability per market.

Run:
  python scripts/predict_historical_matches.py --dry-run
  python scripts/predict_historical_matches.py --limit 500     # smoke run
  python scripts/predict_historical_matches.py                 # full pass
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from workers.api_clients.db import execute_query  # noqa: E402
from workers.api_clients.supabase_client import bulk_store_predictions  # noqa: E402
from workers.jobs.daily_pipeline_v2 import _poisson_probs, _load_dc_rho_cache  # noqa: E402

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

# Predictions schema: market keys the backtester reads (see _load_predictions
# in backtest_pre_match_bots.py:128)
MARKETS_TO_STORE = [
    ("home_prob", "1x2_home"),
    ("draw_prob", "1x2_draw"),
    ("away_prob", "1x2_away"),
    ("over_25_prob", "over25"),
    ("under_25_prob", "under25"),
    ("over_15_prob", "over15"),
    ("under_15_prob", "under15"),
    ("over_35_prob", "over35"),
    ("under_35_prob", "under35"),
    ("btts_yes_prob", "btts_yes"),
    ("btts_no_prob", "btts_no"),
]


def _find_matches_needing_predictions(limit: int | None):
    """Finished matches that have odds_snapshots but no stored ensemble prediction."""
    sql = """
        SELECT
            m.id::text                    AS match_id,
            m.date,
            m.home_team_id::text          AS home_team_id,
            m.away_team_id::text          AS away_team_id,
            ht.name                       AS home_team,
            ta.name                       AS away_team,
            l.tier
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.status = 'finished'
          AND m.score_home IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM odds_snapshots os
            WHERE os.match_id = m.id AND os.market = '1x2'
          )
          AND NOT EXISTS (
            SELECT 1 FROM predictions p
            WHERE p.match_id = m.id AND p.source = 'ensemble'
          )
        ORDER BY m.date DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = execute_query(sql, [])
    return rows or []


def _team_form_from_db(team_id: str, before_date: str, n: int = 10):
    """Return (goals_for[], goals_against[]) from the team's last N finished
    matches strictly before `before_date`. Lookahead-free at the per-match
    level."""
    rows = execute_query(
        """
        SELECT
            CASE WHEN m.home_team_id = %s::uuid THEN m.score_home ELSE m.score_away END AS gf,
            CASE WHEN m.home_team_id = %s::uuid THEN m.score_away ELSE m.score_home END AS ga
        FROM matches m
        WHERE (m.home_team_id = %s::uuid OR m.away_team_id = %s::uuid)
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL
          AND m.date < %s::timestamptz
        ORDER BY m.date DESC LIMIT %s
        """,
        [team_id, team_id, team_id, team_id, before_date, n],
    )
    gf = [float(r["gf"]) for r in rows if r["gf"] is not None]
    ga = [float(r["ga"]) for r in rows if r["ga"] is not None]
    return gf, ga


def _compute_match_prediction(home_id: str, away_id: str, kickoff: str, tier: int):
    """Replica of daily_pipeline_v2.compute_prediction but sources team form
    from our matches table (consistent names) instead of CSV (mismatched names)."""
    home_gf, home_ga = _team_form_from_db(home_id, kickoff, 10)
    away_gf, away_ga = _team_form_from_db(away_id, kickoff, 10)
    if len(home_gf) < 3 or len(away_gf) < 3:
        return None

    exp_h = max(0.3, float(np.mean(home_gf))) * 1.08  # slight home advantage
    exp_a = max(0.3, float(np.mean(away_gf))) * 0.92
    exp_h = (exp_h + float(np.mean(away_ga))) / 2
    exp_a = (exp_a + float(np.mean(home_ga))) / 2

    league_tier = int(tier or 1)
    tier_rho = _load_dc_rho_cache().get(league_tier)
    result = _poisson_probs(exp_h, exp_a, rho=tier_rho)
    result.update({"exp_home": exp_h, "exp_away": exp_a, "data_tier": "A"})
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="Limit number of matches (smoke run)")
    p.add_argument("--dry-run", action="store_true", help="Compute but don't insert")
    p.add_argument("--batch-size", type=int, default=500, help="Bulk insert batch size")
    args = p.parse_args()

    print("Finding matches needing predictions…")
    matches = _find_matches_needing_predictions(args.limit)
    print(f"  {len(matches):,} matches qualify (have odds, no ensemble prediction)")

    if not matches:
        print("Nothing to do.")
        return

    pred_rows: list[dict] = []
    skipped_no_data = 0
    predicted_count = 0
    batch_counter = 0
    inserted_total = 0

    print(f"\nPredicting (batch size {args.batch_size})…")
    for i, m in enumerate(matches, 1):
        kickoff = m["date"].isoformat() if hasattr(m["date"], "isoformat") else str(m["date"])
        pred = _compute_match_prediction(
            home_id=m["home_team_id"],
            away_id=m["away_team_id"],
            kickoff=kickoff,
            tier=m["tier"],
        )
        if pred is None:
            skipped_no_data += 1
            continue
        for prob_key, market_key in MARKETS_TO_STORE:
            prob = pred.get(prob_key)
            if prob is None:
                continue
            pred_rows.append({
                "match_id": m["match_id"],
                "market":   market_key,
                "source":   "ensemble",
                "model_prob": float(prob),
                "confidence": 0.5,
                "reasoning": f"Historical backfill — Poisson+DC (data_tier={pred.get('data_tier', '?')})",
                "model_version": "poisson_backfill",
            })
        predicted_count += 1

        # Flush in batches to keep memory bounded
        if not args.dry_run and len(pred_rows) >= args.batch_size:
            n = bulk_store_predictions(pred_rows)
            inserted_total += n
            pred_rows = []
            batch_counter += 1
            if batch_counter % 4 == 0:
                print(f"  …{i:,}/{len(matches):,} matches scanned, {inserted_total:,} prediction rows inserted")

    # Final flush
    if not args.dry_run and pred_rows:
        n = bulk_store_predictions(pred_rows)
        inserted_total += n

    print()
    print("=" * 70)
    print(f"Matches scanned:           {len(matches):,}")
    print(f"  with prediction possible: {predicted_count:,}")
    print(f"  skipped (no team form):   {skipped_no_data:,}")
    if args.dry_run:
        print(f"Prediction rows that WOULD be inserted: {len(pred_rows):,}")
    else:
        print(f"Prediction rows inserted:  {inserted_total:,}")


if __name__ == "__main__":
    main()
