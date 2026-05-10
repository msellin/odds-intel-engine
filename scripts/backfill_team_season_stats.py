"""
Stage 0d — team_season_stats backfill from match_stats aggregation.

The forward-only writer in `fetch_enrichment.py:231` calls
`store_team_season_stats` with the AF /teams/statistics payload. That endpoint
only goes forward — historical seasons we backfilled have no row in
`team_season_stats`, so MFV's per-team venue averages are NULL on backfilled
matches.

This script computes the same aggregate fields directly from `matches` joined
to `match_stats` and upserts one row per (team_api_id, league_api_id, season).

Idempotent: `store_team_season_stats` upserts on
(team_api_id, league_api_id, season, fetched_date), so re-running today
overwrites today's row.

Usage:
    python3 scripts/backfill_team_season_stats.py
    python3 scripts/backfill_team_season_stats.py --season 2024
    python3 scripts/backfill_team_season_stats.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from workers.api_clients.supabase_client import execute_query, store_team_season_stats

console = Console()


# Aggregate everything in SQL — way faster than pulling per-match rows into Python.
# Two halves: one as home, one as away, then merge per (team_api_id, league_api_id, season).
SQL_HOME = """
SELECT
    m.home_team_api_id          AS team_api_id,
    l.api_football_id           AS league_api_id,
    m.season                    AS season,
    COUNT(*)                    AS played,
    COUNT(*) FILTER (WHERE m.score_home > m.score_away)  AS wins,
    COUNT(*) FILTER (WHERE m.score_home = m.score_away)  AS draws,
    COUNT(*) FILTER (WHERE m.score_home < m.score_away)  AS losses,
    COALESCE(SUM(m.score_home), 0)  AS goals_for,
    COALESCE(SUM(m.score_away), 0)  AS goals_against,
    COUNT(*) FILTER (WHERE m.score_away = 0)  AS clean_sheets,
    COUNT(*) FILTER (WHERE m.score_home = 0)  AS failed_to_score
FROM matches m
JOIN leagues l ON l.id = m.league_id
WHERE m.status = 'finished'
  AND m.score_home IS NOT NULL
  AND m.score_away IS NOT NULL
  AND m.home_team_api_id IS NOT NULL
  AND l.api_football_id IS NOT NULL
  {season_clause}
GROUP BY m.home_team_api_id, l.api_football_id, m.season
"""

SQL_AWAY = """
SELECT
    m.away_team_api_id          AS team_api_id,
    l.api_football_id           AS league_api_id,
    m.season                    AS season,
    COUNT(*)                    AS played,
    COUNT(*) FILTER (WHERE m.score_away > m.score_home)  AS wins,
    COUNT(*) FILTER (WHERE m.score_away = m.score_home)  AS draws,
    COUNT(*) FILTER (WHERE m.score_away < m.score_home)  AS losses,
    COALESCE(SUM(m.score_away), 0)  AS goals_for,
    COALESCE(SUM(m.score_home), 0)  AS goals_against,
    COUNT(*) FILTER (WHERE m.score_home = 0)  AS clean_sheets,
    COUNT(*) FILTER (WHERE m.score_away = 0)  AS failed_to_score
FROM matches m
JOIN leagues l ON l.id = m.league_id
WHERE m.status = 'finished'
  AND m.score_home IS NOT NULL
  AND m.score_away IS NOT NULL
  AND m.away_team_api_id IS NOT NULL
  AND l.api_football_id IS NOT NULL
  {season_clause}
GROUP BY m.away_team_api_id, l.api_football_id, m.season
"""


def _key(row: dict) -> tuple:
    return (row["team_api_id"], row["league_api_id"], row["season"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=None,
                   help="Restrict to a single season (e.g. 2024). Default: all seasons.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute aggregates but don't write team_season_stats.")
    args = p.parse_args()

    season_clause = ""
    params: tuple = ()
    if args.season is not None:
        season_clause = "AND m.season = %s"
        params = (args.season,)

    console.print("[cyan]Aggregating home-side stats...[/cyan]")
    home_rows = execute_query(SQL_HOME.format(season_clause=season_clause), params)
    console.print(f"  {len(home_rows):,} (team, league, season) groups as home")

    console.print("[cyan]Aggregating away-side stats...[/cyan]")
    away_rows = execute_query(SQL_AWAY.format(season_clause=season_clause), params)
    console.print(f"  {len(away_rows):,} (team, league, season) groups as away")

    # Merge home + away per (team, league, season)
    merged: dict[tuple, dict] = {}
    for r in home_rows:
        merged[_key(r)] = {
            "played_home": int(r["played"]),
            "wins_home": int(r["wins"]),
            "draws_home": int(r["draws"]),
            "losses_home": int(r["losses"]),
            "goals_for_home": int(r["goals_for"]),
            "goals_against_home": int(r["goals_against"]),
            "clean_sheets_home": int(r["clean_sheets"]),
            "failed_to_score_home": int(r["failed_to_score"]),
        }
    for r in away_rows:
        k = _key(r)
        merged.setdefault(k, {})
        merged[k].update({
            "played_away": int(r["played"]),
            "wins_away": int(r["wins"]),
            "draws_away": int(r["draws"]),
            "losses_away": int(r["losses"]),
            "goals_for_away": int(r["goals_for"]),
            "goals_against_away": int(r["goals_against"]),
            "clean_sheets_away": int(r["clean_sheets"]),
            "failed_to_score_away": int(r["failed_to_score"]),
        })

    console.print(f"\n[cyan]Merged into {len(merged):,} (team, league, season) rows.[/cyan]")

    if args.dry_run:
        # Sample the first 5 to eyeball
        console.print("[yellow]Dry run — preview of first 5 merged rows:[/yellow]")
        for k in list(merged.keys())[:5]:
            console.print(f"  {k}: {merged[k]}")
        return

    written = 0
    failed = 0

    with Progress(TextColumn("[bold blue]TSS"), BarColumn(),
                  TextColumn("{task.completed}/{task.total} rows"),
                  TimeRemainingColumn(), console=console) as bar:
        task = bar.add_task("walk", total=len(merged))

        for (team_api_id, league_api_id, season), agg in merged.items():
            played_home = agg.get("played_home", 0)
            played_away = agg.get("played_away", 0)
            played_total = played_home + played_away

            wins_total = agg.get("wins_home", 0) + agg.get("wins_away", 0)
            draws_total = agg.get("draws_home", 0) + agg.get("draws_away", 0)
            losses_total = agg.get("losses_home", 0) + agg.get("losses_away", 0)

            gf_total = agg.get("goals_for_home", 0) + agg.get("goals_for_away", 0)
            ga_total = agg.get("goals_against_home", 0) + agg.get("goals_against_away", 0)

            cs_total = agg.get("clean_sheets_home", 0) + agg.get("clean_sheets_away", 0)
            fts_total = agg.get("failed_to_score_home", 0) + agg.get("failed_to_score_away", 0)

            parsed = {
                "played_total": played_total,
                "played_home": played_home,
                "played_away": played_away,
                "wins_total": wins_total,
                "wins_home": agg.get("wins_home", 0),
                "wins_away": agg.get("wins_away", 0),
                "draws_total": draws_total,
                "draws_home": agg.get("draws_home", 0),
                "draws_away": agg.get("draws_away", 0),
                "losses_total": losses_total,
                "losses_home": agg.get("losses_home", 0),
                "losses_away": agg.get("losses_away", 0),
                "goals_for_total": gf_total,
                "goals_for_home": agg.get("goals_for_home", 0),
                "goals_for_away": agg.get("goals_for_away", 0),
                "goals_against_total": ga_total,
                "goals_against_home": agg.get("goals_against_home", 0),
                "goals_against_away": agg.get("goals_against_away", 0),
                "goals_for_avg": round(gf_total / played_total, 3) if played_total else None,
                "goals_against_avg": round(ga_total / played_total, 3) if played_total else None,
                "clean_sheets_total": cs_total,
                "clean_sheets_home": agg.get("clean_sheets_home", 0),
                "clean_sheets_away": agg.get("clean_sheets_away", 0),
                "failed_to_score_total": fts_total,
                "failed_to_score_home": agg.get("failed_to_score_home", 0),
                "failed_to_score_away": agg.get("failed_to_score_away", 0),
                "clean_sheet_pct": round(cs_total / played_total, 3) if played_total else None,
                "failed_to_score_pct": round(fts_total / played_total, 3) if played_total else None,
            }

            try:
                store_team_season_stats(team_api_id, league_api_id, season, parsed)
                written += 1
            except Exception as e:
                failed += 1
                if failed <= 5:
                    console.print(f"  [red]upsert failed for "
                                  f"({team_api_id}, {league_api_id}, {season}): {e}[/red]")
            bar.advance(task)

    console.print(
        f"\n[bold green]✓ team_season_stats backfill complete — "
        f"{written:,} upserted, {failed:,} failed.[/bold green]"
    )


if __name__ == "__main__":
    main()
