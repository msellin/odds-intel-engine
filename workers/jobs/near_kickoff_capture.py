"""
OddsIntel — NEAR-KICKOFF-CAPTURE (2026-09-11): direct-book closing prices.

WHAT. Every 5 minutes, for fixtures kicking off in the next 15 minutes, fetch
that ONE fixture from each direct book we collect (Coolbet, Unibet-Site,
Epicbet) straight by its book event id, and write the prices to odds_snapshots.
`minutes_to_kickoff` <= 15 makes the shared writers stamp them is_closing=TRUE.

WHY. The direct-book sweeps walk the whole board every 30 min — Coolbet's
evening sweep takes 60-75 min end to end — so the last price we held before
kickoff was usually 1-5h old. A price that old is not a close, and it made
real-bet CLV unmeasurable at the book we actually bet at (DIRECT-BOOK-CLV,
migration 332). Closing coverage over the 3 days to 2026-09-11: Coolbet 23
matches, Epicbet 82, Unibet-Site 139 — against Pinnacle 539.

HOW IT FINDS THE EVENT. The sweeps now persist the pairing they already compute
into book_event_map (migration 333). No mapping = no capture for that book: the
job never re-walks a board, which is what keeps it O(imminent fixtures).

WHERE IT RUNS. The operator's Mac (local/launchd/com.oddsintel.near-kickoff-
capture.plist), because two of the three transports only exist there:
  * Coolbet     — plain requests with the watchdog-harvested Imperva cookies
                  (COOLBET_NO_FS). Deliberately NOT FlareSolverr: the Coolbet
                  board sweep holds the Mac FS session almost continuously in
                  the evening, and that FS (1 GiB cap) also carries real-money
                  placement. If the cookies are stale this fails for the tick
                  and logs it — it can never starve placement.
  * Unibet-Site — injected fetch from the logged-in unibet.ee CDP tab.
  * Epicbet     — direct from the residential IP (no Cloudflare there). If it
                  ever falls back to FS it uses its own session id
                  (EPICBET_FLARE_SESSION in the plist) so it can never destroy
                  a sweep's session.
The AF / Pinnacle side of the close is `closing_snap.py` on the VPS.

A (match, book) the sweep priced in the last MIN_GAP_MIN minutes is skipped —
that price is already fresh, and re-fetching it is load for nothing.

    python3 -m workers.jobs.near_kickoff_capture                       # all books
    python3 -m workers.jobs.near_kickoff_capture --books Epicbet --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
# epicbet_explorer reads its FS session id at import. If Epicbet ever falls back
# to FlareSolverr here, it must NOT reuse the sweep's id — `_fs_open` destroys an
# existing session of that name first, which would kill a running sweep.
os.environ.setdefault("EPICBET_FLARE_SESSION", "epicbet_nearko_reader")

from workers.api_clients.db import execute_query  # noqa: E402

log = logging.getLogger(__name__)

WINDOW_MIN = 15    # capture fixtures kicking off within this many minutes
MIN_GAP_MIN = 6    # skip a (match, book) with a snapshot this recent
BOOKS = ("Coolbet", "Unibet-Site", "Epicbet")
UNIBET_SPACING_S = 1.2  # same pacing as the Unibet sweep (DataDome is behavioural)


def due_fixtures(bookmaker: str, window_min: int = WINDOW_MIN,
                 min_gap_min: int = MIN_GAP_MIN) -> list[dict]:
    """Mapped fixtures at `bookmaker` kicking off within `window_min` minutes,
    not yet started, and not priced at that book in the last `min_gap_min`."""
    return execute_query(
        """SELECT bem.match_id::text AS match_id, bem.book_event_id, m.date
             FROM book_event_map bem
             JOIN matches m ON m.id = bem.match_id
            WHERE bem.bookmaker = %s
              AND m.status = 'scheduled'
              AND m.date > now()
              AND m.date <= now() + make_interval(mins => %s)
              AND NOT EXISTS (
                    SELECT 1 FROM odds_snapshots os
                     WHERE os.match_id = bem.match_id
                       AND os.bookmaker = %s
                       AND os.timestamp > now() - make_interval(mins => %s))
            ORDER BY m.date""",
        [bookmaker, window_min, bookmaker, min_gap_min],
    ) or []


def _mins_to_ko(ko) -> int:
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=timezone.utc)
    return int((ko - datetime.now(timezone.utc)).total_seconds() // 60)


def _kicked_off(d: dict) -> bool:
    """Re-checked immediately before EACH fetch: the due list is built once, and
    a fixture due at T-0.5 could otherwise be fetched after kickoff and written
    as a 'closing' row carrying an in-play price."""
    ko = d["date"] if d["date"].tzinfo else d["date"].replace(tzinfo=timezone.utc)
    return ko <= datetime.now(timezone.utc)


def capture_coolbet(due: list[dict], dry_run: bool) -> dict:
    # Forced, not defaulted: this path must NEVER go through the Mac FlareSolverr
    # that the board sweep and the real-money placer share. CoolbetSession reads
    # the flag at __init__, so set it first.
    os.environ["COOLBET_NO_FS"] = "1"
    from workers.automation.coolbet_session import CoolbetSession
    from workers.automation.coolbet_explorer import (
        fetch_match_markets, fetch_odds_for_markets, store_coolbet_snapshots_for_match,
    )
    c = {"due": len(due), "stored": 0, "fails": 0}
    session = CoolbetSession(require_auth=False)
    for d in due:
        if _kicked_off(d):
            continue
        try:
            markets = fetch_match_markets(session, int(d["book_event_id"]))
            odds_map = fetch_odds_for_markets(session, markets)
            parsed, stored, _ = store_coolbet_snapshots_for_match(
                d["match_id"], markets, odds_map,
                dry_run=dry_run, kickoff_iso=d["date"].isoformat(),
            )
            # dry_run stores nothing, so report what WOULD have been written.
            c["stored"] += parsed if dry_run else stored
        except Exception as e:  # noqa: BLE001
            c["fails"] += 1
            log.warning("near-KO Coolbet event %s failed: %s", d["book_event_id"], e)
    return c


def capture_epicbet(due: list[dict], dry_run: bool) -> dict:
    from workers.automation import epicbet_explorer as ex
    from workers.api_clients.supabase_client import store_book_odds_snapshots
    c = {"due": len(due), "stored": 0, "fails": 0}
    sess = ex._session()
    try:
        for d in due:
            if _kicked_off(d):
                continue
            raw = ex.fetch_sidebets(sess, int(d["book_event_id"]))
            if not raw:
                c["fails"] += 1
                continue
            ev = {"id": int(d["book_event_id"]), "raw": raw}
            ids = ex.collect_market_ids(ev)
            odds_map = ex.fetch_odds(sess, ids) if ids else {}
            rows, _dropped = ex.drop_non_monotone_ft_ou(ex.parse_event_markets(ev, odds_map), d["match_id"])
            if dry_run or not rows:
                c["stored"] += len(rows) if dry_run else 0
                continue
            c["stored"] += store_book_odds_snapshots(ex.BOOKMAKER, d["match_id"], rows, _mins_to_ko(d["date"]))
    finally:
        ex.fs_close(sess)
    return c


def capture_unibet(due: list[dict], dry_run: bool) -> dict:
    from workers.automation import unibet_odds_feed as ub
    from workers.api_clients.supabase_client import store_book_odds_snapshots
    c = {"due": len(due), "stored": 0, "fails": 0}
    for i, d in enumerate(due):
        if i:
            time.sleep(UNIBET_SPACING_S)
        if _kicked_off(d):
            continue
        url = (f"{ub._SPORTSBFF}/views/contest-page?_typ=GetContestWithPricesReq"
               f"&contestKey={d['book_event_id']}")
        contest = asyncio.run(ub._async_inject_get(url))
        if not contest or not (contest.get("contest") or {}).get("propositions"):
            c["fails"] += 1
            continue
        rows = ub.parse_contest(contest)
        if dry_run or not rows:
            c["stored"] += len(rows) if dry_run else 0
            continue
        c["stored"] += store_book_odds_snapshots(ub._BOOKMAKER, d["match_id"], rows, _mins_to_ko(d["date"]))
    return c


_CAPTURE = {"Coolbet": capture_coolbet, "Unibet-Site": capture_unibet, "Epicbet": capture_epicbet}


def run_near_kickoff_capture(books=BOOKS, *, dry_run: bool = False,
                             window_min: int = WINDOW_MIN) -> dict:
    """Capture near-kickoff prices at each book. One book failing never stops
    the others. Returns {book: counters}."""
    out: dict[str, dict] = {}
    for book in books:
        try:
            due = due_fixtures(book, window_min)
            out[book] = _CAPTURE[book](due, dry_run) if due else {"due": 0, "stored": 0, "fails": 0}
        except Exception as e:  # noqa: BLE001
            log.warning("near-KO capture for %s failed: %s", book, e)
            out[book] = {"error": str(e)}
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", default=",".join(BOOKS))
    ap.add_argument("--window-min", type=int, default=WINDOW_MIN)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    books = [b.strip() for b in a.books.split(",") if b.strip() in _CAPTURE]
    res = run_near_kickoff_capture(books, dry_run=a.dry_run, window_min=a.window_min)
    log.info("near_kickoff_capture%s: %s", " [DRY-RUN]" if a.dry_run else "", res)


if __name__ == "__main__":
    main()
