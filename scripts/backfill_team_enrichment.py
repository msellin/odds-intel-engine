"""Backfill team enrichment for teams not yet covered by coaches / transfers / team_stats.

Queries matches for all unique AF team IDs, diffs against the existing tables,
and fetches the missing entries in configurable batches.

Usage:
  python scripts/backfill_team_enrichment.py --components coaches
  python scripts/backfill_team_enrichment.py --components transfers
  python scripts/backfill_team_enrichment.py --components team_stats
  python scripts/backfill_team_enrichment.py --components coaches,transfers --batch-size 30
  python scripts/backfill_team_enrichment.py --components coaches --batch-size 25 --offset 50
  python scripts/backfill_team_enrichment.py --dry-run
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import (
    get_coaches, parse_coaches,
    get_transfers, parse_transfers,
    get_team_statistics, parse_team_statistics,
    get_remaining_requests,
)
from workers.api_clients.supabase_client import (
    store_team_coaches,
    store_team_transfers,
    store_team_season_stats,
)
from workers.api_clients.db import execute_query, execute_write

console = Console()

RATE_DELAY = 0.15   # 150ms between AF calls (~6.7 req/sec)
MIN_BUDGET  = 2_000  # abort if fewer requests remain


# ── Helpers ──────────────────────────────────────────────────────────────────

def _all_team_af_ids() -> list[int]:
    """All unique AF team IDs seen across every match."""
    rows = execute_query(
        """
        SELECT DISTINCT af_id FROM (
            SELECT home_team_api_id AS af_id FROM matches WHERE home_team_api_id IS NOT NULL
            UNION
            SELECT away_team_api_id AS af_id FROM matches WHERE away_team_api_id IS NOT NULL
        ) t
        ORDER BY af_id
        """
    )
    return [r["af_id"] for r in rows]


def _missing_coaches(all_ids: list[int]) -> list[int]:
    """Teams with no entry in team_coaches at all."""
    if not all_ids:
        return []
    rows = execute_query(
        "SELECT DISTINCT team_af_id FROM team_coaches WHERE team_af_id = ANY(%s)",
        [all_ids],
    )
    covered = {r["team_af_id"] for r in rows}
    return [i for i in all_ids if i not in covered]


def _missing_transfers(all_ids: list[int]) -> list[int]:
    """Teams not in team_transfer_cache (never fetched)."""
    if not all_ids:
        return []
    rows = execute_query(
        "SELECT team_api_id FROM team_transfer_cache WHERE team_api_id = ANY(%s)",
        [all_ids],
    )
    covered = {r["team_api_id"] for r in rows}
    return [i for i in all_ids if i not in covered]


def _missing_team_stats() -> list[tuple[int, int, int]]:
    """(team_api_id, league_api_id, season) combos for Tier 1 leagues not yet in team_season_stats."""
    # All distinct team+league+season combos from Tier 1 matches
    combos = execute_query(
        """
        SELECT DISTINCT
            m.home_team_api_id  AS team_api_id,
            l.api_football_id   AS league_api_id,
            CASE WHEN EXTRACT(MONTH FROM m.date) >= 7
                 THEN EXTRACT(YEAR FROM m.date)
                 ELSE EXTRACT(YEAR FROM m.date) - 1
            END::int AS season
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE m.home_team_api_id IS NOT NULL
          AND l.tier = 1
          AND l.api_football_id IS NOT NULL

        UNION

        SELECT DISTINCT
            m.away_team_api_id,
            l.api_football_id,
            CASE WHEN EXTRACT(MONTH FROM m.date) >= 7
                 THEN EXTRACT(YEAR FROM m.date)
                 ELSE EXTRACT(YEAR FROM m.date) - 1
            END::int
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE m.away_team_api_id IS NOT NULL
          AND l.tier = 1
          AND l.api_football_id IS NOT NULL

        ORDER BY 1, 2, 3
        """
    )

    all_combos = [(r["team_api_id"], r["league_api_id"], r["season"]) for r in combos]

    # Exclude combos already in team_season_stats
    existing = execute_query(
        "SELECT DISTINCT team_api_id, league_api_id, season FROM team_season_stats"
    )
    covered = {(r["team_api_id"], r["league_api_id"], r["season"]) for r in existing}

    return [c for c in all_combos if c not in covered]


# ── Backfill runners ─────────────────────────────────────────────────────────

def run_coaches(missing: list[int], dry_run: bool) -> int:
    console.print(f"\n[cyan]Coaches — {len(missing)} teams to fetch[/cyan]")
    if dry_run:
        return 0
    stored = 0
    for team_af_id in missing:
        try:
            raw = get_coaches(team_af_id)
            if raw:
                entries = parse_coaches(raw)
                stored += store_team_coaches(team_af_id, entries)
        except Exception as e:
            console.print(f"  [yellow]coaches {team_af_id}: {e}[/yellow]")
        time.sleep(RATE_DELAY)
    console.print(f"  {stored} coach records upserted")
    return stored


def run_transfers(missing: list[int], dry_run: bool) -> int:
    console.print(f"\n[cyan]Transfers — {len(missing)} teams to fetch[/cyan]")
    if dry_run:
        return 0
    stored = 0
    for team_af_id in missing:
        try:
            raw = get_transfers(team_af_id)
            if raw:
                rows = parse_transfers(raw, team_api_id=team_af_id)
                stored += store_team_transfers(team_af_id, rows)
        except Exception as e:
            console.print(f"  [yellow]transfers {team_af_id}: {e}[/yellow]")
        finally:
            # Always mark as attempted — even on API error, so failing teams don't
            # block the queue by being retried in every batch indefinitely.
            execute_write(
                "INSERT INTO team_transfer_cache (team_api_id, fetched_at) VALUES (%s, NOW())"
                " ON CONFLICT (team_api_id) DO UPDATE SET fetched_at = NOW()",
                (team_af_id,),
            )
        time.sleep(RATE_DELAY)
    console.print(f"  {stored} transfer records stored")
    return stored


def run_team_stats(missing: list[tuple[int, int, int]], dry_run: bool) -> int:
    console.print(f"\n[cyan]Team stats — {len(missing)} (team, league, season) combos to fetch[/cyan]")
    if dry_run:
        return 0
    stored = 0
    for team_api_id, league_api_id, season in missing:
        try:
            raw = get_team_statistics(team_api_id, league_api_id, season)
            if raw:
                parsed = parse_team_statistics(raw)
                store_team_season_stats(team_api_id, league_api_id, season, parsed)
                stored += 1
        except Exception as e:
            console.print(f"  [yellow]team_stats {team_api_id}/{league_api_id}/{season}: {e}[/yellow]")
        time.sleep(RATE_DELAY)
    console.print(f"  {stored} team_season_stats records stored")
    return stored


# ── Main ─────────────────────────────────────────────────────────────────────

def run_coaches_batch(batch_size: int = 10) -> None:
    """Scheduler-callable: fetch coaches for the next batch of uncovered teams."""
    all_ids = _all_team_af_ids()
    missing = _missing_coaches(all_ids)
    if not missing:
        return
    batch = missing[:batch_size]
    run_coaches(batch, dry_run=False)


def run_transfers_batch(batch_size: int = 10) -> None:
    """Scheduler-callable: fetch transfers for the next batch of uncovered teams."""
    all_ids = _all_team_af_ids()
    missing = _missing_transfers(all_ids)
    if not missing:
        return
    batch = missing[:batch_size]
    run_transfers(batch, dry_run=False)


def main():
    parser = argparse.ArgumentParser(description="Backfill team enrichment for historically missing teams")
    parser.add_argument("--components", default="coaches,transfers",
                        help="Comma-separated: coaches, transfers, team_stats (default: coaches,transfers)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="How many teams to process per run (default: 50)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip the first N missing teams — use to process in waves (default: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counts only, make no API calls")
    args = parser.parse_args()

    components = {c.strip() for c in args.components.split(",")}
    batch_size = args.batch_size
    offset = args.offset
    dry_run = args.dry_run

    console.print(f"[bold green]═══ Team Enrichment Backfill ═══[/bold green]")
    console.print(f"  Components : {', '.join(sorted(components))}")
    console.print(f"  Batch size : {batch_size}")
    console.print(f"  Offset     : {offset}")
    console.print(f"  Dry run    : {dry_run}")

    if not dry_run:
        status = get_remaining_requests()
        budget = status.get("remaining", 0)
        console.print(f"  AF budget  : {budget:,} requests remaining")
        if budget < MIN_BUDGET:
            console.print(f"[red]Budget too low ({budget} < {MIN_BUDGET}). Aborting.[/red]")
            sys.exit(1)

    total = 0

    if "coaches" in components or "transfers" in components:
        all_ids = _all_team_af_ids()
        console.print(f"\n  {len(all_ids)} unique AF team IDs in DB")

        if "coaches" in components:
            missing = _missing_coaches(all_ids)
            console.print(f"  {len(missing)} missing coaches (never fetched)")
            batch = missing[offset: offset + batch_size]
            console.print(f"  Processing [{offset}:{offset + len(batch)}] of {len(missing)}")
            total += run_coaches(batch, dry_run)

        if "transfers" in components:
            missing = _missing_transfers(all_ids)
            console.print(f"  {len(missing)} missing transfers (never fetched)")
            batch = missing[offset: offset + batch_size]
            console.print(f"  Processing [{offset}:{offset + len(batch)}] of {len(missing)}")
            total += run_transfers(batch, dry_run)

    if "team_stats" in components:
        missing = _missing_team_stats()
        console.print(f"\n  {len(missing)} missing (team, league, season) combos")
        batch = missing[offset: offset + batch_size]
        console.print(f"  Processing [{offset}:{offset + len(batch)}] of {len(missing)}")
        total += run_team_stats(batch, dry_run)

    console.print(f"\n[bold green]Done. {total} records stored.[/bold green]")
    if not dry_run:
        console.print(f"  Next batch: --offset {offset + batch_size}")


if __name__ == "__main__":
    main()
