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

K_BY_CAT = {"tournament": 30, "qualifier_nl": 25, "friendly": 10}
HOME_ADV_BY_CAT = {"tournament": 0, "qualifier_nl": 60, "friendly": 0}
# WC-A1 (2026-06-04): tournament K reduced from 40 → 30 to match the
# eloratings.net convention. K=40 over-rewarded deep tournament runs
# (Morocco's WC22 semi-final pushed them to ~2030, ~270pts above their
# real-world ~1757 anchor) which then made our predictor favour Morocco
# over Brazil in the WC2026 opener. K=30 still rewards tournament results
# meaningfully but doesn't compound away from market reality.

# INITIAL_ELO_SEEDS (WC-A1, 2026-06-04) — anchor each major national side at a
# realistic starting rating so the walk doesn't have to "discover" that Brazil
# > San Marino from scratch. Sourced from eloratings.net snapshot circa 2017
# (start of our backfill window) and rounded to the nearest 10. The walk
# proceeds normally from these seeds; teams not listed start at the legacy
# 1500 default. This single change fixes the Morocco-1940 / Brazil-1759
# inversion bug because Morocco's WC22 gains land on a realistic base instead
# of compounding from 1500, while Brazil's recent qualifier wobbles only nudge
# them off the correct ~2050 anchor instead of falling from 1500.
#
# Format: team name (matches `teams.name` in our DB) → starting ELO at 2017-01-01.
INITIAL_ELO_SEEDS: dict[str, int] = {
    # Top tier — South America
    "Brazil": 2080,
    "Argentina": 2010,
    "Colombia": 1830,
    "Uruguay": 1840,
    "Chile": 1820,
    "Peru": 1740,
    "Paraguay": 1720,
    "Ecuador": 1740,
    "Venezuela": 1680,
    "Bolivia": 1660,
    # Top tier — Europe
    "Germany": 2050,
    "Spain": 2010,
    "France": 1980,
    "Belgium": 2020,
    "Portugal": 1940,
    "Italy": 1900,
    "England": 1900,
    "Netherlands": 1880,
    "Croatia": 1850,
    "Switzerland": 1830,
    "Poland": 1810,
    "Wales": 1800,
    "Sweden": 1790,
    "Denmark": 1780,
    "Austria": 1770,
    "Czech Republic": 1770,
    "Türkiye": 1760,
    "Turkey": 1760,
    "Ukraine": 1760,
    "Russia": 1760,
    "Slovakia": 1740,
    "Republic of Ireland": 1740,
    "Ireland": 1740,
    "Romania": 1730,
    "Iceland": 1730,
    "Hungary": 1720,
    "Serbia": 1720,
    "Norway": 1720,
    "Greece": 1720,
    "Scotland": 1710,
    "Finland": 1670,
    "Bosnia and Herzegovina": 1690,
    "Northern Ireland": 1680,
    "Slovenia": 1670,
    "Albania": 1660,
    "Bulgaria": 1620,
    "Israel": 1700,
    "North Macedonia": 1680,
    "Georgia": 1640,
    # Top tier — CONCACAF
    "Mexico": 1820,
    "USA": 1790,
    "United States": 1790,
    "Costa Rica": 1740,
    "Panama": 1640,
    "Jamaica": 1620,
    "Canada": 1610,
    "Honduras": 1610,
    "Haiti": 1570,
    "El Salvador": 1570,
    # Africa
    "Senegal": 1730,
    "Tunisia": 1730,
    "Egypt": 1720,
    "Morocco": 1720,
    "Algeria": 1720,
    "Nigeria": 1720,
    "Ivory Coast": 1710,
    "Côte d'Ivoire": 1710,
    "Ghana": 1710,
    "Cameroon": 1700,
    "DR Congo": 1650,
    "Congo DR": 1650,
    "Mali": 1660,
    "Burkina Faso": 1660,
    "South Africa": 1620,
    "Cape Verde Islands": 1600,
    "Cape Verde": 1600,
    # Asia
    "Iran": 1770,
    "Japan": 1740,
    "South Korea": 1740,
    "Korea Republic": 1740,
    "Australia": 1720,
    "Saudi Arabia": 1670,
    "Uzbekistan": 1640,
    "Qatar": 1650,
    "Iraq": 1620,
    "United Arab Emirates": 1620,
    "Jordan": 1610,
    "China PR": 1600,
    "China": 1600,
    # Oceania
    "New Zealand": 1620,
}


def initial_elo(team_name: str | None) -> float:
    """Look up the seeded starting ELO for a national side, falling back to
    the legacy 1500 for unseeded teams (mostly smaller federations / non-WC
    sides). Case-insensitive on the name; ' ' / '-' normalised."""
    if not team_name:
        return 1500.0
    norm = team_name.strip()
    if norm in INITIAL_ELO_SEEDS:
        return float(INITIAL_ELO_SEEDS[norm])
    # Cheap case-insensitive fallback for capitalisation drift.
    lower = norm.lower()
    for k, v in INITIAL_ELO_SEEDS.items():
        if k.lower() == lower:
            return float(v)
    return 1500.0


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
               ht.name AS home_name, at.name AS away_name,
               l.api_football_id AS league_af_id, l.name AS league_name
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
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

    # ELO walk — seed each team's starting rating from INITIAL_ELO_SEEDS the
    # first time we see them. Teams not in the seed table start at 1500 (old
    # default) — that's fine for minor federations whose absolute level
    # doesn't matter for our prediction surface.
    elo: dict[str, float] = {}   # team_id → current rating
    n: dict[str, int] = {}        # team_id → matches played
    last_comp: dict[str, str] = {}
    rows: list[tuple] = []        # (team_id, match_date, elo, n, last_comp)
    counts_by_cat = {"tournament": 0, "qualifier_nl": 0, "friendly": 0}
    seeded_count = 0

    for m in matches:
        cat = category_for_league(m["league_af_id"])
        K = K_BY_CAT[cat]
        h_adv = HOME_ADV_BY_CAT[cat]

        h_id = m["home_team_id"]
        a_id = m["away_team_id"]
        # First-touch seed: anchor each side at a realistic starting rating
        # before the walk's first update touches it.
        if h_id not in elo:
            elo[h_id] = initial_elo(m["home_name"])
            if elo[h_id] != 1500.0:
                seeded_count += 1
        if a_id not in elo:
            elo[a_id] = initial_elo(m["away_name"])
            if elo[a_id] != 1500.0:
                seeded_count += 1
        h_elo = elo[h_id] + h_adv
        a_elo = elo[a_id]

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

        new_h = elo[h_id] + K * gd_mult * (act_h - exp_h)
        new_a = elo[a_id] + K * gd_mult * (act_a - exp_a)

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
    console.print(f"  teams seeded from INITIAL_ELO_SEEDS: {seeded_count}")
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
