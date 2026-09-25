"""BOOK FOOTPRINT (#110, 2026-09-23) — count every request we send to a bookmaker,
enforce an hourly budget per book, and record the outcomes that come BEFORE a block.

WHY. #108: ~7,500 Coolbet requests in 8 hours from one exit IP (peak 1,517 in an
hour), 6,033 of them search fallbacks. Nothing counted them; Imperva flagged the IP
and Coolbet went dark for hours. A request budget turns "we slowly became a scraper
nobody noticed" into a refused request and a visible number on /admin/feeds.

HOW. Callers wrap each outbound request:

    footprint.check("Coolbet")              # raises FootprintBudgetExceeded when spent
    ... send ...
    footprint.record("Coolbet", outcome, seconds)   # "ok" | "challenge" | "error"

Counts are shared across processes through `book_footprint` (migration 392): each
process batches its increments and flushes every 20 requests / 30 s / exit, and
`check` reads the DB total for the current hour (cached 30 s) plus its own unflushed
count. Accounting failures never block a request — the budget fails OPEN on a DB
hiccup, because a counter outage must not become a data outage.

Budgets (requests per hour) are set just above measured normal volume; override
per book with env FOOTPRINT_BUDGET_<BOOK> (e.g. FOOTPRINT_BUDGET_COOLBET=400).

PRIORITY RESERVE (FOOTPRINT-PRIORITY-RESERVE, #151, 2026-09-25). A single cap is
first-come-first-served, and the lowest-value requests come first: on the Friday
09-25 weekend slate both books sat AT their budget 10:00-15:00 UTC. Tonybet deep
boards (full market lists for tomorrow's kickoffs) grew ~10/h -> ~120/h and starved
live stats (2 requests in the 14:00 hour instead of 30) and the 14:20 results run;
the Coolbet sweep's refresh of fixtures many hours out starved the near-kickoff
closing capture. Deferrable callers now ask `has_headroom(book)` first and skip
their optional request once the hour is past (1 - reserve) of its budget, leaving
the rest for the requests that cannot wait (closing price, live, results). A
deferral is NOT a refusal: nothing is booked, the caller falls back.

REFUSER IDENTITY (#151). Every refusal also records WHO refused it
(host/proc/pid) in `book_footprint.refused_by` (migration 445). The log line alone
could not explain the "refused while under budget" hours, because the refusing
process's stdout was not in the VPS journal.
"""
from __future__ import annotations

import atexit
import logging
import os
import socket
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Requests per hour, per book, across all our processes.
# RE-TUNED 2026-09-24 (#110/#112) from 13–14 metered hours: ~2x the measured peak, with
# room for a startup catch-up landing in the same hour as a scheduled sweep. Coolbet is the
# exception — it stays at the level set below the #108 danger zone (750–1,500/h got the
# exit IP flagged), and its refresh-by-kickoff change should bring it well under this.
_DEFAULT_BUDGETS = {
    "Coolbet": 500,           # peak 508 / median 294 before refresh-by-kickoff; danger zone 750+
    "Tonybet": 150,           # peak 69 / median 51 (bulk API: pages of 100 events)
    "Unibet-Site": 400,       # peak 177 / median 87, + headroom for the new "World" fixtures
    "Epicbet": 1200,          # peak 576 / median 416 (2 sweeps/h + near-kickoff)
    "Betfair-Exchange": 150,  # peak 53 / median 45 (~15 req per run incl. step-D markets, 4 runs/h)
    "Epicbet-inplay": 3000,   # slimmed collector: 90 s x 25 fixtures ~ 2,000/h worst case
}
# Share of each hour's budget kept back for requests that cannot wait (see PRIORITY
# RESERVE above). Sized from the 09-25 metering: Tonybet's must-run traffic is live
# stats (30/h) + near-kickoff closes (up to ~60/h on a busy slate) + results (~5)
# against 150, so half is reserved; Coolbet's is near-kickoff (~2 requests per due
# fixture, <=19 due/h) + the 5-min health ping (12/h) against 500.
_RESERVE_SHARE = {
    "Coolbet": 0.2,
    "Tonybet": 0.5,
}
SLOW_S = 20.0
_FLUSH_EVERY = 20
_FLUSH_S = 30.0
_CACHE_S = 30.0


class FootprintBudgetExceeded(RuntimeError):
    """Our own hourly request budget for a book is spent — the request was NOT sent."""


def budget(book: str) -> int | None:
    env = os.getenv(f"FOOTPRINT_BUDGET_{book.upper().replace('-', '_')}")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return _DEFAULT_BUDGETS.get(book)


_lock = threading.Lock()
_pending: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_pending_n = 0
_last_flush = time.monotonic()
_batch_hour = None  # the clock hour the pending batch was counted in (FOOTPRINT-HOUR-BOOKING)
_db_cache: dict[str, tuple[float, int, datetime]] = {}
_refusers: dict[str, set[str]] = defaultdict(set)  # book -> {"host/proc/pid"} in the pending batch
# Off only in the smoke harness (scripts/smoke_test.py): CI runs against the PRODUCTION DB, and
# BOOK-FOOTPRINT's forced refusal was flushed into the real book_footprint row every run — the
# "refused while under budget" of #151 (refused_by named runnervm…/smoke_test.py on the first try).
_WRITES_ENABLED = True
_refused_by_col = True  # False once the DB says migration 445 is not applied (then write without it)


def _hour() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _db_count(book: str) -> int:
    hour = _hour()
    cached = _db_cache.get(book)
    if cached and cached[2] == hour and time.monotonic() - cached[0] < _CACHE_S:
        return cached[1]
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query("SELECT requests FROM book_footprint WHERE book = %s AND hour = %s",
                             (book, hour)) or []
        n = int(rows[0]["requests"]) if rows else 0
    except Exception as e:  # noqa: BLE001 — fail open
        log.debug("footprint read failed: %s", e)
        n = cached[1] if cached and cached[2] == hour else 0
    _db_cache[book] = (time.monotonic(), n, hour)
    return n


def _roll_hour() -> None:
    """Flush a batch counted in an earlier clock hour before counting anything new."""
    if _batch_hour is not None and _batch_hour != _hour():
        flush()


def has_headroom(book: str, reserve: float | None = None) -> bool:
    """True while this hour's count is below the part of the budget open to DEFERRABLE
    requests, i.e. cap * (1 - reserve). Never raises and books nothing — the caller
    simply skips its optional request (PRIORITY RESERVE). No budget -> always True."""
    cap = budget(book)
    if not cap:
        return True
    share = _RESERVE_SHARE.get(book, 0.0) if reserve is None else reserve
    with _lock:
        local = _pending[book]["requests"]
    return _db_count(book) + local < cap * (1.0 - share)


def _whoami() -> str:
    return f"{socket.gethostname()}/{os.path.basename(sys.argv[0] or '?')}/{os.getpid()}"


def check(book: str) -> None:
    """Raise FootprintBudgetExceeded when this book's hourly budget is spent."""
    global _batch_hour
    _roll_hour()
    cap = budget(book)
    if not cap:
        return
    with _lock:
        local = _pending[book]["requests"]
    db = _db_count(book)
    if db + local >= cap:
        with _lock:
            if _batch_hour is None:
                _batch_hour = _hour()
            _pending[book]["refused"] += 1
            _refusers[book].add(_whoami())
        # #110 follow-up (2026-09-24): refusals were counted but never logged, and ~17/h
        # showed up for Coolbet in hours whose DB total was 220-340 of 500. Log what THIS
        # process believed at the moment it refused, so the refusing caller can be found.
        log.warning("footprint REFUSED %s: db=%d local=%d cap=%d pid=%d proc=%s thread=%s",
                    book, db, local, cap, os.getpid(), os.path.basename(sys.argv[0] or "?"),
                    threading.current_thread().name)
        _maybe_flush()
        raise FootprintBudgetExceeded(
            f"{book}: hourly request budget of {cap} spent — request not sent "
            f"(protects the exit IP; see #110)")


def record(book: str, outcome: str = "ok", seconds: float | None = None, *,
           count_request: bool = True) -> None:
    """Count one request and its outcome. `count_request=False` records only an
    outcome observed later (e.g. a bot-check page detected after the response)."""
    global _pending_n
    global _batch_hour
    _roll_hour()
    with _lock:
        if _batch_hour is None:
            _batch_hour = _hour()
        p = _pending[book]
        if count_request:
            p["requests"] += 1
            _pending_n += 1
        if outcome == "challenge":
            p["challenges"] += 1
        elif outcome == "error":
            p["errors"] += 1
        if seconds is not None and seconds > SLOW_S:
            p["slow"] += 1
    _maybe_flush()


def _maybe_flush() -> None:
    # FOOTPRINT-HOUR-BOOKING (2026-09-24): also flush the moment the clock hour changes, so a
    # batch is never booked under the NEXT hour (see flush()).
    if (_pending_n >= _FLUSH_EVERY or time.monotonic() - _last_flush > _FLUSH_S
            or (_batch_hour is not None and _batch_hour != _hour())):
        flush()


_UPSERT = """INSERT INTO book_footprint (book, hour, requests, challenges, errors, slow, refused)
   VALUES (%s, %s, %s, %s, %s, %s, %s)
   ON CONFLICT (book, hour) DO UPDATE SET
     requests = book_footprint.requests + EXCLUDED.requests,
     challenges = book_footprint.challenges + EXCLUDED.challenges,
     errors = book_footprint.errors + EXCLUDED.errors,
     slow = book_footprint.slow + EXCLUDED.slow,
     refused = book_footprint.refused + EXCLUDED.refused"""
# Same, plus the distinct union of refusers (host/proc/pid) seen in this hour (#151).
_UPSERT_WITH_WHO = """INSERT INTO book_footprint (book, hour, requests, challenges, errors, slow, refused, refused_by)
   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
   ON CONFLICT (book, hour) DO UPDATE SET
     requests = book_footprint.requests + EXCLUDED.requests,
     challenges = book_footprint.challenges + EXCLUDED.challenges,
     errors = book_footprint.errors + EXCLUDED.errors,
     slow = book_footprint.slow + EXCLUDED.slow,
     refused = book_footprint.refused + EXCLUDED.refused,
     refused_by = CASE WHEN EXCLUDED.refused_by IS NULL THEN book_footprint.refused_by
                       ELSE (SELECT array_agg(DISTINCT x ORDER BY x)
                               FROM unnest(coalesce(book_footprint.refused_by, '{}') || EXCLUDED.refused_by) x)
                  END"""


def flush() -> None:
    global _pending_n, _last_flush, _batch_hour, _refused_by_col
    with _lock:
        batch = {b: dict(c) for b, c in _pending.items() if any(c.values())}
        who = {b: sorted(v) for b, v in _refusers.items() if v}
        _refusers.clear()
        # FOOTPRINT-HOUR-BOOKING (2026-09-24, #139 feeds review): book the batch under the hour it
        # was COUNTED in, not the hour of the flush. Before, Tonybet refusals made at 18:59:57
        # (150/150) landed under 19:00 (81/150), so /admin/feeds said "request budget spent" in an
        # hour that was nowhere near its budget.
        hour = _batch_hour or _hour()
        _pending.clear()
        _pending_n = 0
        _batch_hour = None
        _last_flush = time.monotonic()
    if not batch or not _WRITES_ENABLED:
        return
    try:
        from workers.api_clients.db import execute_write
        for book, c in batch.items():
            vals = (book, hour, c.get("requests", 0), c.get("challenges", 0), c.get("errors", 0),
                    c.get("slow", 0), c.get("refused", 0))
            if _refused_by_col:
                try:
                    execute_write(_UPSERT_WITH_WHO, vals + (who.get(book) or None,))
                except Exception as e:  # noqa: BLE001
                    if "refused_by" not in str(e):
                        raise
                    _refused_by_col = False   # migration 445 not applied yet: keep counting without it
                    execute_write(_UPSERT, vals)
            else:
                execute_write(_UPSERT, vals)
            cached = _db_cache.get(book)
            if cached and cached[2] == hour:
                _db_cache[book] = (cached[0], cached[1] + c.get("requests", 0), hour)
    except Exception as e:  # noqa: BLE001 — accounting must never break a sweep
        log.debug("footprint flush failed: %s", e)


atexit.register(flush)


def classify_status(status: int | None) -> str:
    """HTTP status → outcome. 403/429 are what bot protection answers with."""
    if status is None or status == 0 or status >= 500:
        return "error"
    if status in (401, 403, 429):
        return "challenge"
    return "ok"


def metered_session(book: str):
    """A drop-in `requests.Session` whose every request is budget-checked and
    counted for `book` — so no individual call site can forget to meter."""
    import requests

    class _MeteredSession(requests.Session):
        def request(self, method, url, *args, **kwargs):  # noqa: D401
            check(book)
            t0 = time.monotonic()
            try:
                r = super().request(method, url, *args, **kwargs)
            except Exception:
                record(book, "error", time.monotonic() - t0)
                raise
            record(book, classify_status(r.status_code), time.monotonic() - t0)
            return r

    return _MeteredSession()
