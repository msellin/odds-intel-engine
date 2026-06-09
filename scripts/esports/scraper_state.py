"""
Helpers for self-healing scrapers.

Pattern:

    with scraper_run("match_details_process") as s:
        s.set_total(len(work))
        for item in work:
            try:
                process(item)
                s.tick_done()
            except Exception as e:
                s.tick_failed(str(e))

    # On exit: status='idle', last_success_at=now, items_done frozen for UI.

If the process is killed mid-run the row stays in 'running' state until next
fire, when the wrapper resets it. Self-healing because:
- Each scraper re-scans for "what's missing" via find_missing() callbacks
- Idempotent INSERTs mean re-runs don't duplicate
- Progress is restored across restarts via last_success_at + items_done
"""

from __future__ import annotations

import contextlib
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402


class _ScraperRun:
    def __init__(self, name: str):
        self.name = name
        self.started = time.time()
        self.total = 0
        self.done = 0
        self.failed = 0
        self.last_error: str | None = None

    def set_total(self, n: int) -> None:
        self.total = n
        execute_write(
            """UPDATE cs2_scraper_state SET items_total = %s, items_pending = %s,
                       updated_at = NOW() WHERE scraper_name = %s""",
            (n, n, self.name),
        )

    def set_pending(self, n: int) -> None:
        execute_write(
            "UPDATE cs2_scraper_state SET items_pending = %s, updated_at = NOW() WHERE scraper_name = %s",
            (n, self.name),
        )

    def set_stale(self, n: int) -> None:
        execute_write(
            "UPDATE cs2_scraper_state SET items_stale = %s, updated_at = NOW() WHERE scraper_name = %s",
            (n, self.name),
        )

    def tick_done(self, persist_every: int = 5) -> None:
        self.done += 1
        if self.done % persist_every == 0:
            self._flush()

    def tick_failed(self, err: str, persist_every: int = 1) -> None:
        self.failed += 1
        self.last_error = err[:500]
        if self.failed % persist_every == 0:
            self._flush()

    def note(self, text: str) -> None:
        execute_write(
            "UPDATE cs2_scraper_state SET notes = %s, updated_at = NOW() WHERE scraper_name = %s",
            (text[:500], self.name),
        )

    def _flush(self) -> None:
        execute_write(
            """UPDATE cs2_scraper_state
                  SET items_done = %s, items_failed = %s, items_pending = GREATEST(items_total - %s - %s, 0),
                      last_error = COALESCE(%s, last_error), updated_at = NOW()
                WHERE scraper_name = %s""",
            (self.done, self.failed, self.done, self.failed, self.last_error, self.name),
        )


@contextlib.contextmanager
def scraper_run(name: str, description: str | None = None):
    """Context manager wrapping one scraper invocation. Handles state row updates."""
    # Upsert the row in case it doesn't exist yet.
    execute_write(
        """INSERT INTO cs2_scraper_state (scraper_name, description, status, last_run_at)
           VALUES (%s, %s, 'running', NOW())
           ON CONFLICT (scraper_name) DO UPDATE
             SET status = 'running', last_run_at = NOW(),
                 description = COALESCE(EXCLUDED.description, cs2_scraper_state.description),
                 last_error = NULL, items_done = 0, items_failed = 0, updated_at = NOW()""",
        (name, description),
    )
    run = _ScraperRun(name)
    try:
        yield run
    except Exception:
        err = traceback.format_exc()[-1000:]
        execute_write(
            """UPDATE cs2_scraper_state
                  SET status = 'error', last_error = %s,
                      items_done = %s, items_failed = %s,
                      last_run_duration_s = %s, updated_at = NOW()
                WHERE scraper_name = %s""",
            (err, run.done, run.failed, time.time() - run.started, name),
        )
        raise
    else:
        execute_write(
            """UPDATE cs2_scraper_state
                  SET status = 'idle', last_success_at = NOW(),
                      items_done = %s, items_failed = %s,
                      items_pending = GREATEST(items_total - %s - %s, 0),
                      last_run_duration_s = %s, updated_at = NOW()
                WHERE scraper_name = %s""",
            (run.done, run.failed, run.done, run.failed,
             time.time() - run.started, name),
        )


def get_state(scraper_name: str) -> dict | None:
    rows = execute_query(
        "SELECT * FROM cs2_scraper_state WHERE scraper_name = %s",
        (scraper_name,),
    )
    return rows[0] if rows else None


def get_all_state() -> list[dict]:
    return execute_query(
        "SELECT * FROM cs2_scraper_state ORDER BY scraper_name",
    )
