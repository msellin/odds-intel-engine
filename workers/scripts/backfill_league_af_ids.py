"""
Backfill api_football_id for orphan leagues.

Leagues in our DB with api_football_id IS NULL were created before we started
populating that field, or came from data paths that didn't set it. This script:

  1. Fetches all current AF leagues in one call (uses ~1 API request)
  2. Builds a normalised (country, name) → id map
  3. Queries our DB for orphan leagues with real names (not "Unknown")
  4. Fuzzy-matches each orphan against the AF map
  5. Updates api_football_id for confirmed matches (with --apply flag)

Run:
  python workers/scripts/backfill_league_af_ids.py           # dry-run, show matches
  python workers/scripts/backfill_league_af_ids.py --apply   # write updates to DB
  python workers/scripts/backfill_league_af_ids.py --unmatched  # show what we couldn't find
"""

import sys
import re
import unicodedata
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query
from workers.api_clients import api_football as af
from rich.console import Console
from rich.table import Table

console = Console()


def _normalize(s: str) -> str:
    """Strip accents, punctuation, lowercase — identical to ensure_team()."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    ascii_only = nfkd.encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


# Country name aliases: AF uses different names than we store from older Kambi data
COUNTRY_ALIASES: dict[str, str] = {
    "England": "England",
    "Czech Republic": "Czech-Republic",  # AF uses hyphenated
    "Czech-Republic": "Czech-Republic",
    "South Korea": "South-Korea",
    "El Salvador": "El-Salvador",
    "New Zealand": "New-Zealand",
    "South Africa": "South-Africa",
    "Saudi Arabia": "Saudi-Arabia",
    "United States": "USA",
    "USA": "USA",
}


def build_af_league_map() -> dict[str, dict]:
    """
    Fetch all current AF leagues and build a normalised lookup map.
    Key: (normalised_country, normalised_name)
    Value: {id, name, country}
    """
    console.print("[cyan]Fetching all AF leagues (current season)...[/cyan]")
    af_leagues = af.get_leagues(current=True)
    console.print(f"  Got {len(af_leagues)} AF leagues")

    af_map: dict[str, dict] = {}
    for entry in af_leagues:
        league = entry.get("league", {})
        country = entry.get("country", {})
        lid = league.get("id")
        name = league.get("name", "")
        cname = country.get("name", "")
        key = _normalize(cname) + "_" + _normalize(name)
        af_map[key] = {"id": lid, "name": name, "country": cname}

    return af_map


def get_orphan_leagues() -> list[dict]:
    """Get all leagues with api_football_id IS NULL and a real name."""
    return execute_query("""
        SELECT l.id, l.name, l.country,
               count(m.id) AS match_count
        FROM leagues l
        LEFT JOIN matches m ON m.league_id = l.id
        WHERE l.api_football_id IS NULL
          AND l.name IS NOT NULL
          AND l.name != 'Unknown'
        GROUP BY l.id, l.name, l.country
        ORDER BY match_count DESC, l.country, l.name
    """)


def match_leagues(orphans: list[dict], af_map: dict[str, dict]) -> tuple[list, list]:
    """
    Returns (matched, unmatched).
    matched = [{orphan, af_league}]
    unmatched = [orphan]
    """
    matched = []
    unmatched = []

    for orphan in orphans:
        # Try direct normalised match first
        country = orphan["country"] or ""
        name = orphan["name"] or ""

        # Try both the stored country and any alias
        candidates = [country, COUNTRY_ALIASES.get(country, country)]

        found = None
        for c in candidates:
            key = _normalize(c) + "_" + _normalize(name)
            if key in af_map:
                found = af_map[key]
                break

        if found:
            matched.append({"orphan": orphan, "af_league": found})
        else:
            unmatched.append(orphan)

    return matched, unmatched


def apply_updates(matched: list[dict]) -> tuple[int, list]:
    """
    Write api_football_id to DB for each matched league.
    Skips rows where the AF ID is already taken by another league record
    (those are duplicates that need merging, not simple ID backfills).
    Returns (update_count, skipped_list).
    """
    updated = 0
    skipped = []
    for m in matched:
        orphan = m["orphan"]
        af_league = m["af_league"]
        try:
            execute_query(
                "UPDATE leagues SET api_football_id = %(af_id)s WHERE id = %(id)s",
                {"af_id": af_league["id"], "id": str(orphan["id"])},
            )
            updated += 1
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate key" in str(e).lower():
                skipped.append({**m, "reason": f"AF ID {af_league['id']} already used by another league"})
            else:
                raise
    return updated, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write updates to DB (default: dry-run only)")
    parser.add_argument("--unmatched", action="store_true",
                        help="Show unmatched orphan leagues in detail")
    args = parser.parse_args()

    af_map = build_af_league_map()
    orphans = get_orphan_leagues()
    console.print(f"  Orphan leagues to match: {len(orphans)} ({sum(o['match_count'] for o in orphans)} matches total)")

    matched, unmatched = match_leagues(orphans, af_map)

    # ── Matched ──
    console.rule(f"[bold green]Matched — {len(matched)} leagues")
    if matched:
        t = Table("Our name", "Our country", "Matches", "AF name", "AF country", "AF ID")
        for m in matched:
            o, af_l = m["orphan"], m["af_league"]
            t.add_row(
                o["name"], o["country"], str(o["match_count"]),
                af_l["name"], af_l["country"], str(af_l["id"]),
            )
        console.print(t)

    # ── Unmatched ──
    console.rule(f"[bold yellow]Unmatched — {len(unmatched)} leagues")
    if args.unmatched or len(unmatched) <= 30:
        t = Table("Name", "Country", "Matches", "DB ID")
        for u in unmatched:
            t.add_row(u["name"], u["country"], str(u["match_count"]), str(u["id"]))
        console.print(t)
    else:
        console.print(f"[yellow]Run with --unmatched to see all {len(unmatched)}[/yellow]")

    console.rule("[bold]Summary")
    console.print(
        f"  Matched:   {len(matched)}\n"
        f"  Unmatched: {len(unmatched)}\n"
    )

    if args.apply:
        if not matched:
            console.print("[yellow]Nothing to update.[/yellow]")
            return
        console.print(f"[cyan]Applying {len(matched)} updates...[/cyan]")
        count, skipped = apply_updates(matched)
        console.print(f"[green]✓ Updated {count} leagues with api_football_id.[/green]")
        if skipped:
            console.print(f"\n[yellow]⚠ {len(skipped)} skipped (AF ID already used by another league — true duplicates):[/yellow]")
            for s in skipped:
                o, af_l = s["orphan"], s["af_league"]
                console.print(f"  {o['country']} / {o['name']} → AF ID {af_l['id']} ({s['reason']})")
    else:
        if matched:
            console.print(
                f"[dim]Dry-run. Re-run with --apply to write {len(matched)} updates.[/dim]"
            )


if __name__ == "__main__":
    main()
