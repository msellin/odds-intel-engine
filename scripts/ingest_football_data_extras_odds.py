"""
TIER-C-EXPAND follow-up — ingest closing odds from football-data.co.uk's
new/ extras CSVs into `odds_snapshots` for the 14 countries we just added.

Why this exists: TIER-C-EXPAND added team-form history to
targets_poisson_history.csv for ARG/AUT/BRA/CHN/DNK/FIN/IRL/JPN/MEX/NOR/POL/
RUS/SWE/USA. But the training pipeline reads from the DB, not the CSV:
- predict_historical_matches.py needs odds_snapshots
- Sunday Platt retrain needs (predicted_prob, closing_implied, outcome)

`backfill_historical.py` (May 2026) already pulled ~13K historical matches
for these countries from API-Football into the `matches` table, including
match_stats and events — but skipped odds (`odds_done: 0` on every progress
row, because AF doesn't serve historical pre-match odds reliably). The
football-data /new/ extras CSVs contain PSCH/D/A (Pinnacle closing) and
B365CH/D/A (Bet365 closing) — exactly the gap.

This script joins the football-data rows to existing DB matches by
(league + team-name + date ±1.5d) and writes closing-odds rows. Reuses
the resolve_team / find_match / existing_snapshot_keys / extract_odds_rows
helpers from scripts/ingest_football_data_csvs.py so team-name aliasing
and dedupe behave identically.

After this lands, ~10K-13K historical matches qualify for
`predict_historical_matches.py`, which then feeds the backtest + Platt
retrain.

Run:
  python3 scripts/ingest_football_data_extras_odds.py                   # all 14
  python3 scripts/ingest_football_data_extras_odds.py --league USA      # one
  python3 scripts/ingest_football_data_extras_odds.py --dry-run         # no writes
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

# Reuse the proven helpers from the mainstream ingest script.
from scripts.ingest_football_data_csvs import (  # noqa: E402
    resolve_team,
    find_match,
    existing_snapshot_keys,
    extract_odds_rows,
    load_db_teams,
    parse_date,
)

EXTRAS_URL = "https://www.football-data.co.uk/new/{code}.csv"

# Maps football-data /new/ 3-letter code → DB filter for the matches table.
# `country` is the leagues.country value we filter on.
# `league_names` is a list of leagues.name values we accept (some countries
# have multiple top divisions in our DB — e.g. Argentina has both Liga
# Profesional and Primera Nacional; football-data /new/ARG.csv mixes them).
EXTRAS: dict[str, dict] = {
    "ARG": {"country": "Argentina",      "league_names": ["Liga Profesional Argentina", "Primera Nacional"], "label": "Argentina"},
    "AUT": {"country": "Austria",        "league_names": ["Bundesliga"], "label": "Austria"},
    "BRA": {"country": "Brazil",         "league_names": ["Serie A", "Serie B"], "label": "Brazil"},
    "CHN": {"country": "China",          "league_names": ["Super League"], "label": "China"},
    "DNK": {"country": "Denmark",        "league_names": ["Superliga"], "label": "Denmark"},
    "FIN": {"country": "Finland",        "league_names": ["Veikkausliiga"], "label": "Finland"},
    "IRL": {"country": "Ireland",        "league_names": ["Premier Division"], "label": "Ireland"},
    "JPN": {"country": "Japan",          "league_names": ["J1 League"], "label": "Japan"},
    "MEX": {"country": "Mexico",         "league_names": ["Liga MX"], "label": "Mexico"},
    "NOR": {"country": "Norway",         "league_names": ["Eliteserien"], "label": "Norway"},
    "POL": {"country": "Poland",         "league_names": ["Ekstraklasa"], "label": "Poland"},
    "RUS": {"country": "Russia",         "league_names": ["Premier League"], "label": "Russia"},
    "SWE": {"country": "Sweden",         "league_names": ["Allsvenskan"], "label": "Sweden"},
    "USA": {"country": "USA",            "league_names": ["Major League Soccer"], "label": "USA / MLS"},
}


def fetch_extras_csv(code: str) -> pd.DataFrame:
    url = EXTRAS_URL.format(code=code)
    print(f"  Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), low_memory=False)
    print(f"  Downloaded {len(df):,} rows, {len(df.columns)} columns")
    return df


def find_league_ids(country: str, league_names: list[str]) -> dict[str, str]:
    """Return {league_name: league_id} for the candidate league names in
    this country. The /new/ extras CSVs aren't pre-segmented by division,
    so we try matching against each candidate when looking up the match."""
    rows = execute_query(
        """
        SELECT id::text AS id, name FROM leagues
        WHERE country = %s AND name = ANY(%s::text[])
        """,
        [country, league_names],
    )
    return {r["name"]: r["id"] for r in rows}


def load_all_matches_for_leagues(candidate_league_ids: list[str]) -> dict[tuple[str, str], list[tuple]]:
    """Bulk-load every match for the candidate leagues into memory keyed by
    (home_team_id, away_team_id) → list of (date, match_id).

    Replaces a per-row find_match() DB query (~150ms × thousands of rows on the
    EU pooler) with one SELECT plus in-memory lookup. The CSV match → DB row
    join now runs at memory speed instead of network speed.
    """
    if not candidate_league_ids:
        return {}
    rows = execute_query(
        """
        SELECT id::text AS id, home_team_id::text AS h, away_team_id::text AS a, date
        FROM matches WHERE league_id = ANY(%s::uuid[])
        """,
        [candidate_league_ids],
    )
    out: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        key = (r["h"], r["a"])
        out.setdefault(key, []).append((r["date"], r["id"]))
    return out


def find_match_in_memory(date_iso: str, home_id: str, away_id: str,
                         matches_by_teams: dict[tuple[str, str], list[tuple]]) -> str | None:
    """Find the closest DB match by (home, away, date ±1.5d) using the
    in-memory index built by load_all_matches_for_leagues()."""
    candidates = matches_by_teams.get((home_id, away_id), [])
    if not candidates:
        return None
    from datetime import datetime, timezone
    target = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    best: tuple[float, str] | None = None
    for cand_date, cand_id in candidates:
        # cand_date is datetime from psycopg2 (timezone-aware if column is timestamptz)
        if cand_date.tzinfo is None:
            cand_dt = cand_date.replace(tzinfo=timezone.utc)
        else:
            cand_dt = cand_date
        diff_days = abs((cand_dt - target).total_seconds() / 86400)
        if diff_days >= 1.5:
            continue
        if best is None or diff_days < best[0]:
            best = (diff_days, cand_id)
    return best[1] if best else None


def ingest_one(code: str, cfg: dict, dry_run: bool) -> dict:
    print(f"\n=== {code} — {cfg['label']} ===")
    league_id_map = find_league_ids(cfg["country"], cfg["league_names"])
    if not league_id_map:
        print(f"  [skip] No matching leagues in DB for country={cfg['country']!r}, "
              f"names={cfg['league_names']}")
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "unmatched_teams": 0, "no_match": 0}
    print(f"  Resolved {len(league_id_map)} DB league(s): {list(league_id_map.keys())}")

    # Load all teams across the candidate leagues for fuzzy matching.
    candidate_league_ids = list(league_id_map.values())
    db_teams_norm: dict[str, str] = {}
    for lid in candidate_league_ids:
        db_teams_norm.update(load_db_teams(lid))
    print(f"  {len(db_teams_norm)} unique teams across candidate leagues")

    # Bulk-load all DB matches for the candidate leagues once. Avoids per-row
    # DB queries in the join loop (~150ms × thousands of rows on the EU pooler).
    matches_by_teams = load_all_matches_for_leagues(candidate_league_ids)
    print(f"  {sum(len(v) for v in matches_by_teams.values())} DB matches loaded into memory across {len(matches_by_teams)} team-pairs")

    try:
        df = fetch_extras_csv(code)
    except Exception as e:
        print(f"  DOWNLOAD FAILED: {type(e).__name__}: {e}")
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "unmatched_teams": 0, "no_match": 0}

    # Normalise column probes — the /new/ extras use Home/Away/HG/AG/Date.
    if "Home" not in df.columns or "Away" not in df.columns or "Date" not in df.columns:
        print(f"  [skip] Required columns missing from {code}.csv (got {list(df.columns)[:10]})")
        return {"matched": 0, "inserted": 0, "skipped_existing": 0, "unmatched_teams": 0, "no_match": 0}

    parsed_rows: list[tuple] = []
    unmatched_teams: set[str] = set()
    no_match_skipped = 0
    for _, row in df.iterrows():
        d = parse_date(row.get("Date"))
        if not d:
            continue
        home_name = row.get("Home")
        away_name = row.get("Away")
        home_id = resolve_team(home_name, db_teams_norm)
        away_id = resolve_team(away_name, db_teams_norm)
        if not home_id:
            unmatched_teams.add(str(home_name))
            continue
        if not away_id:
            unmatched_teams.add(str(away_name))
            continue
        kickoff_utc = f"{d}T15:00:00+00:00"
        match_id = find_match_in_memory(kickoff_utc, home_id, away_id, matches_by_teams)
        if not match_id:
            no_match_skipped += 1
            continue
        parsed_rows.append((row, match_id, kickoff_utc))

    matched = len(parsed_rows)
    print(f"  Matched {matched} CSV rows to DB matches "
          f"(no-team={len(unmatched_teams)} unique, no-DB-match={no_match_skipped})")
    if unmatched_teams:
        sample = sorted(unmatched_teams)[:10]
        print(f"  Unmatched team names (sample): {sample}")

    if not parsed_rows:
        return {"matched": 0, "inserted": 0, "skipped_existing": 0,
                "unmatched_teams": len(unmatched_teams), "no_match": no_match_skipped}

    match_ids = list({mid for _, mid, _ in parsed_rows})
    existing = existing_snapshot_keys(match_ids)

    to_insert: list[dict] = []
    skipped_existing = 0
    for row, match_id, kickoff in parsed_rows:
        for snap in extract_odds_rows(row, match_id, kickoff):
            key = (snap["match_id"], snap["bookmaker"], snap["market"], snap["selection"], snap["is_closing"])
            if key in existing:
                skipped_existing += 1
                continue
            to_insert.append(snap)

    print(f"  Would insert {len(to_insert):,} odds rows "
          f"(skipped {skipped_existing} already-present)")

    if dry_run or not to_insert:
        return {"matched": matched, "inserted": 0, "skipped_existing": skipped_existing,
                "unmatched_teams": len(unmatched_teams), "no_match": no_match_skipped}

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

    return {"matched": matched, "inserted": len(to_insert), "skipped_existing": skipped_existing,
            "unmatched_teams": len(unmatched_teams), "no_match": no_match_skipped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="Single 3-letter code (e.g. USA). Default = all.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.league:
        if args.league not in EXTRAS:
            print(f"ERROR: --league {args.league} not in config. Available: {sorted(EXTRAS)}")
            sys.exit(2)
        codes = [args.league]
    else:
        codes = sorted(EXTRAS)
        print(f"Running full batch: {codes}")

    grand = {"matched": 0, "inserted": 0, "skipped_existing": 0, "unmatched_teams": 0, "no_match": 0}
    for code in codes:
        r = ingest_one(code, EXTRAS[code], args.dry_run)
        for k in grand:
            grand[k] += r.get(k, 0)

    print()
    print("=" * 70)
    print(f"TOTAL: matched={grand['matched']:,} | inserted={grand['inserted']:,} "
          f"| skipped_existing={grand['skipped_existing']:,} | no_db_match={grand['no_match']:,} "
          f"| unmatched_teams={grand['unmatched_teams']}")


if __name__ == "__main__":
    main()
