"""
OddsIntel — Historical Internationals Backfill (WC-PHASE-2)

One-off script to pull historical national-team competition fixtures and their
nested data (lineups, events, statistics, player stats) from API-Football, for
the purpose of training a national-team prediction model ahead of WC 2026.

The regular pipeline only pulls today's date, so without this script the engine
has effectively no historical international football data. AF supports up to
WC 2018 / Euro 2020 in its `/fixtures?league=N&season=YYYY` endpoint.

Usage:
  python scripts/backfill_internationals.py                  # Full backfill (~50 competitions)
  python scripts/backfill_internationals.py --no-enrichment  # Fixtures only, skip lineups/events/stats
  python scripts/backfill_internationals.py --dry-run        # Print plan, make no calls
  python scripts/backfill_internationals.py --filter "World Cup,Euro"  # Substring filter on labels

Idempotent: bulk_store_matches upserts on (api_football_id), and enrichment
skips matches that already have stats rows.
"""
import sys, os, argparse, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from rich.console import Console

from workers.api_clients.api_football import (
    get_fixtures_by_league_season,
    fixture_to_match_dict,
    get_fixtures_batch,
    parse_fixture_lineups,
    parse_fixture_stats,
    parse_fixture_events,
    parse_fixture_players,
)
from workers.api_clients.supabase_client import (
    bulk_store_matches,
    store_match_lineups,
    store_match_stats_full,
    store_match_events_af,
    store_match_player_stats,
)
from workers.api_clients.db import execute_query

console = Console()


# ── Competition list ─────────────────────────────────────────────────────────
# Discovered via /tmp/probe_intl.py against AF's /leagues?country=World endpoint.
# Each entry: (league_af_id, season, label). Order roughly by recency / impact.
COMPETITIONS = [
    # World Cup history (most direct signal for WC 2026 predictions)
    (1, 2022, "World Cup 2022 (Qatar)"),
    (1, 2018, "World Cup 2018 (Russia)"),

    # Continental championships — Europe
    (4, 2024, "Euro 2024"),
    (4, 2020, "Euro 2020"),
    (960, 2023, "Euro 2024 Qualification"),

    # Continental championships — Americas
    (9, 2024, "Copa America 2024"),
    (9, 2021, "Copa America 2021"),
    (22, 2025, "CONCACAF Gold Cup 2025"),
    (22, 2023, "CONCACAF Gold Cup 2023"),
    (22, 2021, "CONCACAF Gold Cup 2021"),
    (22, 2019, "CONCACAF Gold Cup 2019"),
    (536, 2024, "CONCACAF Nations League 2024"),
    (536, 2023, "CONCACAF Nations League 2023"),
    (536, 2022, "CONCACAF Nations League 2022"),

    # Continental championships — Africa / Asia
    (6, 2025, "Africa Cup of Nations 2025"),
    (6, 2023, "Africa Cup of Nations 2023"),
    (6, 2021, "Africa Cup of Nations 2021"),
    (6, 2019, "Africa Cup of Nations 2019"),
    (7, 2023, "Asian Cup 2023"),
    (7, 2019, "Asian Cup 2019"),
    (35, 2024, "Asian Cup Qualification 2024"),
    (35, 2022, "Asian Cup Qualification 2022"),
    (36, 2025, "AFCON Qualification 2025"),
    (36, 2023, "AFCON Qualification 2023"),
    (36, 2021, "AFCON Qualification 2021"),

    # UEFA Nations League — all editions (huge match volume, great for ELO)
    (5, 2024, "UEFA Nations League 2024-25"),
    (5, 2022, "UEFA Nations League 2022-23"),
    (5, 2020, "UEFA Nations League 2020-21"),
    (5, 2018, "UEFA Nations League 2018-19"),

    # WC 2026 Qualifiers — most recent cycle, all confederations
    (32, 2024, "WC 2026 Qual — Europe"),
    (29, 2023, "WC 2026 Qual — Africa"),
    (30, 2026, "WC 2026 Qual — Asia"),
    (31, 2026, "WC 2026 Qual — CONCACAF"),
    (33, 2026, "WC 2026 Qual — Oceania"),
    (34, 2026, "WC 2026 Qual — South America"),
    (37, 2026, "WC 2026 Qual — Intercontinental Play-offs"),

    # WC 2022 Qualifiers — for training history
    (32, 2020, "WC 2022 Qual — Europe"),
    (29, 2022, "WC 2022 Qual — Africa"),
    (30, 2022, "WC 2022 Qual — Asia"),
    (31, 2022, "WC 2022 Qual — CONCACAF"),
    (33, 2022, "WC 2022 Qual — Oceania"),
    (34, 2022, "WC 2022 Qual — South America"),
    (37, 2022, "WC 2022 Qual — Intercontinental Play-offs"),

    # Regional + smaller national-team comps
    (24, 2024, "ASEAN Championship 2024"),
    (24, 2022, "ASEAN Championship 2022"),
    (25, 2024, "Gulf Cup of Nations 2024"),
    (25, 2023, "Gulf Cup of Nations 2023"),
    (28, 2023, "SAFF Championship 2023"),
    (28, 2021, "SAFF Championship 2021"),
    (1008, 2025, "CAFA Nations Cup 2025"),
    (1008, 2023, "CAFA Nations Cup 2023"),
    (860, 2025, "Arab Cup 2025"),
    (860, 2021, "Arab Cup 2021"),
    (913, 2026, "Finalissima 2026"),
    (913, 2022, "Finalissima 2022"),

    # Friendlies — annual buckets (already partially in DB, will dedup)
    (10, 2025, "Friendlies 2025"),
    (10, 2024, "Friendlies 2024"),
    (10, 2023, "Friendlies 2023"),
    (10, 2022, "Friendlies 2022"),
]


def backfill_fixtures(comps: list[tuple], dry_run: bool = False) -> dict[int, str]:
    """Pull fixtures for each (lid, season). Returns af_id → match_id map for all stored fixtures."""
    af_to_match: dict[int, str] = {}
    total_in = 0
    total_finished = 0
    total_stored = 0

    for lid, season, label in comps:
        try:
            t0 = time.monotonic()
            fixtures = get_fixtures_by_league_season(lid, season)
        except Exception as e:
            console.print(f"  [red]✗ {label} (league={lid} s={season}): AF error: {e}[/red]")
            continue

        if not fixtures:
            console.print(f"  [yellow]· {label}: 0 fixtures (skip)[/yellow]")
            continue

        finished = sum(1 for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN"))
        total_in += len(fixtures)
        total_finished += finished

        if dry_run:
            console.print(f"  [cyan]→ {label} (league={lid} s={season}): {len(fixtures)} fixtures ({finished} finished) — DRY RUN, not storing[/cyan]")
            continue

        # Store via bulk_store_matches (idempotent upsert)
        match_dicts = [fixture_to_match_dict(f) for f in fixtures]
        try:
            match_ids = bulk_store_matches(match_dicts)
        except Exception as e:
            console.print(f"  [red]✗ {label}: store failed: {e}[/red]")
            continue

        stored = sum(1 for mid in match_ids if mid)
        total_stored += stored

        for f, mid in zip(fixtures, match_ids):
            if not mid:
                continue
            af_id = f.get("fixture", {}).get("id")
            if af_id:
                af_to_match[af_id] = mid

        elapsed = time.monotonic() - t0
        console.print(f"  [green]✓ {label}: {stored}/{len(fixtures)} stored ({finished} finished, {elapsed:.1f}s)[/green]")

    console.print(f"\n[bold]Fixtures: {total_stored} stored across competitions ({total_finished} finished, {total_in} total returned)[/bold]\n")
    return af_to_match


def enrich_finished_matches(af_to_match: dict[int, str], chunk_size: int = 20) -> dict:
    """
    For finished matches in the af_to_match map, pull nested lineups/events/
    stats/players via get_fixtures_batch (one AF call per 20 fixtures).

    Skips matches that already have a `match_stats` row (idempotent).
    """
    counts = {"lineups": 0, "events": 0, "stats": 0, "players": 0, "skipped": 0, "errors": 0}

    if not af_to_match:
        return counts

    # Filter to finished matches only — no point fetching lineups for scheduled
    finished_match_ids = list(af_to_match.values())

    # Find which match_ids are actually finished
    finished_rows = execute_query(
        "SELECT id, api_football_id, home_team_api_id FROM matches "
        "WHERE id = ANY(%s::uuid[]) AND status = 'finished'",
        [finished_match_ids]
    )
    if not finished_rows:
        console.print("  [yellow]No finished matches to enrich[/yellow]")
        return counts

    # Skip ones that already have stats
    finished_ids = [r["id"] for r in finished_rows]
    existing = execute_query(
        "SELECT match_id FROM match_stats WHERE match_id = ANY(%s::uuid[])",
        [finished_ids]
    )
    enriched_set = {r["match_id"] for r in existing}

    to_enrich = [r for r in finished_rows if r["id"] not in enriched_set]
    counts["skipped"] = len(finished_rows) - len(to_enrich)

    console.print(f"  Enrichment plan: {len(to_enrich)} matches to fetch, {counts['skipped']} already enriched")

    if not to_enrich:
        return counts

    # Build af_id list and a quick lookup
    af_id_to_row = {r["api_football_id"]: r for r in to_enrich if r["api_football_id"]}
    af_ids = list(af_id_to_row.keys())

    # Batch in chunks of 20
    for i in range(0, len(af_ids), chunk_size):
        chunk = af_ids[i : i + chunk_size]
        try:
            batch = get_fixtures_batch(chunk)
        except Exception as e:
            console.print(f"  [yellow]Batch {i}-{i+chunk_size} failed: {e}[/yellow]")
            counts["errors"] += len(chunk)
            continue

        for af_id, fix in batch.items():
            row = af_id_to_row.get(af_id)
            if not row:
                continue
            match_id = row["id"]
            home_api_id = row.get("home_team_api_id")

            # Lineups
            try:
                lineups_raw = fix.get("lineups", [])
                parsed = parse_fixture_lineups(lineups_raw)
                if parsed:
                    store_match_lineups(match_id, parsed)
                    counts["lineups"] += 1
            except Exception as e:
                console.print(f"    [yellow]lineups err af={af_id}: {e}[/yellow]")

            # Events
            try:
                events_raw = fix.get("events", [])
                parsed_events = parse_fixture_events(events_raw)
                if parsed_events:
                    n = store_match_events_af(match_id, parsed_events, home_team_api_id=home_api_id)
                    if n:
                        counts["events"] += 1
            except Exception as e:
                console.print(f"    [yellow]events err af={af_id}: {e}[/yellow]")

            # Stats (full + halftime if present in batch — halftime only via separate endpoint, skip in backfill)
            try:
                stats_raw = fix.get("statistics", [])
                parsed_stats = parse_fixture_stats(stats_raw)
                if parsed_stats:
                    store_match_stats_full(match_id, parsed_stats)
                    counts["stats"] += 1
            except Exception as e:
                console.print(f"    [yellow]stats err af={af_id}: {e}[/yellow]")

            # Player stats
            try:
                players_raw = fix.get("players", [])
                parsed_players = parse_fixture_players(players_raw, home_team_api_id=home_api_id)
                if parsed_players:
                    n = store_match_player_stats(match_id, af_id, parsed_players)
                    if n:
                        counts["players"] += 1
            except Exception as e:
                console.print(f"    [yellow]players err af={af_id}: {e}[/yellow]")

        # Throttle slightly to be polite to AF
        time.sleep(0.2)

        # Progress every 5 batches
        if (i // chunk_size) % 5 == 4:
            console.print(f"    progress: {i + chunk_size}/{len(af_ids)} fixtures enriched")

    return counts


def main():
    parser = argparse.ArgumentParser(description="Backfill historical international fixtures + nested data")
    parser.add_argument("--no-enrichment", action="store_true", help="Skip lineups/events/stats backfill")
    parser.add_argument("--dry-run", action="store_true", help="Print plan, make no DB writes (still hits AF for fixtures)")
    parser.add_argument("--filter", type=str, default=None,
                        help="Comma-separated substrings; only competitions matching any substring are processed")
    args = parser.parse_args()

    comps = COMPETITIONS
    if args.filter:
        needles = [s.strip().lower() for s in args.filter.split(",") if s.strip()]
        comps = [c for c in COMPETITIONS if any(n in c[2].lower() for n in needles)]

    console.print(f"[bold green]═══ Internationals Backfill: {len(comps)} competitions ═══[/bold green]\n")

    # Phase A: fixtures
    af_to_match = backfill_fixtures(comps, dry_run=args.dry_run)

    if args.dry_run:
        console.print("[cyan]Dry run — skipping enrichment.[/cyan]")
        return

    if args.no_enrichment:
        console.print("[cyan]--no-enrichment set — skipping nested data fetch.[/cyan]")
        return

    # Phase B: nested data for finished matches
    console.print("[bold green]═══ Enriching finished matches (lineups, events, stats, players) ═══[/bold green]")
    counts = enrich_finished_matches(af_to_match)
    console.print(f"\n[bold green]Enrichment done:[/bold green] "
                  f"{counts['lineups']} lineups, {counts['events']} events, "
                  f"{counts['stats']} stats, {counts['players']} player_stats; "
                  f"{counts['skipped']} already enriched, {counts['errors']} errors")


if __name__ == "__main__":
    main()
