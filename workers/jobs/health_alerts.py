"""
OddsIntel — Pipeline Health Alerts (PIPE-ALERT / SYNTHETIC-LIVENESS)

Sends an email alert to ADMIN_ALERT_EMAIL when the pipeline is silently broken.
The /admin/ops dashboard is post-mortem — you have to open it to see problems.
This job is proactive: it fires if something looks wrong.

Conditions checked:
  1. Morning bet check (09:30): 0 bets placed today with ≥10 scheduled matches
  2. Odds coverage (09:15): Pinnacle odds missing for >10 of today's scheduled matches
  3. Snapshot staleness (hourly 10-23 UTC): no live snapshot in last 25 min during active window
  4. Settlement check (21:30): 0 results settled when >5 bets were pending before settlement

Each condition logs to console always. Email fires only when the condition is true.
One alert per condition per UTC day (deduped in memory via a simple set — process-level only).

Requires: RESEND_API_KEY + ADMIN_ALERT_EMAIL in .env.
"""

import os
from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from workers.api_clients.db import execute_query

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")

# In-memory dedup: set of "YYYY-MM-DD:condition_name" strings already alerted today.
# Resets on process restart (fine — restart is rare, and a re-alert on restart is OK).
_alerted_today: set[str] = set()


def _dedup_key(condition: str) -> str:
    return f"{date.today().isoformat()}:{condition}"


def _send_alert(subject: str, body_html: str) -> None:
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print(f"[yellow]Alert (no email configured): {subject}[/yellow]")
        return

    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": body_html},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]Alert sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Alert send failed ({resp.status_code}): {subject}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Alert send error: {e}[/yellow]")


def _alert_once(condition: str, subject: str, body_html: str) -> None:
    key = _dedup_key(condition)
    if key in _alerted_today:
        return
    _alerted_today.add(key)
    console.print(f"[red bold]PIPELINE ALERT: {subject}[/red bold]")
    _send_alert(f"[OddsIntel Alert] {subject}", body_html)


def check_morning_bets() -> None:
    """No bets placed today despite ≥10 scheduled matches — morning pipeline may have failed."""
    today = date.today().isoformat()

    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM matches WHERE date::date = %s AND status = 'scheduled'",
        (today,)
    )
    match_count = (rows[0]["cnt"] if rows else 0) or 0

    if match_count < 10:
        console.print(f"[dim]health_alerts: {match_count} scheduled matches today — skipping bet check[/dim]")
        return

    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM simulated_bets WHERE created_at::date = %s",
        (today,)
    )
    bet_count = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: {bet_count} bets placed today ({match_count} matches)[/dim]")

    if bet_count == 0:
        _alert_once(
            "zero_bets",
            f"0 bets placed — {match_count} matches scheduled",
            f"<p>Today ({today}) has {match_count} scheduled matches but 0 simulated bets were placed.</p>"
            f"<p>The morning betting pipeline likely failed. Check Railway logs.</p>",
        )


def check_pinnacle_coverage() -> None:
    """More than 10 scheduled matches today have no Pinnacle odds — odds fetch may have failed."""
    today = date.today().isoformat()

    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt
        FROM matches m
        WHERE m.date::date = %s
          AND m.status = 'scheduled'
          AND NOT EXISTS (
              SELECT 1 FROM match_signals ms
              WHERE ms.match_id = m.id
                AND ms.signal_name = 'pinnacle_implied_home'
          )
        """,
        (today,)
    )
    missing = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: {missing} scheduled matches missing Pinnacle odds[/dim]")

    if missing > 10:
        _alert_once(
            "pinnacle_missing",
            f"{missing} matches missing Pinnacle odds",
            f"<p>Today ({today}) has {missing} scheduled matches without Pinnacle implied odds.</p>"
            f"<p>The odds fetch job may have failed or the AF Pinnacle bookmaker is unavailable.</p>",
        )


def check_snapshot_staleness() -> None:
    """No live snapshot in last 25 min during 10-23 UTC — LivePoller may be down."""
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    if hour < 10 or hour >= 23:
        return  # Outside live window — no matches expected

    rows = execute_query(
        "SELECT MAX(created_at) AS last_snap FROM live_match_snapshots"
    )
    last_snap = rows[0]["last_snap"] if rows else None

    if last_snap is None:
        console.print("[dim]health_alerts: no live snapshots in DB yet[/dim]")
        return

    if last_snap.tzinfo is None:
        from datetime import timezone as tz
        last_snap = last_snap.replace(tzinfo=tz.utc)

    age_minutes = (now_utc - last_snap).total_seconds() / 60
    console.print(f"[dim]health_alerts: last live snapshot {age_minutes:.1f} min ago[/dim]")

    if age_minutes > 25:
        _alert_once(
            "snapshot_stale",
            f"LivePoller stale — last snapshot {age_minutes:.0f} min ago",
            f"<p>It is {now_utc.strftime('%H:%M UTC')} and the last live match snapshot was "
            f"{age_minutes:.0f} minutes ago.</p>"
            f"<p>LivePoller may be down or stuck. Check Railway logs for the live-poller thread.</p>",
        )


def check_settlement() -> None:
    """Settlement produced 0 results when >5 bets were pending — settlement job may have failed."""
    today = date.today().isoformat()

    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt FROM simulated_bets
        WHERE result != 'pending'
          AND updated_at::date = %s
        """,
        (today,)
    )
    settled_today = (rows[0]["cnt"] if rows else 0) or 0

    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result = 'pending'
          AND m.status = 'finished'
        """,
    )
    stale_pending = (rows[0]["cnt"] if rows else 0) or 0

    console.print(f"[dim]health_alerts: {settled_today} bets settled today, {stale_pending} pending on finished matches[/dim]")

    if stale_pending > 5:
        _alert_once(
            "settlement_stale",
            f"Settlement gap — {stale_pending} pending bets on finished matches",
            f"<p>There are {stale_pending} simulated bets still marked 'pending' on matches "
            f"that have finished.</p>"
            f"<p>Settlement job may have failed. Today ({today}) settled {settled_today} bets total.</p>"
            f"<p>Check Railway logs for the 21:00 UTC settlement job.</p>",
        )


def run_morning_checks() -> None:
    """09:30 UTC check — run after the morning betting pipeline."""
    console.print("[cyan]health_alerts: running morning checks[/cyan]")
    try:
        check_morning_bets()
    except Exception as e:
        console.print(f"[yellow]health_alerts morning bet check error: {e}[/yellow]")
    try:
        check_pinnacle_coverage()
    except Exception as e:
        console.print(f"[yellow]health_alerts pinnacle check error: {e}[/yellow]")


def run_snapshot_check() -> None:
    """Hourly 10-23 UTC — LivePoller staleness check."""
    try:
        check_snapshot_staleness()
    except Exception as e:
        console.print(f"[yellow]health_alerts snapshot check error: {e}[/yellow]")


def run_settlement_check() -> None:
    """21:30 UTC — settlement completeness check."""
    console.print("[cyan]health_alerts: running settlement check[/cyan]")
    try:
        check_settlement()
    except Exception as e:
        console.print(f"[yellow]health_alerts settlement check error: {e}[/yellow]")
