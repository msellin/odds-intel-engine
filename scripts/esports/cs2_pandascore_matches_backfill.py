"""
CS2 PandaScore match-history backfill.

bo3.gg only covers tier-1/2 + some tier-3 matches. PandaScore covers a wider
swath including qualifiers, amateur leagues, and regional series. The Oxuji
incident (2026-06-09) revealed this: our bot priced a team with 0 matches in
DB while PandaScore had 5+ recent finished matches for them.

This script paginates /csgo/matches/past (and optionally /upcoming) and
UPSERTs into cs2_pandascore_matches. Idempotent — re-running is safe. Stops
when (a) it hits a previously-seen pandascore_id, or (b) hits the `--since`
date cutoff, or (c) exhausts pagination.

Rate limits: PandaScore free tier = 1000 req/hr. 100 matches/page → can pull
~100k matches/hr. We sleep 1s between pages anyway to be polite.

Run:
    python3 scripts/esports/cs2_pandascore_matches_backfill.py [--pages 200] [--since 2025-01-01]
    python3 scripts/esports/cs2_pandascore_matches_backfill.py --upcoming   # also pull scheduled
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
try:
    from scraper_state import scraper_run
except ImportError:
    scraper_run = None  # type: ignore


API_KEY = os.getenv("PANDASCORE_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
BASE = "https://api.pandascore.co/csgo"
RATE_DELAY = 1.0   # 1s between page requests — polite
PER_PAGE = 100


def _derive_winner(opponents: list, winner_id) -> tuple[str | None, int | None, int | None]:
    """Return (winner_label, score1, score2) from PandaScore opponents+winner_id."""
    if len(opponents) != 2:
        return None, None, None
    t1_id = (opponents[0].get("opponent") or {}).get("id")
    t2_id = (opponents[1].get("opponent") or {}).get("id")
    # results array is keyed by team_id
    score1 = score2 = None
    return _winner_label(t1_id, t2_id, winner_id), score1, score2


def _scores_from_results(results: list, t1_id, t2_id):
    s1 = s2 = None
    for r in results:
        tid = r.get("team_id")
        if tid == t1_id:
            s1 = r.get("score")
        elif tid == t2_id:
            s2 = r.get("score")
    return s1, s2


def _winner_label(t1_id, t2_id, winner_id):
    if winner_id is None:
        return None
    if winner_id == t1_id:
        return "team1"
    if winner_id == t2_id:
        return "team2"
    return None


def _existing_ids() -> set[int]:
    rows = execute_query("SELECT pandascore_id FROM cs2_pandascore_matches")
    return {r["pandascore_id"] for r in rows}


def fetch_page(endpoint: str, page: int) -> list[dict] | None:
    # CRITICAL: filter[status]=finished — without this the API returns mostly
    # 'canceled' matches (scheduled-but-never-played qualifiers) with NULL
    # begin_at/end_at, which are useless for modelling. Found 2,439/2,464
    # canceled matches on first naive backfill before adding this filter.
    status_filter = "&filter[status]=finished" if "past" in endpoint else ""
    url = f"{BASE}/{endpoint}?per_page={PER_PAGE}&page={page}&sort=-end_at{status_filter}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        print(f"  [!] {endpoint} page {page}: {e}", file=sys.stderr)
        return None
    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", "60"))
        print(f"  [!] 429 rate-limited; sleeping {retry_after}s", file=sys.stderr)
        time.sleep(retry_after)
        return fetch_page(endpoint, page)
    if not r.ok:
        print(f"  [!] {endpoint} page {page}: status {r.status_code}", file=sys.stderr)
        return None
    return r.json()


def upsert_match(m: dict) -> bool:
    opps = m.get("opponents") or []
    if len(opps) != 2:
        return False
    o1 = opps[0].get("opponent") or {}
    o2 = opps[1].get("opponent") or {}
    t1_id, t1_name = o1.get("id"), o1.get("name")
    t2_id, t2_name = o2.get("id"), o2.get("name")
    if not t1_name or not t2_name:
        return False

    results = m.get("results") or []
    s1, s2 = _scores_from_results(results, t1_id, t2_id)
    winner_id = m.get("winner_id")
    winner = _winner_label(t1_id, t2_id, winner_id)

    tournament_name = (m.get("tournament") or {}).get("name")
    serie_name = (m.get("serie") or {}).get("full_name") or (m.get("serie") or {}).get("name")

    execute_write("""
        INSERT INTO cs2_pandascore_matches
            (pandascore_id, team1_id, team1_name, team2_id, team2_name,
             score1, score2, winner, winner_id, best_of,
             begin_at, end_at, status, tournament_name, serie_name, league_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (pandascore_id) DO UPDATE SET
            score1 = EXCLUDED.score1, score2 = EXCLUDED.score2,
            winner = EXCLUDED.winner, winner_id = EXCLUDED.winner_id,
            status = EXCLUDED.status, end_at = EXCLUDED.end_at,
            fetched_at = NOW()
    """, (
        m["id"], t1_id, t1_name, t2_id, t2_name,
        s1, s2, winner, winner_id, m.get("number_of_games"),
        m.get("begin_at"), m.get("end_at"), m.get("status"),
        tournament_name, serie_name, m.get("league_id"),
    ))
    return True


def main():
    if not API_KEY:
        print("PANDASCORE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=50, help="Max pages to fetch (100 matches each)")
    ap.add_argument("--since", default=None, help="Stop when we hit matches before this date (YYYY-MM-DD)")
    ap.add_argument("--upcoming", action="store_true", help="Also fetch /upcoming for future matches")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="Don't stop on first already-seen ID; full re-scan")
    args = ap.parse_args()

    since_dt = None
    if args.since:
        since_dt = datetime.combine(date.fromisoformat(args.since), datetime.min.time(),
                                     tzinfo=timezone.utc)

    print(f"\n=== PandaScore matches backfill  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    existing = _existing_ids()
    print(f"  existing pandascore_ids in DB: {len(existing)}")

    ctx = scraper_run("pandascore_matches", "PandaScore match-history backfill (10× wider than bo3.gg coverage)") if scraper_run else None
    st = ctx.__enter__() if ctx else None

    total_inserted = total_seen = 0
    endpoints = ["matches/past"]
    if args.upcoming:
        endpoints.append("matches/upcoming")

    try:
        for endpoint in endpoints:
            print(f"\n  --- {endpoint} ---")
            for page in range(1, args.pages + 1):
                if page > 1:
                    time.sleep(RATE_DELAY)
                rows = fetch_page(endpoint, page)
                if rows is None:
                    break
                if not rows:
                    print(f"  page {page}: empty — done")
                    break
                page_inserted = 0
                hit_seen = 0
                hit_old = False
                for m in rows:
                    total_seen += 1
                    if not args.no_skip_existing and m["id"] in existing:
                        hit_seen += 1
                        continue
                    # Stop if before --since date
                    if since_dt and m.get("begin_at"):
                        try:
                            bt = datetime.fromisoformat(m["begin_at"].replace("Z", "+00:00"))
                            if bt < since_dt:
                                hit_old = True
                                continue
                        except (ValueError, TypeError):
                            pass
                    if upsert_match(m):
                        page_inserted += 1
                        total_inserted += 1
                        existing.add(m["id"])
                        if st: st.tick_done()

                print(f"  page {page}: seen {len(rows)}, inserted {page_inserted}, skipped {hit_seen} existing")

                # If this whole page was already seen, we're done
                if not args.no_skip_existing and hit_seen == len(rows):
                    print(f"  all matches on page {page} already in DB — stopping")
                    break
                if hit_old:
                    print(f"  hit --since cutoff on page {page} — stopping")
                    break

        if st:
            st.set_total(total_seen)
            st.note(f"endpoints={endpoints} inserted={total_inserted} seen={total_seen}")
        print(f"\n  total inserted: {total_inserted}  seen: {total_seen}")

    finally:
        if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
