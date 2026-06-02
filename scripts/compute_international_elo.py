"""
OddsIntel — Compute National-Team ELO (WC-PHASE-3)

Walks every finished international match in chronological order and updates
each team's ELO rating. Writes one row per (team, match_date) to
`team_elo_international`. Idempotent — clears the table at start before
re-walking, so the same script can be re-run after new internationals are
backfilled.

K-factor by competition tier:
  tournament (WC, Euro, Copa, AFCON, Asian Cup, Gold Cup):       K=40
  qualifier + Nations League + regional cup:                     K=25
  friendly:                                                      K=10

Home advantage: +60 only for qualifier_nl matches (real home/away).
Tournaments and friendlies are treated as neutral by default.
(Refinement candidate: detect host nation in tournaments — deferred.)

Goal-diff multiplier: max(1, sqrt(gd+1)) — standard ELO scaling.

Usage:
  python scripts/compute_international_elo.py            # full rebuild
  python scripts/compute_international_elo.py --dry-run  # print stats, write nothing
"""
import sys, os, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console
from workers.api_clients.db import execute_query, execute_write, bulk_upsert

console = Console()

# AF league id → category
COMP_CATEGORY = {
    1: "tournament",     # World Cup
    4: "tournament",     # Euro Championship
    6: "tournament",     # Africa Cup of Nations
    7: "tournament",     # Asian Cup
    9: "tournament",     # Copa America
    22: "tournament",    # CONCACAF Gold Cup

    5: "qualifier_nl",   # UEFA Nations League
    24: "qualifier_nl",  # ASEAN Championship
    25: "qualifier_nl",  # Gulf Cup of Nations
    28: "qualifier_nl",  # SAFF Championship
    29: "qualifier_nl",  # WC Qual Africa
    30: "qualifier_nl",  # WC Qual Asia
    31: "qualifier_nl",  # WC Qual CONCACAF
    32: "qualifier_nl",  # WC Qual Europe
    33: "qualifier_nl",  # WC Qual Oceania
    34: "qualifier_nl",  # WC Qual South America
    35: "qualifier_nl",  # Asian Cup Qualification
    36: "qualifier_nl",  # AFCON Qualification
    37: "qualifier_nl",  # WC Qual Intercontinental Play-offs
    536: "qualifier_nl", # CONCACAF Nations League
    860: "qualifier_nl", # Arab Cup
    960: "qualifier_nl", # Euro Championship Qualification
    1008: "qualifier_nl", # CAFA Nations Cup

    10: "friendly",      # Friendlies
    913: "friendly",     # Finalissima (single match)
}

K_BY_CAT = {"tournament": 40, "qualifier_nl": 25, "friendly": 10}
HOME_ADV_BY_CAT = {"tournament": 0, "qualifier_nl": 60, "friendly": 0}


def category_for_league(league_af_id: int | None) -> str:
    if league_af_id is None:
        return "friendly"
    return COMP_CATEGORY.get(league_af_id, "friendly")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Compute but don't write")
    args = parser.parse_args()

    console.print("[bold cyan]═══ Compute international ELO ═══[/bold cyan]\n")

    # Pull all finished international matches in chronological order
    matches = execute_query("""
        SELECT m.id, m.date::date AS match_date,
               m.home_team_id, m.away_team_id,
               m.score_home, m.score_away,
               l.api_football_id AS league_af_id, l.name AS league_name
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE l.country = 'World'
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL
          AND m.score_away IS NOT NULL
          AND l.api_football_id = ANY(%s::int[])
        ORDER BY m.date ASC, m.id ASC
    """, [list(COMP_CATEGORY.keys())])

    console.print(f"  loaded {len(matches)} finished international matches")
    if not matches:
        console.print("[red]No matches found — backfill internationals first[/red]")
        return

    # ELO walk
    elo: dict[str, float] = {}   # team_id → current rating
    n: dict[str, int] = {}        # team_id → matches played
    last_comp: dict[str, str] = {}
    rows: list[tuple] = []        # (team_id, match_date, elo, n, last_comp)
    counts_by_cat = {"tournament": 0, "qualifier_nl": 0, "friendly": 0}

    for m in matches:
        cat = category_for_league(m["league_af_id"])
        K = K_BY_CAT[cat]
        h_adv = HOME_ADV_BY_CAT[cat]

        h_id = m["home_team_id"]
        a_id = m["away_team_id"]
        h_elo = elo.get(h_id, 1500.0) + h_adv
        a_elo = elo.get(a_id, 1500.0)

        # Expected scores from rating differential
        exp_h = 1.0 / (1 + 10 ** ((a_elo - h_elo) / 400))
        exp_a = 1.0 - exp_h

        sh, sa = m["score_home"], m["score_away"]
        gd = abs(sh - sa)
        gd_mult = max(1.0, (gd + 1) ** 0.5)

        if sh > sa:
            act_h, act_a = 1.0, 0.0
        elif sh < sa:
            act_h, act_a = 0.0, 1.0
        else:
            act_h, act_a = 0.5, 0.5

        new_h = elo.get(h_id, 1500.0) + K * gd_mult * (act_h - exp_h)
        new_a = elo.get(a_id, 1500.0) + K * gd_mult * (act_a - exp_a)

        elo[h_id] = new_h
        elo[a_id] = new_a
        n[h_id] = n.get(h_id, 0) + 1
        n[a_id] = n.get(a_id, 0) + 1
        last_comp[h_id] = cat
        last_comp[a_id] = cat

        rows.append((h_id, m["match_date"], round(new_h, 2), n[h_id], cat))
        rows.append((a_id, m["match_date"], round(new_a, 2), n[a_id], cat))
        counts_by_cat[cat] += 1

    console.print(f"\n  walked: {counts_by_cat['tournament']} tournament, "
                  f"{counts_by_cat['qualifier_nl']} qualifier/NL, "
                  f"{counts_by_cat['friendly']} friendly matches")
    console.print(f"  unique teams with ELO: {len(elo)}")
    console.print(f"  total ELO update rows to write: {len(rows)}")

    # Top + bottom ratings sanity check
    final = [(team_id, rating, n.get(team_id, 0)) for team_id, rating in elo.items()]
    final.sort(key=lambda x: -x[1])
    top = final[:15]
    bot = final[-10:]

    # Resolve team names for printing
    all_ids = [t[0] for t in top] + [t[0] for t in bot]
    teams = execute_query("SELECT id, name FROM teams WHERE id = ANY(%s::uuid[])", [all_ids])
    name_by_id = {t["id"]: t["name"] for t in teams}

    console.print("\n[bold]Top 15 by ELO:[/bold]")
    for tid, rating, nm in top:
        console.print(f"  {rating:>7.1f}  {name_by_id.get(tid, '?')}  (n={nm})")
    console.print("\n[bold]Bottom 10 by ELO:[/bold]")
    for tid, rating, nm in bot:
        console.print(f"  {rating:>7.1f}  {name_by_id.get(tid, '?')}  (n={nm})")

    if args.dry_run:
        console.print("\n[yellow]Dry run — no writes.[/yellow]")
        return

    # Deduplicate (team_id, match_date) — keep the chronologically LAST update.
    # A team can play more than once on the same date in qualifier doubleheaders
    # or congested tournament schedules. `rows` is built in chronological order
    # so the last entry per key is the correct post-day ELO.
    dedup: dict[tuple, tuple] = {}
    for row in rows:
        dedup[(row[0], row[1])] = row
    deduped_rows = list(dedup.values())
    if len(deduped_rows) != len(rows):
        console.print(f"  deduped {len(rows) - len(deduped_rows)} same-day double-up rows")

    # Clear + bulk upsert
    console.print("\n[cyan]Clearing team_elo_international and writing fresh rows...[/cyan]")
    execute_write("TRUNCATE team_elo_international")

    updated = bulk_upsert(
        table="team_elo_international",
        columns=["team_id", "match_date", "elo_rating", "n_matches", "last_comp"],
        rows=deduped_rows,
        conflict_columns=["team_id", "match_date"],
        update_columns=["elo_rating", "n_matches", "last_comp"],
    )
    console.print(f"[green]✓ wrote {updated} rows[/green]")


if __name__ == "__main__":
    main()
