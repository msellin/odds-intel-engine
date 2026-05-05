"""
OddsIntel — Watchlist Signal Alerts (ENG-8)

Scans saved matches for each subscribed user and sends alerts:
  Free   — kickoff reminder when a saved match starts within 2 hours
  Pro    — + odds movement alert (≥5% shift on any market in last 6h)
  Elite  — same as Pro

Run schedule (scheduler.py): 08:30, 14:30, 20:30 UTC daily

Idempotent via watchlist_alert_log UNIQUE(user_id, match_id, alert_type).
If a user has already received a 'kickoff_reminder' for a given match,
they won't get a second one regardless of how many times this job runs.

Usage:
  python -m workers.jobs.watchlist_alerts           # live run
  python -m workers.jobs.watchlist_alerts --dry-run # print, no sends
  python -m workers.jobs.watchlist_alerts --limit 5 # max 5 users
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query, execute_write

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
SITE_URL = os.getenv("SITE_URL", "https://oddsintel.app")

KICKOFF_WINDOW_HOURS = 2      # alert when match is ≤2h away
ODDS_MOVE_THRESHOLD = 0.05    # 5% relative movement triggers alert
ODDS_LOOKBACK_HOURS = 6       # compare to odds from 6h ago


# ── Data fetchers ──────────────────────────────────────────────────────────

def fetch_subscribed_users() -> list[dict]:
    """Return users with watchlist_alerts_enabled=true who have saved matches."""
    rows = execute_query(
        """
        SELECT DISTINCT
            p.id        AS user_id,
            p.tier,
            au.email
        FROM profiles p
        JOIN auth.users au ON au.id = p.id
        JOIN user_notification_settings uns ON uns.user_id = p.id
        WHERE uns.watchlist_alerts_enabled = true
          AND EXISTS (
            SELECT 1 FROM saved_matches sm WHERE sm.user_id = p.id
          )
        ORDER BY p.id
        """,
        [],
    )
    return rows or []


def fetch_user_saved_matches(user_id: str) -> list[dict]:
    """Return upcoming saved matches for a user (not yet finished)."""
    rows = execute_query(
        """
        SELECT
            m.id        AS match_id,
            m.date      AS kickoff,
            ht.name     AS home_team,
            at.name     AS away_team,
            l.name      AS league
        FROM saved_matches sm
        JOIN matches  m  ON m.id  = sm.match_id
        JOIN teams    ht ON ht.id = m.home_team_id
        JOIN teams    at ON at.id = m.away_team_id
        JOIN leagues  l  ON l.id  = m.league_id
        WHERE sm.user_id = %s
          AND m.status IN ('scheduled', 'not_started')
          AND m.date > now()
        ORDER BY m.date ASC
        """,
        [user_id],
    )
    return rows or []


def already_alerted(user_id: str, match_id: str, alert_type: str) -> bool:
    """Check if we've already sent this alert type for this match to this user."""
    rows = execute_query(
        """
        SELECT 1 FROM watchlist_alert_log
        WHERE user_id = %s AND match_id = %s AND alert_type = %s
        LIMIT 1
        """,
        [user_id, match_id, alert_type],
    )
    return bool(rows)


def log_alert(user_id: str, match_id: str, alert_type: str,
              email: str, resend_id: str | None, status: str, error_msg: str | None = None):
    execute_write(
        """
        INSERT INTO watchlist_alert_log
            (user_id, match_id, alert_type, email_to, resend_id, status, error_msg)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, match_id, alert_type) DO NOTHING
        """,
        [user_id, match_id, alert_type, email, resend_id, status, error_msg],
    )


def fetch_odds_movement(match_id: str) -> list[dict]:
    """
    Return markets where odds have moved ≥5% relative in the last ODDS_LOOKBACK_HOURS.
    Returns list of dicts: {market, selection, old_odds, new_odds, pct_change}.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ODDS_LOOKBACK_HOURS)
    rows = execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (market, selection)
                market, selection, odds, timestamp
            FROM odds_snapshots
            WHERE match_id = %s
            ORDER BY market, selection, timestamp DESC
        ),
        earliest AS (
            SELECT DISTINCT ON (market, selection)
                market, selection, odds AS old_odds
            FROM odds_snapshots
            WHERE match_id = %s
              AND timestamp <= %s
            ORDER BY market, selection, timestamp DESC
        )
        SELECT
            l.market,
            l.selection,
            e.old_odds,
            l.odds AS new_odds,
            ABS(l.odds - e.old_odds) / NULLIF(e.old_odds, 0) AS pct_change
        FROM latest l
        JOIN earliest e ON e.market = l.market AND e.selection = l.selection
        WHERE ABS(l.odds - e.old_odds) / NULLIF(e.old_odds, 0) >= %s
        ORDER BY pct_change DESC
        LIMIT 3
        """,
        [match_id, match_id, cutoff.isoformat(), ODDS_MOVE_THRESHOLD],
    )
    return rows or []


# ── Email builders ─────────────────────────────────────────────────────────

_HEADER = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#0a0f1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e2e8f0;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0f1a;padding:24px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">

<!-- Header -->
<tr><td style="background:#0d1829;border-radius:12px 12px 0 0;padding:20px 28px;border-bottom:2px solid #3b82f6;">
  <a href="{site_url}" style="text-decoration:none;">
    <span style="font-family:monospace;font-size:20px;font-weight:900;font-style:italic;letter-spacing:-0.5px;color:#ffffff;">ODDS</span><span style="font-family:monospace;font-size:20px;font-weight:900;font-style:italic;color:#3b82f6;">INTEL</span>
  </a>
</td></tr>

<!-- Body -->
<tr><td style="background:#111827;padding:24px 28px;">
""".strip()

_FOOTER = """
<br/>
<p style="color:#475569;font-size:11px;margin:16px 0 0 0;">
  You're receiving this because you saved a match on OddsIntel.<br/>
  <a href="{site_url}/profile" style="color:#64748b;">Manage notification settings →</a>
</p>
</td></tr>

<!-- Footer bar -->
<tr><td style="background:#0d1829;border-radius:0 0 12px 12px;padding:14px 28px;border-top:1px solid #1e293b;">
  <p style="color:#334155;font-size:11px;margin:0;">© 2026 OddsIntel · <a href="{site_url}" style="color:#3b82f6;text-decoration:none;">oddsintel.app</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def build_kickoff_email(email: str, match: dict) -> tuple[str, str]:
    """Build kickoff reminder email. Returns (subject, html)."""
    home = match["home_team"]
    away = match["away_team"]
    league = match["league"]
    kickoff: datetime = match["kickoff"]
    match_id = match["match_id"]

    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    mins = int((kickoff - datetime.now(timezone.utc)).total_seconds() / 60)
    time_str = f"{mins} min" if mins < 60 else f"{mins // 60}h {mins % 60:02d}m"

    subject = f"Kickoff in {time_str}: {home} vs {away}"

    body = f"""
<h2 style="color:#f1f5f9;font-size:18px;font-weight:700;margin:0 0 4px 0;">Saved match kicking off soon</h2>
<p style="color:#64748b;font-size:13px;margin:0 0 20px 0;">{league}</p>

<div style="background:#0d1829;border:1px solid #1e3a5f;border-radius:10px;padding:18px 20px;margin-bottom:20px;">
  <p style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;margin:0 0 8px 0;">Match</p>
  <p style="color:#f1f5f9;font-size:22px;font-weight:700;margin:0 0 6px 0;">{home} <span style="color:#475569;">vs</span> {away}</p>
  <p style="color:#3b82f6;font-size:14px;font-weight:600;margin:0;">Kicks off in <strong>{time_str}</strong></p>
</div>

<a href="{SITE_URL}/matches/{match_id}"
   style="display:inline-block;background:#3b82f6;color:#ffffff;font-size:13px;font-weight:700;
          text-decoration:none;padding:10px 22px;border-radius:8px;">
  View Match →
</a>
"""

    html = _HEADER.format(title=subject, site_url=SITE_URL)
    html += body
    html += _FOOTER.format(site_url=SITE_URL)
    return subject, html


def build_odds_move_email(email: str, match: dict, movements: list[dict]) -> tuple[str, str]:
    """Build odds movement alert email. Returns (subject, html)."""
    home = match["home_team"]
    away = match["away_team"]
    league = match["league"]
    match_id = match["match_id"]

    # Largest movement for subject line
    top = movements[0]
    pct = top["pct_change"] * 100
    direction = "shortened" if top["new_odds"] < top["old_odds"] else "drifted"
    subject = f"Odds alert: {home} vs {away} — {top['selection']} {direction} {pct:.0f}%"

    rows_html = ""
    for mv in movements:
        pct_mv = mv["pct_change"] * 100
        is_shorter = mv["new_odds"] < mv["old_odds"]
        arrow = "▼" if is_shorter else "▲"
        colour = "#22c55e" if is_shorter else "#f97316"
        rows_html += f"""
<tr>
  <td style="padding:8px 0;color:#94a3b8;font-size:13px;border-bottom:1px solid #1e293b;">{mv['selection']}</td>
  <td style="padding:8px 0;color:#64748b;font-size:13px;font-family:monospace;text-align:right;border-bottom:1px solid #1e293b;">{float(mv['old_odds']):.2f}</td>
  <td style="padding:8px 0;color:#f1f5f9;font-size:13px;font-family:monospace;text-align:right;border-bottom:1px solid #1e293b;">{float(mv['new_odds']):.2f}</td>
  <td style="padding:8px 0;color:{colour};font-size:13px;font-weight:700;text-align:right;border-bottom:1px solid #1e293b;">{arrow} {pct_mv:.0f}%</td>
</tr>"""

    body = f"""
<h2 style="color:#f1f5f9;font-size:18px;font-weight:700;margin:0 0 4px 0;">Odds movement detected</h2>
<p style="color:#64748b;font-size:13px;margin:0 0 20px 0;">{home} vs {away} · {league}</p>

<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:20px;">
  <thead>
    <tr>
      <th style="text-align:left;color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;padding-bottom:6px;">Selection</th>
      <th style="text-align:right;color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;padding-bottom:6px;">Was</th>
      <th style="text-align:right;color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;padding-bottom:6px;">Now</th>
      <th style="text-align:right;color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:0.08em;padding-bottom:6px;">Move</th>
    </tr>
  </thead>
  <tbody>{rows_html}
  </tbody>
</table>

<a href="{SITE_URL}/matches/{match_id}"
   style="display:inline-block;background:#3b82f6;color:#ffffff;font-size:13px;font-weight:700;
          text-decoration:none;padding:10px 22px;border-radius:8px;">
  View Match →
</a>
"""

    html = _HEADER.format(title=subject, site_url=SITE_URL)
    html += body
    html += _FOOTER.format(site_url=SITE_URL)
    return subject, html


# ── Resend sender ──────────────────────────────────────────────────────────

def send_via_resend(to_email: str, subject: str, html: str) -> tuple[str | None, str | None]:
    """Send email via Resend REST API. Returns (resend_id, error_msg)."""
    import httpx

    if not RESEND_API_KEY:
        return None, "RESEND_API_KEY not set"

    payload = {
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("id"), None
        else:
            return None, f"Resend {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return None, str(e)[:200]


# ── Main ───────────────────────────────────────────────────────────────────

def run_watchlist_alerts(dry_run: bool = False, limit: int | None = None):
    now = datetime.now(timezone.utc)
    kickoff_cutoff = now + timedelta(hours=KICKOFF_WINDOW_HOURS)

    if not RESEND_API_KEY and not dry_run:
        console.print("[red]RESEND_API_KEY not set — aborting. Use --dry-run to test.[/red]")
        return

    users = fetch_subscribed_users()
    if limit:
        users = users[:limit]

    console.print(f"[bold]Watchlist Alerts[/bold] — {now.strftime('%Y-%m-%d %H:%M UTC')} — {len(users)} subscribed users")

    kickoff_sent = 0
    odds_sent = 0
    skipped = 0
    errors = 0

    for user in users:
        uid = user["user_id"]
        email = user["email"]
        tier = user["tier"]
        is_pro = tier in ("pro", "elite")

        matches = fetch_user_saved_matches(uid)
        if not matches:
            continue

        for match in matches:
            match_id = str(match["match_id"])
            kickoff: datetime = match["kickoff"]
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)

            # ── Kickoff reminder (all tiers) ────────────────────────────────
            if now < kickoff <= kickoff_cutoff:
                if already_alerted(uid, match_id, "kickoff_reminder"):
                    skipped += 1
                    continue

                subject, html = build_kickoff_email(email, match)

                if dry_run:
                    console.print(f"  [cyan][DRY-RUN] kickoff → {email}: {subject}[/cyan]")
                    kickoff_sent += 1
                    continue

                resend_id, err = send_via_resend(email, subject, html)
                if err:
                    console.print(f"  [red]✗ kickoff {email}: {err}[/red]")
                    log_alert(uid, match_id, "kickoff_reminder", email, None, "error", err)
                    errors += 1
                else:
                    console.print(f"  [green]✓ kickoff → {email}: {match['home_team']} vs {match['away_team']}[/green]")
                    log_alert(uid, match_id, "kickoff_reminder", email, resend_id, "sent")
                    kickoff_sent += 1

            # ── Odds movement alert (Pro/Elite only) ────────────────────────
            if is_pro:
                if already_alerted(uid, match_id, "odds_move"):
                    skipped += 1
                    continue

                movements = fetch_odds_movement(match_id)
                if not movements:
                    continue

                subject, html = build_odds_move_email(email, match, movements)

                if dry_run:
                    console.print(f"  [cyan][DRY-RUN] odds_move → {email}: {subject}[/cyan]")
                    odds_sent += 1
                    continue

                resend_id, err = send_via_resend(email, subject, html)
                if err:
                    console.print(f"  [red]✗ odds_move {email}: {err}[/red]")
                    log_alert(uid, match_id, "odds_move", email, None, "error", err)
                    errors += 1
                else:
                    console.print(f"  [green]✓ odds_move → {email}: {match['home_team']} vs {match['away_team']}[/green]")
                    log_alert(uid, match_id, "odds_move", email, resend_id, "sent")
                    odds_sent += 1

    console.print(
        f"\n[bold]Done[/bold] — "
        f"kickoff: [green]{kickoff_sent}[/green]  "
        f"odds_move: [green]{odds_sent}[/green]  "
        f"skipped: {skipped}  "
        f"errors: [red]{errors}[/red]"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send watchlist signal alerts")
    parser.add_argument("--dry-run", action="store_true", help="Print emails without sending")
    parser.add_argument("--limit", type=int, default=None, help="Max users to process")
    args = parser.parse_args()
    run_watchlist_alerts(dry_run=args.dry_run, limit=args.limit)
