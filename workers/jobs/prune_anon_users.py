"""ANON-AUTH PHASE 4 — weekly prune of stale anonymous Supabase users.

Anon users accumulate from drive-by clicks (favorite / tracker pick).
Most never come back, but they still count toward Supabase MAU billing
and clutter auth.users. Delete anon users that haven't signed in for 90
days; cascade removes their profiles row and any favorites/picks
keyed on the same user_id.

Safe boundaries:
  - Only deletes WHERE is_anonymous = TRUE (real users are never touched)
  - Only deletes WHERE last_sign_in_at < NOW() - INTERVAL '90 days'
  - Logs the count of deleted rows to pipeline_runs for visibility
  - Skips with kill-switch 'prune_anon_users' if needed (manual brake)
"""

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
)
from workers.utils.kill_switches import is_disabled

console = Console()

PRUNE_AGE_DAYS = 90


def run() -> None:
    if is_disabled("prune_anon_users"):
        console.print("[yellow]prune_anon_users disabled via kill switch[/yellow]")
        return

    from datetime import date as _date
    today = _date.today().isoformat()
    run_id = log_pipeline_start("prune_anon_users", today)

    try:
        # Count first so we can log how many would be deleted.
        r = execute_query(
            f"""
            SELECT COUNT(*) AS n
            FROM auth.users
            WHERE is_anonymous = TRUE
              AND last_sign_in_at < NOW() - INTERVAL '{PRUNE_AGE_DAYS} days'
            """, []
        )
        candidates = r[0]["n"] if r else 0
        console.print(
            f"[cyan]prune_anon_users: {candidates} anonymous users idle "
            f"≥ {PRUNE_AGE_DAYS} days[/cyan]"
        )

        if candidates == 0:
            log_pipeline_complete(run_id, metadata={"deleted": 0})
            return

        # Safety cap: never delete more than 10k rows in one run. Catches
        # the "config error → schedule fires hourly → mass-wipes anon
        # users" failure mode. If 10k+ are stale, something's wrong —
        # alert via the failed status.
        if candidates > 10_000:
            msg = (
                f"prune_anon_users: ABORT — {candidates} candidates exceeds "
                f"safety cap of 10000. Investigate before running."
            )
            console.print(f"[red]{msg}[/red]")
            log_pipeline_failed(run_id, msg)
            return

        execute_write(
            f"""
            DELETE FROM auth.users
            WHERE is_anonymous = TRUE
              AND last_sign_in_at < NOW() - INTERVAL '{PRUNE_AGE_DAYS} days'
            """, []
        )
        console.print(f"[green]prune_anon_users: deleted {candidates} rows[/green]")
        log_pipeline_complete(run_id, metadata={"deleted": candidates})
    except Exception as e:
        console.print(f"[red]prune_anon_users failed: {e}[/red]")
        log_pipeline_failed(run_id, str(e)[:2000])
        raise


if __name__ == "__main__":
    run()
