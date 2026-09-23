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
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Requests per hour, per book, across all our processes.
_DEFAULT_BUDGETS = {
    "Coolbet": 500,       # was 750–1,500/h before the #108 flag; search now capped
    "Tonybet": 800,       # sweeps + deep boards + 2-min live + results; tighten once measured
    "Unibet-Site": 500,   # the sweep's own cap is 180 fetches per run, 2 runs/h
    "Epicbet": 4000,      # never blocked; in-play collector is chatty — tighten once measured
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
_db_cache: dict[str, tuple[float, int, datetime]] = {}


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


def check(book: str) -> None:
    """Raise FootprintBudgetExceeded when this book's hourly budget is spent."""
    cap = budget(book)
    if not cap:
        return
    with _lock:
        local = _pending[book]["requests"]
    if _db_count(book) + local >= cap:
        with _lock:
            _pending[book]["refused"] += 1
        _maybe_flush()
        raise FootprintBudgetExceeded(
            f"{book}: hourly request budget of {cap} spent — request not sent "
            f"(protects the exit IP; see #110)")


def record(book: str, outcome: str = "ok", seconds: float | None = None, *,
           count_request: bool = True) -> None:
    """Count one request and its outcome. `count_request=False` records only an
    outcome observed later (e.g. a bot-check page detected after the response)."""
    global _pending_n
    with _lock:
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
    if _pending_n >= _FLUSH_EVERY or time.monotonic() - _last_flush > _FLUSH_S:
        flush()


def flush() -> None:
    global _pending_n, _last_flush
    with _lock:
        batch = {b: dict(c) for b, c in _pending.items() if any(c.values())}
        _pending.clear()
        _pending_n = 0
        _last_flush = time.monotonic()
    if not batch:
        return
    hour = _hour()
    try:
        from workers.api_clients.db import execute_write
        for book, c in batch.items():
            execute_write(
                """INSERT INTO book_footprint (book, hour, requests, challenges, errors, slow, refused)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (book, hour) DO UPDATE SET
                     requests = book_footprint.requests + EXCLUDED.requests,
                     challenges = book_footprint.challenges + EXCLUDED.challenges,
                     errors = book_footprint.errors + EXCLUDED.errors,
                     slow = book_footprint.slow + EXCLUDED.slow,
                     refused = book_footprint.refused + EXCLUDED.refused""",
                (book, hour, c.get("requests", 0), c.get("challenges", 0), c.get("errors", 0),
                 c.get("slow", 0), c.get("refused", 0)))
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
