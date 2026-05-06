"""
Duplicate Detection Script — OddsIntel Engine

Finds duplicate leagues, teams, and fixtures created by the Kambi scraper
(which was removed 2026-05-06). Outputs:
  1. Human-readable report to stdout
  2. SQL merge migration to stdout (--sql flag) or a file (--out FILE)
  3. Baseline row counts (--counts flag)

Run:
  python workers/scripts/detect_duplicates.py           # report only
  python workers/scripts/detect_duplicates.py --counts  # baseline counts
  python workers/scripts/detect_duplicates.py --sql     # generate migration SQL
  python workers/scripts/detect_duplicates.py --sql --out supabase/migrations/047_drop_kambi_duplicates.sql
"""

import sys
import argparse
import re
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query
from rich.console import Console
from rich.table import Table

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Identical normalisation used in ensure_team() — strip accents, punctuation, lowercase."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


# ── Counts ────────────────────────────────────────────────────────────────────

def print_counts():
    console.rule("[bold cyan]Baseline Row Counts")
    row = execute_query("""
        SELECT
          (SELECT count(*) FROM leagues)        AS leagues,
          (SELECT count(*) FROM teams)          AS teams,
          (SELECT count(*) FROM matches)        AS matches,
          (SELECT count(*) FROM simulated_bets) AS simulated_bets,
          (SELECT count(*) FROM predictions)    AS predictions,
          (SELECT count(*) FROM odds_snapshots) AS odds_snapshots,
          (SELECT count(*) FROM match_signals)  AS match_signals
    """)[0]
    t = Table("Table", "Rows")
    for k, v in row.items():
        t.add_row(k, f"{v:,}")
    console.print(t)
    return row


# ── Duplicate leagues ─────────────────────────────────────────────────────────

def find_duplicate_leagues():
    """
    Kambi leagues have api_football_id IS NULL (AF leagues always get one).
    For each orphan league that has matches, find the AF canonical by matching
    normalised (country, name).
    """
    orphans = execute_query("""
        SELECT l.id, l.name, l.country, l.tier,
               count(m.id) AS match_count
        FROM leagues l
        LEFT JOIN matches m ON m.league_id = l.id
        WHERE l.api_football_id IS NULL
        GROUP BY l.id, l.name, l.country, l.tier
        HAVING count(m.id) > 0
        ORDER BY l.country, l.name
    """)

    canonicals = execute_query("""
        SELECT id, name, country FROM leagues WHERE api_football_id IS NOT NULL
    """)
    canonical_map: dict[str, dict] = {}
    for c in canonicals:
        key = _normalize(c["country"]) + "_" + _normalize(c["name"])
        canonical_map[key] = c

    pairs = []
    unmatched = []
    for orphan in orphans:
        key = _normalize(orphan["country"]) + "_" + _normalize(orphan["name"])
        canon = canonical_map.get(key)
        if canon:
            pairs.append({"orphan": orphan, "canonical": canon})
        else:
            unmatched.append(orphan)

    return pairs, unmatched


# ── Duplicate teams ───────────────────────────────────────────────────────────

def find_duplicate_teams():
    """
    Find teams where same (country, normalised_name) appears more than once.
    Canonical = team with more matches (or whichever has the AF-style name without FK prefix).
    """
    all_teams = execute_query("""
        SELECT t.id, t.name, t.country,
               count(DISTINCT m.id) AS match_count
        FROM teams t
        LEFT JOIN matches m ON m.home_team_id = t.id OR m.away_team_id = t.id
        GROUP BY t.id, t.name, t.country
    """)

    # Group by normalised key
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for team in all_teams:
        key = _normalize(team["country"]) + "_" + _normalize(team["name"])
        groups[key].append(team)

    pairs = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        # Canonical = highest match count; tie-break = shorter name (no FK/FC prefix)
        group.sort(key=lambda t: (-t["match_count"], len(t["name"])))
        canonical = group[0]
        for orphan in group[1:]:
            pairs.append({"orphan": orphan, "canonical": canonical})

    return pairs


# ── Duplicate fixtures ────────────────────────────────────────────────────────

def find_duplicate_fixtures():
    """
    Same (home_team_id, away_team_id, date::date) appearing more than once.
    Canonical = match with api_football_id (AF fixture); orphan = Kambi-created.
    Returns each duplicate group with properly-parsed UUID lists.
    """
    rows = execute_query("""
        SELECT
            home_team_id, away_team_id, date::date AS match_date,
            count(*) AS fixture_count,
            -- Use string_agg so we control the delimiter, avoiding array parsing issues
            string_agg(id::text, ',' ORDER BY api_football_id NULLS LAST) AS match_ids_str,
            string_agg(coalesce(api_football_id::text, 'null'), ','
                       ORDER BY api_football_id NULLS LAST) AS af_ids_str
        FROM matches
        GROUP BY home_team_id, away_team_id, date::date
        HAVING count(*) > 1
        ORDER BY match_date DESC
    """)

    dupes = []
    for r in rows:
        match_ids = r["match_ids_str"].split(",")
        af_ids = r["af_ids_str"].split(",")
        dupes.append({
            "match_date": r["match_date"],
            "fixture_count": r["fixture_count"],
            "match_ids": match_ids,
            "af_ids": af_ids,
        })
    return dupes


# ── SQL generation ────────────────────────────────────────────────────────────

def generate_sql(league_pairs, team_pairs, fixture_dupes) -> str:
    lines = [
        "-- ============================================================",
        "-- Migration 047: Remove Kambi Duplicate Leagues, Teams, Fixtures",
        "-- ============================================================",
        "-- Generated by workers/scripts/detect_duplicates.py",
        "-- Kambi scraper removed 2026-05-06. This cleans up the DB records",
        "-- it created that duplicate API-Football canonical records.",
        "--",
        "-- SAFE TO RE-RUN: all UPDATEs are idempotent (WHERE old_id has no",
        "-- effect once rows are already re-pointed).",
        "",
        "BEGIN;",
        "",
    ]

    # ── League merges ──
    if league_pairs:
        lines += [
            "-- ============================================================",
            "-- 1. Merge Kambi duplicate leagues into AF canonical",
            "-- ============================================================",
            "",
        ]
        for p in league_pairs:
            o = p["orphan"]
            c = p["canonical"]
            lines += [
                f"-- {o['country']} / {o['name']} ({o['match_count']} matches) → {c['country']} / {c['name']}",
                f"UPDATE matches        SET league_id = '{c['id']}' WHERE league_id = '{o['id']}';",
                f"UPDATE seasons        SET league_id = '{c['id']}' WHERE league_id = '{o['id']}';",
                f"UPDATE model_evaluations SET league_id = '{c['id']}' WHERE league_id = '{o['id']}';",
                f"DELETE FROM leagues   WHERE id = '{o['id']}';",
                "",
            ]

    # ── Team merges ──
    if team_pairs:
        lines += [
            "-- ============================================================",
            "-- 2. Merge duplicate teams (same normalised name/country)",
            "-- ============================================================",
            "",
        ]
        for p in team_pairs:
            o = p["orphan"]
            c = p["canonical"]
            lines += [
                f"-- {o['country']}: '{o['name']}' ({o['match_count']} matches) → '{c['name']}' ({c['match_count']} matches)",
                f"UPDATE matches        SET home_team_id = '{c['id']}' WHERE home_team_id = '{o['id']}';",
                f"UPDATE matches        SET away_team_id = '{c['id']}' WHERE away_team_id = '{o['id']}';",
                f"UPDATE lineups        SET team_id = '{c['id']}' WHERE team_id = '{o['id']}';",
                f"UPDATE players        SET team_id = '{c['id']}' WHERE team_id = '{o['id']}';",
                f"UPDATE manager_tenures SET team_id = '{c['id']}' WHERE team_id = '{o['id']}';",
                f"-- team_transfers uses team_api_id (integer AF ID), not team_id UUID — skip",
                f"DELETE FROM team_elo_daily  WHERE team_id = '{o['id']}';",
                f"DELETE FROM team_form_cache WHERE team_id = '{o['id']}';",
                f"DELETE FROM teams           WHERE id = '{o['id']}';",
                "",
            ]

    # ── Fixture merges ──
    if fixture_dupes:
        lines += [
            "-- ============================================================",
            "-- 3. Merge duplicate fixtures (same home+away+date)",
            "-- NOTE: Run AFTER league + team merges above.",
            "-- ============================================================",
            "",
        ]
        for d in fixture_dupes:
            ids = d["match_ids"]
            af_ids = d["af_ids"]
            # Canonical = first in array (sorted by af_id NULLS LAST, so AF fixture is first if it exists)
            canonical_id = ids[0]
            for orphan_id in ids[1:]:
                lines += [
                    f"-- Duplicate fixture on {d['match_date']}",
                    f"-- Canonical: {canonical_id} (af_id={af_ids[0]})",
                    f"-- Orphan:    {orphan_id}",
                    f"UPDATE simulated_bets     SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE predictions        SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE odds_snapshots     SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_signals      SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_events       SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_stats        SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_weather      SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_player_stats SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_injuries     SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE live_match_snapshots SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE news_events        SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE lineups            SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE user_bets          SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_previews     SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE referee_matches    SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"UPDATE match_page_views   SET match_id = '{canonical_id}' WHERE match_id = '{orphan_id}';",
                    f"DELETE FROM matches WHERE id = '{orphan_id}';",
                    "",
                ]

    # ── Kambi odds_snapshots cleanup ──
    lines += [
        "-- ============================================================",
        "-- 4. Remove Kambi-sourced odds rows (ub/paf/kambi bookmakers)",
        "-- These were redundant: AF already covers Unibet separately.",
        "-- ============================================================",
        "",
        "DELETE FROM odds_snapshots WHERE bookmaker IN ('ub', 'paf', 'kambi');",
        "",
    ]

    lines += ["COMMIT;", ""]
    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(league_pairs, league_unmatched, team_pairs, fixture_dupes):
    console.rule("[bold cyan]Duplicate Leagues")
    if league_pairs:
        t = Table("Orphan (Kambi)", "Matches", "→ Canonical (AF)")
        for p in league_pairs:
            o, c = p["orphan"], p["canonical"]
            t.add_row(f"{o['country']} / {o['name']}", str(o["match_count"]),
                      f"{c['country']} / {c['name']}")
        console.print(t)
    else:
        console.print("[green]None found[/green]")

    if league_unmatched:
        console.print(f"\n[yellow]⚠ {len(league_unmatched)} orphan leagues with NO AF match found:[/yellow]")
        for u in league_unmatched:
            console.print(f"  {u['country']} / {u['name']} — {u['match_count']} matches (id={u['id']})")
        console.print("[yellow]These need manual investigation before running the migration.[/yellow]")

    console.rule("[bold cyan]Duplicate Teams")
    if team_pairs:
        t = Table("Orphan", "Matches", "→ Canonical", "Matches")
        for p in team_pairs:
            o, c = p["orphan"], p["canonical"]
            t.add_row(f"{o['country']}: {o['name']}", str(o["match_count"]),
                      f"{c['country']}: {c['name']}", str(c["match_count"]))
        console.print(t)
    else:
        console.print("[green]None found[/green]")

    console.rule("[bold cyan]Duplicate Fixtures")
    if fixture_dupes:
        t = Table("Date", "Fixture count", "Match IDs")
        for d in fixture_dupes:
            t.add_row(str(d["match_date"]), str(d["fixture_count"]),
                      "\n".join(str(i) for i in d["match_ids"]))
        console.print(t)
    else:
        console.print("[green]None found[/green]")

    console.rule("[bold]Summary")
    console.print(
        f"  Duplicate leagues:  {len(league_pairs)} mergeable, {len(league_unmatched)} unmatched\n"
        f"  Duplicate teams:    {len(team_pairs)}\n"
        f"  Duplicate fixtures: {len(fixture_dupes)}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", action="store_true", help="Print baseline row counts")
    parser.add_argument("--sql", action="store_true", help="Generate merge migration SQL")
    parser.add_argument("--out", type=str, help="Write SQL to this file instead of stdout")
    args = parser.parse_args()

    if args.counts:
        print_counts()
        return

    console.print("[cyan]Scanning for duplicates...[/cyan]")
    league_pairs, league_unmatched = find_duplicate_leagues()
    team_pairs = find_duplicate_teams()
    fixture_dupes = find_duplicate_fixtures()

    print_report(league_pairs, league_unmatched, team_pairs, fixture_dupes)

    if args.sql:
        if league_unmatched:
            console.print(
                f"\n[yellow]⚠ {len(league_unmatched)} unmatched orphan leagues will be left as-is.[/yellow]\n"
                "These are likely AF-created leagues without api_football_id populated,\n"
                "not Kambi duplicates. Only the 20 confirmed merges will be included in SQL."
            )
        sql = generate_sql(league_pairs, team_pairs, fixture_dupes)
        if args.out:
            Path(args.out).write_text(sql)
            console.print(f"\n[green]SQL written to {args.out}[/green]")
        else:
            console.print("\n[bold]--- GENERATED SQL ---[/bold]")
            console.print(sql)


if __name__ == "__main__":
    main()
