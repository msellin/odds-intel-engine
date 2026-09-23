"""FEED CONTROL (#107 FEEDS-DASHBOARD phase B, 2026-09-23) — enforce the pause /
run-now requests /admin/feeds writes to `feed_controls` (migration 389).

The web app only records requests; this module is the only thing that acts on
them, and it can only do two things, both to feeds listed in the registry:

  * `is_job_paused(job_name)` — called at the top of `_run_job`: a paused feed's
    scheduled run is skipped (logged, not failed). Cached 30 s so a pause lookup
    never costs every job a DB round-trip; a pause therefore takes effect within
    30 s, which is well inside any feed's interval.
  * `drain(scheduler, scheduler_module)` — every 30 s: each pending run-now becomes
    a one-off APScheduler job running that feed's own wrapper from
    workers/scheduler.py (so it is logged to pipeline_runs like any run). A paused
    feed's run-now is refused, not queued — resume first.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, execute_write
from workers.registry.feed_registry import FEEDS, FEEDS_BY_ID

log = logging.getLogger(__name__)

_JOB_TO_FEED = {f["job"]: f["id"] for f in FEEDS if f.get("job") and "pause" in (f.get("controls") or [])}
_CACHE_S = 30
_cache: dict = {"at": 0.0, "paused": set()}
_lock = threading.Lock()


def _paused_feeds() -> set[str]:
    with _lock:
        if time.monotonic() - _cache["at"] < _CACHE_S:
            return _cache["paused"]
    try:
        rows = execute_query("SELECT feed_id FROM feed_controls WHERE paused") or []
        paused = {r["feed_id"] for r in rows}
    except Exception as e:  # noqa: BLE001 — a control-table hiccup must never stop jobs
        log.debug("feed_controls read failed: %s", e)
        paused = _cache["paused"]
    with _lock:
        _cache.update(at=time.monotonic(), paused=paused)
    return paused


def is_job_paused(job_name: str) -> bool:
    feed = _JOB_TO_FEED.get(job_name)
    return bool(feed) and feed in _paused_feeds()


def drain(scheduler, scheduler_module) -> dict:
    """Turn pending run-now requests into one-off runs. Returns counters."""
    pending = execute_query(
        """SELECT feed_id, paused, run_now_requested_by FROM feed_controls
            WHERE run_now_requested_at IS NOT NULL
              AND (run_now_started_at IS NULL OR run_now_started_at < run_now_requested_at)""") or []
    started = refused = 0
    for p in pending:
        feed = FEEDS_BY_ID.get(p["feed_id"])
        fn = getattr(scheduler_module, (feed or {}).get("wrapper") or "", None)
        if not feed or "run_now" not in (feed.get("controls") or []) or fn is None:
            result = "refused: feed has no run-now control"
        elif p["paused"]:
            result = "refused: feed is paused — resume it first"
        else:
            scheduler.add_job(fn, trigger="date", run_date=datetime.now(timezone.utc),
                              id=f"runnow_{feed['id']}", replace_existing=True,
                              max_instances=1, misfire_grace_time=300)
            result = f"started one-off run of {feed['wrapper']}"
        ok = result.startswith("started")
        started += ok
        refused += not ok
        execute_write("UPDATE feed_controls SET run_now_started_at = now(), updated_at = now() "
                      "WHERE feed_id = %s", (p["feed_id"],))
        execute_write(
            """UPDATE feed_actions SET handled_at = now(), result = %s
                WHERE id = (SELECT id FROM feed_actions WHERE feed_id = %s AND action = 'run_now'
                              AND handled_at IS NULL ORDER BY created_at DESC LIMIT 1)""",
            (result, p["feed_id"]))
        log.info("feed run-now %s: %s", p["feed_id"], result)
    return {"started": started, "refused": refused}
