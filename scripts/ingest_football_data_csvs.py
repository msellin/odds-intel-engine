"""
BACKTEST-HISTORICAL-CSV-INGEST (2026-05-18): pull closing odds from
football-data.co.uk and load into odds_snapshots so the backtester can run
on years of history instead of just the 18 days of live-poll coverage.

CSV source: https://www.football-data.co.uk/data.php — one CSV per league
per season at URL pattern https://www.football-data.co.uk/mmz4281/{season}/{code}.csv
(e.g. mmz4281/2425/E0.csv = Premier League 2024-25 season).

What we extract per row (when present):
  1x2:    PSCH/PSCD/PSCA (Pinnacle closing) or fallback PSH/PSD/PSA
          B365CH/B365CD/B365CA (Bet365 closing) or fallback B365H/B365D/B365A
  OU 2.5: PC>2.5, PC<2.5 (Pinnacle closing) or fallback P>2.5, P<2.5
          B365C>2.5, B365C<2.5 (Bet365 closing) or fallback B365>2.5, B365<2.5

Matching: each football-data row has Date + HomeTeam + AwayTeam. We look up
the corresponding match in our DB by (date_window, fuzzy team names, league).
Team-name aliases are stored inline below — start with EPL and grow as we add
leagues.

Idempotency: each odds_snapshots row keyed by (match_id, bookmaker, market,
selection, is_closing). Skip rows that already exist.

Usage:
  python scripts/ingest_football_data_csvs.py --league E0 --seasons 2425
  python scripts/ingest_football_data_csvs.py --league E0 --seasons 2425,2324,2223
  python scripts/ingest_football_data_csvs.py --league E0 --dry-run
  python scripts/ingest_football_data_csvs.py --all-leagues   (after EPL works)
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

# ── Per-league config ──────────────────────────────────────────────────────
# Maps football-data league codes → our DB league name + country. Used for
# scoping the match-lookup query to the right league.
LEAGUES = {
    "E0":  {"db_name": "Premier League",    "country": "England",  "label": "EPL"},
    "E1":  {"db_name": "Championship",      "country": "England",  "label": "EFL Championship"},
    "E2":  {"db_name": "League One",        "country": "England",  "label": "League One"},
    "E3":  {"db_name": "League Two",        "country": "England",  "label": "League Two"},
    "SP1": {"db_name": "La Liga",           "country": "Spain",    "label": "La Liga"},
    "SP2": {"db_name": "Segunda División",  "country": "Spain",    "label": "Segunda"},
    "D1":  {"db_name": "Bundesliga",        "country": "Germany",  "label": "Bundesliga"},
    "I1":  {"db_name": "Serie A",           "country": "Italy",    "label": "Serie A IT"},
    "F1":  {"db_name": "Ligue 1",           "country": "France",   "label": "Ligue 1"},
    "N1":  {"db_name": "Eredivisie",        "country": "Netherlands", "label": "Eredivisie"},
    "B1":  {"db_name": "Jupiler Pro League","country": "Belgium",  "label": "Jupiler"},
    "P1":  {"db_name": "Primeira Liga",     "country": "Portugal", "label": "Primeira"},
    "T1":  {"db_name": "Süper Lig",         "country": "Turkey",   "label": "Süper Lig"},
    "G1":  {"db_name": "Super League",      "country": "Greece",   "label": "Greece SL"},
}

# Team-name aliases — football-data side → our DB side (substring match
# after normalization). Add entries as we discover mismatches.
TEAM_ALIASES = {
    # English Premier League
    "Man United":          "Manchester United",
    "Man City":            "Manchester City",
    "Spurs":               "Tottenham",
    "Nott'm Forest":       "Nottingham Forest",
    "Newcastle":           "Newcastle United",
    "West Ham":            "West Ham United",
    "Sheffield Utd":       "Sheffield United",
    "Sheffield Weds":      "Sheffield Wednesday",
    "Stoke":               "Stoke City",
    "Hull":                "Hull City",
    # TIER-C-EXPAND-ALIASES (2026-05-25): REMOVED 6 broken aliases that pointed
    # to long names not in our DB — Brighton, Leicester, Norwich, Cardiff,
    # QPR, Inter. DB uses the short names; normalize-only path now matches them.
    # La Liga
    "Atletico Madrid":     "Atlético Madrid",
    "Ath Bilbao":          "Athletic Club",
    "Ath Madrid":          "Atlético Madrid",
    "Celta":               "Celta Vigo",
    "Espanol":             "Espanyol",                  # 274 FD rows
    "Vallecano":           "Rayo Vallecano",            # 274 FD rows
    "Betis":               "Real Betis",                # 266 FD rows
    "Sociedad":            "Real Sociedad",             # 266 FD rows
    # Italy
    "Roma":                "AS Roma",
    "Verona":              "Hellas Verona",
    "Milan":               "AC Milan",                  # 266 FD rows
    "Spal":                "SPAL",
    # Germany
    "Bayern Munich":       "Bayern München",
    "Dortmund":            "Borussia Dortmund",
    "M'gladbach":          "Borussia Monchengladbach",
    "Leverkusen":          "Bayer Leverkusen",
    "Hertha":              "Hertha BSC",                # 238 FD rows (was: Hertha Berlin — wrong)
    "Ein Frankfurt":       "Eintracht Frankfurt",
    "FC Koln":             "1. FC Köln",
    "Heidenheim":          "1. FC Heidenheim",
    "St Pauli":            "FC St. Pauli",
    "Hoffenheim":          "TSG Hoffenheim",            # 238 FD rows
    "Augsburg":            "FC Augsburg",               # 238 FD rows
    "Freiburg":            "SC Freiburg",               # 238 FD rows
    "Hannover":            "Hannover 96",               # 238 FD rows
    "Wolfsburg":           "VfL Wolfsburg",             # 238 FD rows
    "Schalke 04":          "FC Schalke 04",             # 238 FD rows
    "Stuttgart":           "VfB Stuttgart",             # 238 FD rows
    # Spain
    "Sp Gijon":            "Sporting Gijon",
    "Villarreal B":        "Villarreal II",
    "Sociedad B":          "Real Sociedad II",
    # Netherlands
    "For Sittard":         "Fortuna Sittard",
    # Belgium
    "St Truiden":          "St. Truiden",
    "RAAL La Louviere":    "RAAL La Louvière",
    "Club Brugge":         "Club Brugge KV",            # 241 FD rows
    "Standard":            "Standard Liege",            # 240 FD rows
    # Portugal
    "Sp Braga":            "SC Braga",
    "Sp Lisbon":           "Sporting CP",
    # Turkey
    "Ad. Demirspor":       "Adana Demirspor",
    "Karagumruk":          "Fatih Karagümrük",
    # France
    "St Etienne":          "Saint-Étienne",
    "Paris SG":            "Paris Saint Germain",       # 247 FD rows
    # England Championship/L1/L2 — DB uses short names; only add if FD-side abbrev differs
    "Peterboro":           "Peterborough",              # 311 FD rows
    # TIER-C-ALIAS-NEXT-BATCH (2026-05-25): second-round audit on remaining 9K
    # unmatched. Each verified against `SELECT name FROM teams WHERE name = ...`.
    "Kasimpasa":           "Kasımpaşa",                 # 256 FD rows
    "Buyuksehyr":          "Başakşehir",                # 256
    "Troyes":              "Estac Troyes",              # 251
    "Brest":               "Stade Brestois 29",         # 248
    "AEK":                 "AEK Athens FC",             # 242
    "Hoffenheim":          "1899 Hoffenheim",           # 238 (overrides Tier-C first batch — DB has '1899 Hoffenheim' not 'TSG Hoffenheim')
    "Nurnberg":            "1. FC Nürnberg",            # 238
    "Mainz":               "FSV Mainz 05",              # 238
    "Hamburg":             "Hamburger SV",              # 238
    "Bochum":              "Vfl Bochum",                # 238 (note lowercase 'fl' in DB)
    "Greuther Furth":      "SpVgg Greuther Fürth",      # 238
    "Darmstadt":           "SV Darmstadt 98",           # 238
    "Paderborn":           "SC Paderborn 07",           # 238
    "Porto":               "FC Porto",                  # 238
    "Gaziantep":           "Gaziantep FK",              # 222
    "Mechelen":            "KV Mechelen",               # 211
    "Cartagena":           "FC Cartagena",              # 210
    "Regensburg":          "SSV Jahn Regensburg",       # 204
    "Karlsruhe":           "Karlsruher SC",             # 204
    "Zwolle":              "PEC Zwolle",                # 196
    "Sandhausen":          "SV Sandhausen",             # 170
    "Bielefeld":           "Arminia Bielefeld",         # 170
    "Santander":           "Racing Santander",          # 168
}


def normalize_team_name(name: str) -> str:
    """Lowercase, strip accents, strip common suffixes/prefixes for fuzzy match.

    Accent stripping is what makes football-data's ASCII names (Besiktas,
    Fenerbahce, Goztepe, Koln, Alcorcon) match our DB's diacritic forms
    (Beşiktaş, Fenerbahçe, Göztepe, Köln, Alcorcón) automatically without
    needing an alias entry per team.
    """
    import unicodedata
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    n = n.strip().lower()
    for suffix in (" fc", " sc", " cf", " ac", " bk", " if", " afc", " utd", " united"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def fetch_csv(code: str, season: str) -> pd.DataFrame:
    """Download football-data CSV for a league/season. Returns empty DataFrame on 404."""
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
    print(f"  GET {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compat)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"    (404 — no CSV for {code}/{season})")
            return pd.DataFrame()
        raise
    try:
        df = pd.read_csv(io.StringIO(raw), low_memory=False, dtype=str)
    except Exception as e:
        print(f"    parse error: {e}")
        return pd.DataFrame()
    return df


def parse_date(s: str) -> str | None:
    """football-data uses DD/MM/YY or DD/MM/YYYY → return YYYY-MM-DD."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_odd(v) -> float | None:
    """Float or None."""
    if v is None or pd.isna(v):
        return None
    try:
        f = float(v)
        if f <= 1.0 or f > 1000:
            return None
        return f
    except (ValueError, TypeError):
        return None


def load_db_teams(league_id: str) -> dict[str, str]:
    """Return {normalized_name: team_id} for teams that ever played in this league."""
    rows = execute_query(
        """
        SELECT DISTINCT t.id::text AS id, t.name
        FROM teams t
        WHERE t.id IN (
          SELECT home_team_id FROM matches WHERE league_id = %s UNION
          SELECT away_team_id FROM matches WHERE league_id = %s
        )
        """,
        [league_id, league_id],
    )
    return {normalize_team_name(r["name"]): r["id"] for r in rows}


def resolve_team(fd_name: str, db_teams_norm: dict[str, str]) -> str | None:
    """Map a football-data team name to our DB team_id.
    Strategy: 1) alias lookup, 2) exact normalized, 3) substring (unique only),
    4) rapidfuzz best match (≥85 score, ≥10 lead over runner-up)."""
    if not fd_name:
        return None
    alias = TEAM_ALIASES.get(fd_name, fd_name)
    norm = normalize_team_name(alias)
    if norm in db_teams_norm:
        return db_teams_norm[norm]
    candidates = [tid for k, tid in db_teams_norm.items() if norm and (norm in k or k in norm)]
    if len(candidates) == 1:
        return candidates[0]
    # Rapidfuzz fallback — handles short-name / accent / suffix variations
    try:
        from rapidfuzz import process, fuzz
        names = list(db_teams_norm.keys())
        results = process.extract(norm, names, scorer=fuzz.WRatio, limit=3)
        if results and results[0][1] >= 85:
            best_name, best_score, _ = results[0]
            runner_score = results[1][1] if len(results) > 1 else 0
            if best_score - runner_score >= 10:
                return db_teams_norm[best_name]
    except ImportError:
        pass
    return None


def find_match(date_iso: str, home_id: str, away_id: str, league_id: str) -> str | None:
    """Find our match row by (date ±1 day, home, away, league)."""
    rows = execute_query(
        """
        SELECT id::text AS id FROM matches
        WHERE league_id = %s
          AND home_team_id = %s::uuid
          AND away_team_id = %s::uuid
          AND ABS(EXTRACT(EPOCH FROM (date - %s::timestamptz)) / 86400) < 1.5
        ORDER BY ABS(EXTRACT(EPOCH FROM (date - %s::timestamptz))) LIMIT 1
        """,
        [league_id, home_id, away_id, date_iso, date_iso],
    )
    return rows[0]["id"] if rows else None


def lookup_league_id(label: str) -> str | None:
    cfg = LEAGUES[label]
    rows = execute_query(
        "SELECT id::text AS id FROM leagues WHERE name = %s AND country = %s LIMIT 1",
        [cfg["db_name"], cfg["country"]],
    )
    return rows[0]["id"] if rows else None


def existing_snapshot_keys(match_ids: list[str]) -> set:
    """Set of (match_id, bookmaker, market, selection, is_closing) already in odds_snapshots."""
    if not match_ids:
        return set()
    rows = execute_query(
        """
        SELECT match_id::text AS match_id, bookmaker, market, selection, is_closing
        FROM odds_snapshots WHERE match_id = ANY(%s::uuid[])
        """,
        [match_ids],
    )
    return {(r["match_id"], r["bookmaker"], r["market"], r["selection"], bool(r["is_closing"])) for r in rows}


# ── Per-row extraction ─────────────────────────────────────────────────────

def extract_odds_rows(row: pd.Series, match_id: str, kickoff_utc: str) -> list[dict]:
    """Pull every odds value from a football-data row that we want to ingest.
    Returns list of dicts suitable for INSERT INTO odds_snapshots.
    """
    out = []

    def add(bookmaker: str, market: str, selection: str, odds: float | None, is_closing: bool):
        if odds is None:
            return
        out.append({
            "match_id": match_id,
            "bookmaker": bookmaker,
            "market": market,
            "selection": selection,
            "odds": odds,
            "timestamp": kickoff_utc,    # CSV gives closing — timestamp = kickoff
            "is_closing": is_closing,
            "is_opening": False,
        })

    # Pinnacle 1x2 (prefer closing)
    psh = parse_odd(row.get("PSCH")) or parse_odd(row.get("PSH"))
    psd = parse_odd(row.get("PSCD")) or parse_odd(row.get("PSD"))
    psa = parse_odd(row.get("PSCA")) or parse_odd(row.get("PSA"))
    if psh and psd and psa:
        add("Pinnacle", "1x2", "home", psh, True)
        add("Pinnacle", "1x2", "draw", psd, True)
        add("Pinnacle", "1x2", "away", psa, True)

    # Bet365 1x2
    bh = parse_odd(row.get("B365CH")) or parse_odd(row.get("B365H"))
    bd = parse_odd(row.get("B365CD")) or parse_odd(row.get("B365D"))
    ba = parse_odd(row.get("B365CA")) or parse_odd(row.get("B365A"))
    if bh and bd and ba:
        add("Bet365", "1x2", "home", bh, True)
        add("Bet365", "1x2", "draw", bd, True)
        add("Bet365", "1x2", "away", ba, True)

    # OU 2.5 — Pinnacle closing or opening
    p_o25 = parse_odd(row.get("PC>2.5")) or parse_odd(row.get("P>2.5"))
    p_u25 = parse_odd(row.get("PC<2.5")) or parse_odd(row.get("P<2.5"))
    if p_o25 and p_u25:
        add("Pinnacle", "over_under_25", "over", p_o25, True)
        add("Pinnacle", "over_under_25", "under", p_u25, True)

    # OU 2.5 — Bet365
    b_o25 = parse_odd(row.get("B365C>2.5")) or parse_odd(row.get("B365>2.5"))
    b_u25 = parse_odd(row.get("B365C<2.5")) or parse_odd(row.get("B365<2.5"))
    if b_o25 and b_u25:
        add("Bet365", "over_under_25", "over", b_o25, True)
        add("Bet365", "over_under_25", "under", b_u25, True)

    return out


# ── Main ───────────────────────────────────────────────────────────────────

def ingest_league_season(league_code: str, season: str, dry_run: bool):
    cfg = LEAGUES[league_code]
    league_id = lookup_league_id(league_code)
    if not league_id:
        print(f"  [skip] {cfg['db_name']} ({cfg['country']}) not in DB leagues table")
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}

    df = fetch_csv(league_code, season)
    if df.empty:
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}

    db_teams_norm = load_db_teams(league_id)
    print(f"    {len(df):,} CSV rows / {len(db_teams_norm)} known teams in this league")

    # Resolve matches
    parsed_rows = []
    unmatched_teams = set()
    for _, row in df.iterrows():
        d = parse_date(row.get("Date"))
        if not d:
            continue
        home_name = row.get("HomeTeam") or row.get("Home")
        away_name = row.get("AwayTeam") or row.get("Away")
        home_id = resolve_team(home_name, db_teams_norm)
        away_id = resolve_team(away_name, db_teams_norm)
        if not home_id:
            unmatched_teams.add(home_name)
            continue
        if not away_id:
            unmatched_teams.add(away_name)
            continue
        kickoff_utc = f"{d}T15:00:00+00:00"
        match_id = find_match(kickoff_utc, home_id, away_id, league_id)
        if not match_id:
            continue
        parsed_rows.append((row, match_id, kickoff_utc))

    matched = len(parsed_rows)
    print(f"    Matched {matched} CSV rows to DB matches")
    if unmatched_teams:
        print(f"    Unmatched teams ({len(unmatched_teams)}): {sorted(unmatched_teams)[:10]}" +
              (f" …+{len(unmatched_teams)-10} more" if len(unmatched_teams) > 10 else ""))

    if not parsed_rows:
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": len(df)}

    match_ids = list({mid for _, mid, _ in parsed_rows})
    existing = existing_snapshot_keys(match_ids)

    to_insert = []
    skipped_existing = 0
    for row, match_id, kickoff in parsed_rows:
        for snap in extract_odds_rows(row, match_id, kickoff):
            key = (snap["match_id"], snap["bookmaker"], snap["market"], snap["selection"], snap["is_closing"])
            if key in existing:
                skipped_existing += 1
                continue
            to_insert.append(snap)

    print(f"    Would insert {len(to_insert):,} odds rows (skipped {skipped_existing} already-present)")

    if dry_run or not to_insert:
        return {"matched": matched, "inserted": 0, "skipped_existing": skipped_existing, "no_match": len(df) - matched}

    # Bulk insert
    from psycopg2.extras import execute_values
    from workers.api_clients.db import _pool

    with _pool.getconn() as conn:
        try:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO odds_snapshots
                      (match_id, bookmaker, market, selection, odds, timestamp, is_closing, is_opening)
                    VALUES %s
                    """,
                    [
                        (s["match_id"], s["bookmaker"], s["market"], s["selection"],
                         s["odds"], s["timestamp"], s["is_closing"], s["is_opening"])
                        for s in to_insert
                    ],
                )
                conn.commit()
        finally:
            _pool.putconn(conn)
    return {"matched": matched, "inserted": len(to_insert), "skipped_existing": skipped_existing, "no_match": len(df) - matched}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="E0", help=f"League code: one of {','.join(LEAGUES)}")
    p.add_argument("--seasons", default="2425,2324,2526", help="Comma-separated seasons (e.g. 2425,2324)")
    p.add_argument("--all-leagues", action="store_true", help="Process every league in LEAGUES")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    leagues = list(LEAGUES) if args.all_leagues else [args.league]
    if not args.all_leagues and args.league not in LEAGUES:
        print(f"Unknown league code {args.league}. Available: {list(LEAGUES)}")
        sys.exit(1)

    grand = {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}
    for code in leagues:
        for season in seasons:
            print(f"\n== {LEAGUES[code]['label']} ({code}) — {season} ==")
            r = ingest_league_season(code, season, args.dry_run)
            for k in grand:
                grand[k] += r[k]

    print()
    print("=" * 70)
    print(f"TOTAL: matched={grand['matched']:,} | inserted={grand['inserted']:,} "
          f"| skipped_existing={grand['skipped_existing']:,} | no_db_match={grand['no_match']:,}")


if __name__ == "__main__":
    main()
