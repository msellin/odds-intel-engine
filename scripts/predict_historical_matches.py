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
    level. Kept as a fallback / single-match path; the bulk path used by
    main() is _load_all_team_form + _team_form_from_cache."""
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


def _load_all_team_form(team_ids: list[str]) -> dict[str, list[tuple]]:
    """Bulk-load every (date, gf, ga) finished-match record for each team
    into memory in a single SELECT. Replaces N per-match form lookups
    (~150ms × 2 × 10k matches = ~50 min on EU pooler) with one bulk query
    and dict-keyed in-memory filtering (~30s end-to-end).

    Returns: {team_id: [(date, gf, ga), ...]} sorted ascending by date.
    """
    if not team_ids:
        return {}
    rows = execute_query(
        """
        SELECT
            home_team_id::text AS h,
            away_team_id::text AS a,
            date,
            score_home,
            score_away
        FROM matches
        WHERE (home_team_id = ANY(%s::uuid[]) OR away_team_id = ANY(%s::uuid[]))
          AND status = 'finished'
          AND score_home IS NOT NULL
        ORDER BY date
        """,
        [team_ids, team_ids],
    )
    ids = set(team_ids)
    out: dict[str, list[tuple]] = {tid: [] for tid in team_ids}
    for r in rows:
        h, a = r["h"], r["a"]
        d = r["date"]
        sh = float(r["score_home"])
        sa = float(r["score_away"])
        # Same team can appear as home or away — record each from the team's POV.
        if h in ids:
            out[h].append((d, sh, sa))  # team is home → GF=score_home, GA=score_away
        if a in ids:
            out[a].append((d, sa, sh))  # team is away → flip
    return out


def _team_form_from_cache(team_id: str, before_dt, cache: dict[str, list[tuple]],
                          n: int = 10) -> tuple[list[float], list[float]]:
    """In-memory equivalent of _team_form_from_db using the bulk-loaded cache.
    `before_dt` must be a tz-aware datetime."""
    records = cache.get(team_id, [])
    if not records:
        return [], []
    # records sorted ascending by date — walk forward, keep last n strictly before
    relevant: list[tuple[float, float]] = []
    for d, gf, ga in records:
        if d >= before_dt:
            break
        relevant.append((gf, ga))
    relevant = relevant[-n:]
    return [r[0] for r in relevant], [r[1] for r in relevant]


def _compute_match_prediction(home_id: str, away_id: str, kickoff: str, tier: int,
                              form_cache: dict | None = None):
    """Replica of daily_pipeline_v2.compute_prediction but sources team form
    from our matches table (consistent names) instead of CSV (mismatched names).

    If `form_cache` is provided, looks up team history in memory (fast).
    Falls back to per-team DB queries if not provided (slow — original path).
    """
    if form_cache is not None:
        from datetime import datetime as _dt
        kickoff_dt = kickoff if hasattr(kickoff, "tzinfo") else _dt.fromisoformat(
            kickoff.replace("Z", "+00:00") if isinstance(kickoff, str) else kickoff
        )
        home_gf, home_ga = _team_form_from_cache(home_id, kickoff_dt, form_cache, 10)
        away_gf, away_ga = _team_form_from_cache(away_id, kickoff_dt, form_cache, 10)
    else:
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

    # Bulk-load team form once. ~30s vs ~50min for per-row DB lookups.
    team_ids = sorted({m["home_team_id"] for m in matches} |
                      {m["away_team_id"] for m in matches})
    print(f"\nBulk-loading team form for {len(team_ids):,} unique teams…")
    import time
    _t0 = time.time()
    form_cache = _load_all_team_form(team_ids)
    total_records = sum(len(v) for v in form_cache.values())
    print(f"  Loaded {total_records:,} (team, match) records in {time.time() - _t0:.1f}s")

    pred_rows: list[dict] = []
    skipped_no_data = 0
    predicted_count = 0
    inserted_total = 0

    from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeRemainingColumn, TextColumn

    print(f"\nPredicting (batch size {args.batch_size})…")
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TextColumn("inserted={task.fields[inserted]}"),
        TextColumn("•"),
        TextColumn("skipped={task.fields[skipped]}"),
        TextColumn("•"),
        TimeRemainingColumn(),
    ) as prog:
        task_id = prog.add_task("Matches", total=len(matches), inserted=0, skipped=0)
        for m in matches:
            kickoff = m["date"]  # datetime from psycopg2 (tz-aware)
            pred = _compute_match_prediction(
                home_id=m["home_team_id"],
                away_id=m["away_team_id"],
                kickoff=kickoff,
                tier=m["tier"],
                form_cache=form_cache,
            )
            if pred is None:
                skipped_no_data += 1
                prog.update(task_id, advance=1, skipped=skipped_no_data)
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
            prog.update(task_id, advance=1, inserted=inserted_total)

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
