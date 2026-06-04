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


# CSV-FULL-EXTRACT (2026-06-04): on-disk path so the 479 already-downloaded
# CSVs can be re-ingested without re-hitting the network.
_DISK_ROOT = Path(__file__).resolve().parent.parent / "data" / "raw" / "football_data_co_uk" / "main"


def list_disk_seasons(code: str) -> list[str]:
    d = _DISK_ROOT / code
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.csv"))


def fetch_csv(code: str, season: str, from_disk: bool = False) -> pd.DataFrame:
    """Load a football-data CSV. With `from_disk=True`, read from the local
    mirror at data/raw/football_data_co_uk/main/<CODE>/<season>.csv instead
    of downloading. Returns empty DataFrame if not present."""
    if from_disk:
        path = _DISK_ROOT / code / f"{season}.csv"
        if not path.is_file():
            print(f"    (disk miss — no local CSV for {code}/{season})")
            return pd.DataFrame()
        print(f"  READ {path}")
        try:
            return pd.read_csv(path, low_memory=False, dtype=str, encoding="utf-8", on_bad_lines="skip")
        except Exception as e:
            print(f"    parse error: {e}")
            return pd.DataFrame()
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


# CSV-FULL-EXTRACT (2026-06-04): bulk pre-load + in-memory match lookup.
# Replaces 380-552 DB queries per league-season with 1, ~150-200x speedup.
def load_all_matches_in_league(league_id: str) -> dict[tuple[str, str], list[tuple]]:
    """{(home_team_id, away_team_id): [(date, match_id), ...]} for league."""
    rows = execute_query(
        """
        SELECT id::text AS id, home_team_id::text AS h, away_team_id::text AS a, date
        FROM matches WHERE league_id = %s::uuid
        """,
        [league_id],
    )
    out: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        out.setdefault((r["h"], r["a"]), []).append((r["date"], r["id"]))
    return out


def find_match_in_memory(date_iso: str, home_id: str, away_id: str,
                         matches_by_teams: dict[tuple[str, str], list[tuple]]) -> str | None:
    candidates = matches_by_teams.get((home_id, away_id), [])
    if not candidates:
        return None
    from datetime import datetime as _dt, timezone as _tz
    target = _dt.fromisoformat(date_iso.replace("Z", "+00:00"))
    if target.tzinfo is None:
        target = target.replace(tzinfo=_tz.utc)
    best: tuple[float, str] | None = None
    for cand_date, cand_id in candidates:
        cand_dt = cand_date if cand_date.tzinfo else cand_date.replace(tzinfo=_tz.utc)
        diff_days = abs((cand_dt - target).total_seconds() / 86400)
        if diff_days >= 1.5:
            continue
        if best is None or diff_days < best[0]:
            best = (diff_days, cand_id)
    return best[1] if best else None


def lookup_league_id(label: str) -> str | None:
    cfg = LEAGUES[label]
    rows = execute_query(
        "SELECT id::text AS id FROM leagues WHERE name = %s AND country = %s LIMIT 1",
        [cfg["db_name"], cfg["country"]],
    )
    return rows[0]["id"] if rows else None


def existing_snapshot_keys(match_ids: list[str], bookmakers: list[str] | None = None) -> set:
    """Set of (match_id, bookmaker, market, selection, is_closing, is_opening, handicap_line)
    already in odds_snapshots. When `bookmakers` is provided, the SELECT is
    scoped to those names only — dramatically smaller result for the
    CSV-FULL-EXTRACT case (we know exactly which 9 bookmakers we write).
    """
    if not match_ids:
        return set()
    if bookmakers:
        rows = execute_query(
            """
            SELECT match_id::text AS match_id, bookmaker, market, selection,
                   is_closing, is_opening, handicap_line
            FROM odds_snapshots
            WHERE match_id = ANY(%s::uuid[])
              AND bookmaker = ANY(%s::text[])
            """,
            [match_ids, bookmakers],
        )
    else:
        rows = execute_query(
            """
            SELECT match_id::text AS match_id, bookmaker, market, selection,
                   is_closing, is_opening, handicap_line
            FROM odds_snapshots WHERE match_id = ANY(%s::uuid[])
            """,
            [match_ids],
        )
    return {(r["match_id"], r["bookmaker"], r["market"], r["selection"],
             bool(r["is_closing"]), bool(r["is_opening"]),
             float(r["handicap_line"]) if r["handicap_line"] is not None else None)
            for r in rows}




# ── Per-row extraction ─────────────────────────────────────────────────────

# CSV-FULL-EXTRACT (2026-06-04): per-bookmaker column prefixes for the
# 1X2 / OU / AH triplet, closing + opening. football-data is inconsistent —
# Pinnacle 1X2 uses PSC/PS but Pinnacle OU/AH drops the S → PC/P. Every other
# book is consistent across markets. We model each market explicitly.
#
# Tuple shape per book:
#   (name,
#    1x2_close_pref,  1x2_open_pref,   # e.g. PSC + PS
#    ou_close_pref,   ou_open_pref,    # e.g. PC + P  (None ou_close = no OU)
#    ah_close_pref,   ah_open_pref)    # e.g. PC + P  (None ah_close = no AH)
_BOOKMAKERS_1X2_OU_AH: list[tuple[str, str | None, str | None, str | None, str | None, str | None, str | None]] = [
    ("Pinnacle",          "PSC",  "PS",  "PC",   "P",   "PC",   "P"),
    ("Bet365",            "B365C","B365","B365C","B365","B365C","B365"),
    ("Betfair Exchange",  "BFEC", "BFE", "BFEC", "BFE", "BFEC", "BFE"),
    ("BetWin",            "BWC",  "BW",  None,   None,  None,   None),
    ("Betfred",           "BFC",  "BF",  None,   None,  None,   None),
    ("William Hill",      "WHC",  "WH",  None,   None,  None,   None),
    ("1xBet",             "1XBC", "1XB", None,   None,  None,   None),
    ("Max",               "MaxC", None,  "MaxC", None,  "MaxC", None),
    ("Avg",               "AvgC", None,  "AvgC", None,  "AvgC", None),
]


def extract_odds_rows(row: pd.Series, match_id: str, kickoff_utc: str) -> list[dict]:
    """Pull every odds value from a football-data row we want to ingest.

    Captures, for each main-league CSV row:
      • 1X2 closing + opening across 9 bookmakers (where columns exist)
      • OU 2.5 closing + opening across Pinnacle / Bet365 / Exchange / Max / Avg
      • Asian Handicap closing (with AHCh line) + opening (with AHh line)
        across Pinnacle / Bet365 / Exchange / Max / Avg

    Closing rows: timestamp = kickoff_utc, is_closing=True, is_opening=False.
    Opening rows: timestamp = kickoff_utc minus ~7d (pre-kickoff stand-in,
    real opening time isn't in the CSV), is_closing=False, is_opening=True.
    """
    out: list[dict] = []

    # Pre-kickoff opening timestamp — football-data doesn't carry the real
    # opening timestamp; use kickoff − 7d so downstream code can still join
    # by match_id without confusion. is_opening flag is the source of truth.
    from datetime import datetime, timedelta, timezone
    kickoff_dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    opening_ts = (kickoff_dt - timedelta(days=7)).isoformat()

    def add(bookmaker: str, market: str, selection: str, odds: float | None,
            is_closing: bool, is_opening: bool, handicap_line: float | None = None):
        if odds is None:
            return
        out.append({
            "match_id": match_id,
            "bookmaker": bookmaker,
            "market": market,
            "selection": selection,
            "odds": odds,
            "timestamp": kickoff_utc if is_closing else opening_ts,
            "is_closing": is_closing,
            "is_opening": is_opening,
            "handicap_line": handicap_line,
        })

    # ── 1X2 + OU 2.5 + AH closing+opening across all configured bookmakers ─
    ah_close_line = parse_odd(row.get("AHCh"))   # closing handicap line
    ah_open_line  = parse_odd(row.get("AHh"))    # opening handicap line
    # Negative handicaps are legitimate (home favoured); parse_odd rejects ≤1.0.
    # Re-parse handicap line with looser bounds.
    def _parse_line(v):
        try:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None
    ah_close_line = _parse_line(row.get("AHCh"))
    ah_open_line  = _parse_line(row.get("AHh"))

    for (name, p_1x2_c, p_1x2_o, p_ou_c, p_ou_o, p_ah_c, p_ah_o) in _BOOKMAKERS_1X2_OU_AH:
        # 1X2 closing
        if p_1x2_c:
            h = parse_odd(row.get(f"{p_1x2_c}H"))
            d = parse_odd(row.get(f"{p_1x2_c}D"))
            a = parse_odd(row.get(f"{p_1x2_c}A"))
            if h and d and a:
                add(name, "1x2", "home", h, True, False)
                add(name, "1x2", "draw", d, True, False)
                add(name, "1x2", "away", a, True, False)
        # 1X2 opening
        if p_1x2_o:
            h = parse_odd(row.get(f"{p_1x2_o}H"))
            d = parse_odd(row.get(f"{p_1x2_o}D"))
            a = parse_odd(row.get(f"{p_1x2_o}A"))
            if h and d and a:
                add(name, "1x2", "home", h, False, True)
                add(name, "1x2", "draw", d, False, True)
                add(name, "1x2", "away", a, False, True)
        # OU 2.5 closing
        if p_ou_c:
            o = parse_odd(row.get(f"{p_ou_c}>2.5"))
            u = parse_odd(row.get(f"{p_ou_c}<2.5"))
            if o and u:
                add(name, "over_under_25", "over",  o, True, False)
                add(name, "over_under_25", "under", u, True, False)
        # OU 2.5 opening
        if p_ou_o:
            o = parse_odd(row.get(f"{p_ou_o}>2.5"))
            u = parse_odd(row.get(f"{p_ou_o}<2.5"))
            if o and u:
                add(name, "over_under_25", "over",  o, False, True)
                add(name, "over_under_25", "under", u, False, True)
        # AH closing — requires the AHCh closing-line column to be present
        if p_ah_c and ah_close_line is not None:
            ahh = parse_odd(row.get(f"{p_ah_c}AHH"))
            aha = parse_odd(row.get(f"{p_ah_c}AHA"))
            if ahh and aha:
                add(name, "asian_handicap", "home", ahh, True, False, ah_close_line)
                add(name, "asian_handicap", "away", aha, True, False, ah_close_line)
        # AH opening — requires the AHh opening-line column to be present
        if p_ah_o and ah_open_line is not None:
            ahh = parse_odd(row.get(f"{p_ah_o}AHH"))
            aha = parse_odd(row.get(f"{p_ah_o}AHA"))
            if ahh and aha:
                add(name, "asian_handicap", "home", ahh, False, True, ah_open_line)
                add(name, "asian_handicap", "away", aha, False, True, ah_open_line)

    return out


def extract_match_stats(row: pd.Series, match_id: str) -> dict | None:
    """Extract secondary match stats (shots, SoT, corners, fouls, cards) from
    a main-league CSV row. Returns dict for INSERT INTO match_stats ...
    ON CONFLICT DO UPDATE — only fills columns that are currently NULL.

    Extras CSVs (/new/<COUNTRY>.csv) do NOT carry these stats; this function
    returns None when the row is from an extras CSV.
    """
    def _int(v):
        try:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            return int(float(v))
        except (TypeError, ValueError):
            return None

    hs  = _int(row.get("HS"));   as_ = _int(row.get("AS"))
    hst = _int(row.get("HST"));  ast = _int(row.get("AST"))
    hc  = _int(row.get("HC"));   ac  = _int(row.get("AC"))
    hy  = _int(row.get("HY"));   ay  = _int(row.get("AY"))
    hr  = _int(row.get("HR"));   ar  = _int(row.get("AR"))
    hf  = _int(row.get("HF"));   af  = _int(row.get("AF"))

    # Bail if nothing meaningful is present (extras CSV).
    if all(v is None for v in (hs, as_, hst, ast, hc, ac, hy, ay, hr, ar, hf, af)):
        return None

    return {
        "match_id": match_id,
        "shots_home": hs, "shots_away": as_,
        "shots_on_target_home": hst, "shots_on_target_away": ast,
        "corners_home": hc, "corners_away": ac,
        "yellow_cards_home": hy, "yellow_cards_away": ay,
        "red_cards_home": hr, "red_cards_away": ar,
        "fouls_home": hf, "fouls_away": af,
        # Legacy duplicate columns kept in sync — some reads still use these.
        "yellows_home": hy, "yellows_away": ay,
        "reds_home": hr, "reds_away": ar,
    }


# ── Main ───────────────────────────────────────────────────────────────────

def ingest_league_season(league_code: str, season: str, dry_run: bool, from_disk: bool = False):
    cfg = LEAGUES[league_code]
    league_id = lookup_league_id(league_code)
    if not league_id:
        print(f"  [skip] {cfg['db_name']} ({cfg['country']}) not in DB leagues table")
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}

    df = fetch_csv(league_code, season, from_disk=from_disk)
    if df.empty:
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}

    db_teams_norm = load_db_teams(league_id)
    matches_by_teams = load_all_matches_in_league(league_id)
    print(f"    {len(df):,} CSV rows / {len(db_teams_norm)} known teams / "
          f"{sum(len(v) for v in matches_by_teams.values())} DB matches indexed")

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
        match_id = find_match_in_memory(kickoff_utc, home_id, away_id, matches_by_teams)
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
    # Scope dedup query to only the bookmakers this script writes — cuts the
    # SELECT result from ~30-50K rows (all AF-live + CSV bookmakers) to just
    # the 9 CSV bookmakers' existing rows.
    csv_bookmakers = [name for name, *_ in _BOOKMAKERS_1X2_OU_AH]
    existing = existing_snapshot_keys(match_ids, bookmakers=csv_bookmakers)

    to_insert: list[dict] = []
    stats_to_upsert: list[dict] = []
    referee_to_set: list[tuple[str, str]] = []   # (match_id, referee)
    skipped_existing = 0
    for row, match_id, kickoff in parsed_rows:
        for snap in extract_odds_rows(row, match_id, kickoff):
            hl = snap.get("handicap_line")
            key = (snap["match_id"], snap["bookmaker"], snap["market"],
                   snap["selection"], snap["is_closing"], snap["is_opening"],
                   float(hl) if hl is not None else None)
            if key in existing:
                skipped_existing += 1
                continue
            to_insert.append(snap)
        stats = extract_match_stats(row, match_id)
        if stats:
            stats_to_upsert.append(stats)
        ref = row.get("Referee")
        if isinstance(ref, str) and ref.strip():
            referee_to_set.append((match_id, ref.strip()))

    print(f"    Would insert {len(to_insert):,} odds rows "
          f"(skipped {skipped_existing} already-present); "
          f"{len(stats_to_upsert)} match_stats upserts; "
          f"{len(referee_to_set)} referee updates")

    if dry_run:
        return {"matched": matched, "inserted": 0,
                "skipped_existing": skipped_existing,
                "no_match": len(df) - matched}

    # CSV-FULL-EXTRACT perf: single pinned connection across all three writes,
    # COPY for the big odds_snapshots load, execute_values for the smaller
    # ON CONFLICT / UPDATE FROM VALUES paths. Saves 2 pool round-trips and
    # gets ~3-5x throughput on the odds payload vs execute_values.
    import io as _io
    from psycopg2.extras import execute_values
    from workers.api_clients.db import _pool

    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            # ── odds_snapshots via COPY ─────────────────────────────────
            if to_insert:
                buf = _io.StringIO()
                for s in to_insert:
                    hl = s.get("handicap_line")
                    # tab-separated, \N for NULL — escape any \\t/\\n that could
                    # accidentally appear (none of our string values can, but
                    # be safe).
                    fields = [
                        s["match_id"],
                        s["bookmaker"].replace("\t", " ").replace("\n", " "),
                        s["market"],
                        s["selection"],
                        f"{s['odds']:.4f}",
                        s["timestamp"],
                        "t" if s["is_closing"] else "f",
                        "t" if s["is_opening"] else "f",
                        f"{hl:.2f}" if hl is not None else "\\N",
                    ]
                    buf.write("\t".join(fields) + "\n")
                buf.seek(0)
                cur.copy_expert(
                    """
                    COPY odds_snapshots
                      (match_id, bookmaker, market, selection, odds,
                       timestamp, is_closing, is_opening, handicap_line)
                    FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')
                    """,
                    buf,
                )

            # ── match_stats UPSERT via execute_values ──────────────────
            if stats_to_upsert:
                cols = [
                    "shots_home", "shots_away",
                    "shots_on_target_home", "shots_on_target_away",
                    "corners_home", "corners_away",
                    "yellow_cards_home", "yellow_cards_away",
                    "red_cards_home", "red_cards_away",
                    "fouls_home", "fouls_away",
                    "yellows_home", "yellows_away",
                    "reds_home", "reds_away",
                ]
                col_list = ", ".join(["match_id"] + cols)
                on_conflict_set = ", ".join(
                    f"{c} = COALESCE(match_stats.{c}, EXCLUDED.{c})" for c in cols
                )
                execute_values(
                    cur,
                    f"""
                    INSERT INTO match_stats ({col_list})
                    VALUES %s
                    ON CONFLICT (match_id) DO UPDATE SET {on_conflict_set}
                    """,
                    [tuple([s["match_id"]] + [s.get(c) for c in cols]) for s in stats_to_upsert],
                )

            # ── matches.referee bulk UPDATE FROM VALUES ────────────────
            if referee_to_set:
                execute_values(
                    cur,
                    """
                    UPDATE matches SET referee = data.ref
                    FROM (VALUES %s) AS data(mid, ref)
                    WHERE matches.id = data.mid::uuid
                      AND matches.referee IS NULL
                    """,
                    [(m, r) for m, r in referee_to_set],
                )

            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)

    return {"matched": matched, "inserted": len(to_insert),
            "skipped_existing": skipped_existing,
            "no_match": len(df) - matched}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="E0", help=f"League code: one of {','.join(LEAGUES)}")
    p.add_argument("--seasons", default="2425,2324,2526", help="Comma-separated seasons (e.g. 2425,2324)")
    p.add_argument("--all-leagues", action="store_true", help="Process every league in LEAGUES")
    p.add_argument("--all-seasons", action="store_true",
                   help="Discover seasons from disk (requires --from-disk); overrides --seasons")
    p.add_argument("--from-disk", action="store_true",
                   help="Read CSVs from data/raw/football_data_co_uk/main/<CODE>/<season>.csv instead of HTTP")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    leagues = list(LEAGUES) if args.all_leagues else [args.league]
    if not args.all_leagues and args.league not in LEAGUES:
        print(f"Unknown league code {args.league}. Available: {list(LEAGUES)}")
        sys.exit(1)

    grand = {"matched": 0, "inserted": 0, "skipped_existing": 0, "no_match": 0}
    for code in leagues:
        if args.all_seasons:
            if not args.from_disk:
                print("--all-seasons requires --from-disk"); sys.exit(2)
            seasons = list_disk_seasons(code)
            if not seasons:
                print(f"\n== {LEAGUES[code]['label']} ({code}) — no disk seasons =="); continue
        else:
            seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
        for season in seasons:
            print(f"\n== {LEAGUES[code]['label']} ({code}) — {season} ==")
            r = ingest_league_season(code, season, args.dry_run, from_disk=args.from_disk)
            for k in grand:
                grand[k] += r[k]

    print()
    print("=" * 70)
    print(f"TOTAL: matched={grand['matched']:,} | inserted={grand['inserted']:,} "
          f"| skipped_existing={grand['skipped_existing']:,} | no_db_match={grand['no_match']:,}")


if __name__ == "__main__":
    main()
