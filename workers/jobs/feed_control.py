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


# ── AUTO-PAUSE: the circuit breaker (migration 391) ──────────────────────────
AUTO_PAUSE_AFTER = 2          # failed runs in a row
_BACKOFF_MIN = (60, 120, 240, 480, 720)


def _backoff(count: int) -> int:
    return _BACKOFF_MIN[min(max(count, 1), len(_BACKOFF_MIN)) - 1]


def _notify(msg: str, key: str) -> None:
    try:
        from workers.notify.telegram import send_telegram
        send_telegram(msg, dedup_key=key, dedup_window_s=1800)
    except Exception as e:  # noqa: BLE001
        log.debug("feed auto-pause notify failed: %s", e)


def apply_auto_pause(evals: list[dict]) -> dict:
    """Called by feed_health after each evaluation. Pauses auto_pause feeds that
    have failed AUTO_PAUSE_AFTER runs in a row SINCE their last auto-resume, and
    resets the backoff counter once a feed succeeds again. Never touches a feed an
    operator paused by hand."""
    ctl = {r["feed_id"]: r for r in (execute_query(
        """SELECT c.feed_id, c.paused, c.paused_by, c.auto_pause_count,
                  (SELECT max(created_at) FROM feed_actions a
                    WHERE a.feed_id = c.feed_id AND a.action = 'auto_resume') AS resumed_at
             FROM feed_controls c""") or [])}
    paused = reset = 0
    for e in evals:
        f = FEEDS_BY_ID.get(e["feed_id"])
        if not f or not f.get("auto_pause"):
            continue
        c = ctl.get(e["feed_id"]) or {}
        count = int(c.get("auto_pause_count") or 0)
        ok_now = e.get("last_run_status") in ("completed", "success") and e.get("status") == "ok"
        if ok_now and count and not c.get("paused"):
            execute_write("UPDATE feed_controls SET auto_pause_count = 0, updated_at = now() "
                          "WHERE feed_id = %s", (e["feed_id"],))
            _notify(f"🟢 <b>{f['label']}</b> recovered after an auto-pause — running normally again.",
                    f"autopause-ok-{e['feed_id']}")
            reset += 1
            continue
        if c.get("paused"):
            continue
        streak = int(e.get("fail_streak") or 0)
        resumed_at = c.get("resumed_at")
        last_run = e.get("last_run_at")
        if streak < AUTO_PAUSE_AFTER or (resumed_at and last_run and last_run <= resumed_at):
            continue
        count += 1
        mins = _backoff(count)
        reason = (f"auto-paused after {streak} failed runs"
                  f"{': ' + (e.get('last_error') or '')[:140] if e.get('last_error') else ''}")
        execute_write(
            """INSERT INTO feed_controls (feed_id, paused, paused_reason, paused_by, paused_at,
                                         auto_resume_at, auto_pause_count, updated_at)
               VALUES (%s, true, %s, 'auto', now(), now() + make_interval(mins => %s), %s, now())
               ON CONFLICT (feed_id) DO UPDATE SET paused = true, paused_reason = EXCLUDED.paused_reason,
                 paused_by = 'auto', paused_at = now(), auto_resume_at = EXCLUDED.auto_resume_at,
                 auto_pause_count = EXCLUDED.auto_pause_count, updated_at = now()""",
            (e["feed_id"], reason, mins, count))
        execute_write("INSERT INTO feed_actions (feed_id, action, reason, actor, handled_at, result) "
                      "VALUES (%s, 'auto_pause', %s, 'auto', now(), %s)",
                      (e["feed_id"], reason, f"paused for {mins} min (attempt {count})"))
        _notify(f"⏸ <b>{f['label']}</b> auto-paused after {streak} failed runs — "
                f"backing off {mins // 60} h, then one test run.\n"
                f"<a href=\"https://www.oddsintel.app/admin/feeds\">/admin/feeds</a>",
                f"autopause-{e['feed_id']}-{count}")
        paused += 1
    return {"auto_paused": paused, "auto_reset": reset}


def auto_resume() -> int:
    """Resume engine-paused feeds whose backoff has elapsed and queue ONE test run.
    If that run fails, apply_auto_pause pauses again with a doubled backoff."""
    due = execute_query(
        """SELECT feed_id FROM feed_controls
            WHERE paused AND paused_by = 'auto' AND auto_resume_at IS NOT NULL
              AND auto_resume_at <= now()""") or []
    for d in due:
        execute_write(
            """UPDATE feed_controls SET paused = false, paused_reason = NULL, paused_by = NULL,
                      paused_at = NULL, auto_resume_at = NULL,
                      run_now_requested_at = now(), run_now_requested_by = 'auto',
                      updated_at = now() WHERE feed_id = %s""", (d["feed_id"],))
        execute_write("INSERT INTO feed_actions (feed_id, action, reason, actor, handled_at, result) "
                      "VALUES (%s, 'auto_resume', 'backoff elapsed — one test run', 'auto', now(), "
                      "'resumed; test run queued')", (d["feed_id"],))
        execute_write("INSERT INTO feed_actions (feed_id, action, reason, actor) "
                      "VALUES (%s, 'run_now', 'auto-resume test run', 'auto')", (d["feed_id"],))
        with _lock:
            _cache["at"] = 0.0        # make the resume visible to _run_job immediately
    return len(due)


def drain(scheduler, scheduler_module) -> dict:
    """Turn pending run-now requests into one-off runs. Returns counters."""
    resumed = auto_resume()
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
    return {"started": started, "refused": refused, "auto_resumed": resumed}
