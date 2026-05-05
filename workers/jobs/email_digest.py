"""
OddsIntel — Daily Email Digest (ENG-4)

Sends a tier-appropriate morning email to all subscribed users at 07:30 UTC.

Tier content:
  Free   — top 3 match preview teasers + site activity stats + upgrade CTA
  Pro    — + value bet count + signal alert count for today
  Elite  — + full value bet details with odds + model confidence

Requires RESEND_API_KEY in .env. Uses Resend's Python SDK.

One email per user per day enforced via email_digest_log unique constraint.

Usage:
  python -m workers.jobs.email_digest           # live run (today)
  python -m workers.jobs.email_digest --dry-run # print emails, no sends
  python -m workers.jobs.email_digest --limit 5 # send to max 5 users
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query, execute_write

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
SITE_URL = os.getenv("SITE_URL", "https://oddsintel.app")


# ── Data fetchers ──────────────────────────────────────────────────────────

def fetch_todays_previews(target_date: str, limit: int = 3) -> list[dict]:
    """Return top match previews generated today, ordered by league tier."""
    rows = execute_query(
        """
        SELECT
            mp.preview_text,
            mp.preview_short,
            mp.match_id,
            mp.league_tier,
            ht.name  AS home_team,
            at.name  AS away_team,
            l.name   AS league,
            m.date   AS kickoff
        FROM match_previews mp
        JOIN matches  m  ON m.id  = mp.match_id
        JOIN teams    ht ON ht.id = m.home_team_id
        JOIN teams    at ON at.id = m.away_team_id
        JOIN leagues  l  ON l.id  = m.league_id
        WHERE mp.match_date = %s
        ORDER BY mp.league_tier ASC, mp.signal_count DESC
        LIMIT %s
        """,
        [target_date, limit],
    )
    return rows or []


def fetch_value_bets_summary(target_date: str) -> dict:
    """Count today's value bets and return top picks for Elite email."""
    rows = execute_query(
        """
        SELECT
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            sb.edge_percent,
            sb.model_probability,
            ht.name AS home_team,
            at.name AS away_team,
            l.name  AS league
        FROM simulated_bets sb
        JOIN matches m  ON m.id  = sb.match_id
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        WHERE sb.created_at::date = %s
          AND sb.result = 'pending'
          AND sb.edge_percent >= 3
        ORDER BY sb.edge_percent DESC
        LIMIT 10
        """,
        [target_date],
    )
    return {
        "count": len(rows or []),
        "top_picks": rows or [],
    }


def fetch_subscribed_users() -> list[dict]:
    """
    Return all users who have email_digest_enabled = true.
    Joins profiles for tier + email.
    """
    rows = execute_query(
        """
        SELECT
            p.id,
            p.email,
            p.tier,
            p.is_superadmin
        FROM profiles p
        JOIN user_notification_settings uns ON uns.user_id = p.id
        WHERE uns.email_digest_enabled = true
          AND p.email IS NOT NULL
          AND p.email != ''
        ORDER BY p.created_at ASC
        """,
        [],
    )
    return rows or []


def already_sent(user_id: str, digest_date: str) -> bool:
    rows = execute_query(
        "SELECT id FROM email_digest_log WHERE user_id = %s AND digest_date = %s",
        [user_id, digest_date],
    )
    return bool(rows)


def log_send(user_id: str, digest_date: str, tier: str, email_to: str,
             resend_id: str | None, status: str, preview_count: int,
             value_bet_count: int, error_msg: str | None = None):
    execute_write(
        """
        INSERT INTO email_digest_log
          (user_id, digest_date, tier, sent_at, resend_id, email_to,
           status, error_msg, preview_count, value_bet_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, digest_date) DO NOTHING
        """,
        [
            user_id, digest_date, tier, datetime.now(timezone.utc).isoformat(),
            resend_id, email_to, status, error_msg, preview_count, value_bet_count,
        ],
    )


# ── Email HTML builders ────────────────────────────────────────────────────

# Brand colours — matches oddsintel.app (dark theme, green primary)
_GREEN      = "#22c55e"   # text-green-500 — "INTEL" in logo, primary CTA
_GREEN_DARK = "#16a34a"   # green-600 — hover/dark variant
_GREEN_BG   = "#f0fdf4"   # green-50 — light tint for callouts
_GREEN_BD   = "#bbf7d0"   # green-200 — callout border
_SITE_BG    = "#0a0a14"   # site background — header
_NAVY       = "#0f172a"   # fallback dark
_SLATE      = "#1e293b"   # body text
_MUTED      = "#64748b"   # secondary text
_BORDER     = "#e2e8f0"   # card borders
_BG         = "#f1f5f9"   # email outer background
_WHITE      = "#ffffff"
_EDGE_GREEN = "#22c55e"   # positive edge colour


def _kickoff_fmt(raw: str) -> str:
    """'2026-05-05T19:00:00' → 'May 05 · 19:00 UTC'"""
    try:
        dt = datetime.fromisoformat(str(raw)[:19])
        return dt.strftime("%b %d · %H:%M UTC")
    except Exception:
        return str(raw)[:16].replace("T", " ") + " UTC"


def _preview_card_html(p: dict, full_text: bool = False) -> str:
    home = p.get("home_team", "?")
    away = p.get("away_team", "?")
    league = p.get("league", "")
    kickoff = _kickoff_fmt(p.get("kickoff", ""))
    text = p["preview_text"] if full_text else p["preview_short"]
    match_url = f"{SITE_URL}/matches/{p['match_id']}"
    return f"""
    <div style="background:{_WHITE};border:1px solid {_BORDER};border-left:3px solid {_GREEN};border-radius:8px;padding:18px 20px;margin-bottom:14px;">
      <div style="margin-bottom:8px;">
        <span style="display:inline-block;background:#f0fdf4;color:{_GREEN};font-size:11px;font-weight:600;letter-spacing:0.04em;padding:2px 8px;border-radius:4px;text-transform:uppercase;">{league}</span>
        <span style="font-size:12px;color:{_MUTED};margin-left:8px;">{kickoff}</span>
      </div>
      <div style="font-size:17px;font-weight:700;color:{_SLATE};margin-bottom:10px;">{home} <span style="color:{_MUTED};font-weight:400;">vs</span> {away}</div>
      <div style="font-size:14px;color:#334155;line-height:1.7;">{text}</div>
      <div style="margin-top:14px;">
        <a href="{match_url}" style="display:inline-block;background:{_GREEN};color:{_WHITE};font-size:12px;font-weight:600;padding:7px 14px;border-radius:6px;text-decoration:none;">
          View full analysis →
        </a>
      </div>
    </div>"""


def _value_bet_row_html(bet: dict) -> str:
    home = bet.get("home_team", "?")
    away = bet.get("away_team", "?")
    league = bet.get("league", "")
    market = bet.get("market", "")
    selection = bet.get("selection", "")
    odds = bet.get("odds_at_pick", 0)
    edge = bet.get("edge_percent", 0)
    conf = bet.get("model_probability", 0)
    return f"""
    <tr>
      <td style="padding:10px 8px;border-bottom:1px solid {_BG};font-size:13px;color:{_SLATE};">{home} vs {away}<br><span style="color:{_MUTED};font-size:11px;">{league}</span></td>
      <td style="padding:10px 8px;border-bottom:1px solid {_BG};font-size:13px;color:{_SLATE};">{market} — {selection}</td>
      <td style="padding:10px 8px;border-bottom:1px solid {_BG};font-size:13px;font-weight:700;color:{_SLATE};">{odds:.2f}</td>
      <td style="padding:10px 8px;border-bottom:1px solid {_BG};font-size:13px;font-weight:700;color:{_GREEN};">+{edge:.1f}%</td>
      <td style="padding:10px 8px;border-bottom:1px solid {_BG};font-size:13px;color:{_MUTED};">{conf:.0%}</td>
    </tr>"""


def build_email_html(
    user_email: str,
    tier: str,
    previews: list[dict],
    value_bets: dict,
    target_date: str,
) -> tuple[str, str]:
    """Build (subject, html_body) for one user."""
    is_pro = tier in ("pro", "elite")
    is_elite = tier == "elite"
    display_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%B %d, %Y")
    preview_count = len(previews)
    bet_count = value_bets["count"]

    # Subject
    if is_elite and bet_count > 0:
        subject = f"OddsIntel · {display_date} — {bet_count} value bet{'s' if bet_count != 1 else ''} + {preview_count} match previews"
    elif is_pro:
        subject = f"OddsIntel · {display_date} — {preview_count} match previews + {bet_count} value bet{'s' if bet_count != 1 else ''} today"
    else:
        subject = f"OddsIntel · Today's {preview_count} match previews — {display_date}"

    unsubscribe_url = f"{SITE_URL}/profile?tab=notifications"
    value_bets_url  = f"{SITE_URL}/value-bets"

    # Preview cards
    preview_cards_html = "".join(_preview_card_html(p, full_text=is_pro) for p in previews)

    # Value bets section
    value_bets_section = ""
    if is_elite and bet_count > 0:
        rows_html = "".join(_value_bet_row_html(b) for b in value_bets["top_picks"][:5])
        value_bets_section = f"""
        <div style="margin-top:28px;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:{_MUTED};text-transform:uppercase;margin-bottom:10px;">Today's Value Bets</div>
          <table style="width:100%;border-collapse:collapse;background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;overflow:hidden;">
            <thead>
              <tr style="background:{_BG};">
                <th style="padding:10px 8px;text-align:left;font-size:11px;color:{_MUTED};font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid {_BORDER};">MATCH</th>
                <th style="padding:10px 8px;text-align:left;font-size:11px;color:{_MUTED};font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid {_BORDER};">BET</th>
                <th style="padding:10px 8px;text-align:left;font-size:11px;color:{_MUTED};font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid {_BORDER};">ODDS</th>
                <th style="padding:10px 8px;text-align:left;font-size:11px;color:{_MUTED};font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid {_BORDER};">EDGE</th>
                <th style="padding:10px 8px;text-align:left;font-size:11px;color:{_MUTED};font-weight:600;letter-spacing:0.04em;text-transform:uppercase;border-bottom:1px solid {_BORDER};">CONF</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
          <p style="margin-top:12px;margin-bottom:0;">
            <a href="{value_bets_url}" style="font-size:13px;color:{_GREEN};text-decoration:none;font-weight:600;">View all value bets →</a>
          </p>
        </div>"""
    elif is_pro and bet_count > 0:
        value_bets_section = f"""
        <div style="margin-top:20px;background:{_GREEN_BG};border:1px solid {_GREEN_BD};border-radius:8px;padding:16px 20px;">
          <div style="font-size:15px;font-weight:700;color:#15803d;">{bet_count} value bet{'s' if bet_count != 1 else ''} identified today</div>
          <p style="color:#166534;font-size:13px;margin:4px 0 12px;">Model edge ≥ 3% on today's slate.</p>
          <a href="{value_bets_url}" style="display:inline-block;background:#15803d;color:{_WHITE};font-size:12px;font-weight:600;padding:7px 14px;border-radius:6px;text-decoration:none;">View value bets →</a>
        </div>"""
    elif not is_pro:
        value_bets_section = f"""
        <div style="margin-top:20px;background:#f0fdf4;border:1px solid {_GREEN_BD};border-radius:8px;padding:16px 20px;">
          <div style="font-size:15px;font-weight:700;color:{_GREEN_DARK};">Unlock value bets with Pro</div>
          <p style="color:{_GREEN_DARK};font-size:13px;margin:4px 0 12px;">Our model found {bet_count} value bet{'s' if bet_count != 1 else ''} today. Pro members see edge %, confidence scores, and full analysis.</p>
          <a href="{SITE_URL}/pricing" style="display:inline-block;background:{_GREEN};color:{_WHITE};font-size:12px;font-weight:600;padding:7px 14px;border-radius:6px;text-decoration:none;">See plans →</a>
        </div>"""

    # "View all matches" CTA for free users
    all_matches_cta = ""
    if not is_pro:
        all_matches_cta = f"""
        <p style="margin:16px 0 0;font-size:13px;color:{_MUTED};">
          <a href="{SITE_URL}/matches" style="color:{_GREEN};text-decoration:none;font-weight:600;">View all today's matches →</a>
        </p>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:{_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">

  <!-- Outer wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
    <tr><td align="center" style="padding:32px 16px 24px;">

      <!-- Card -->
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Logo header — matches site: dark bg, ODDS white, INTEL green -->
        <tr>
          <td style="background:{_SITE_BG};border-radius:10px 10px 0 0;padding:24px 32px;text-align:center;">
            <a href="{SITE_URL}" style="text-decoration:none;display:inline-block;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.04em;">ODDS</span><span style="font-size:28px;font-weight:800;color:{_GREEN};letter-spacing:0.04em;">INTEL</span>
            </a>
            <div style="font-size:12px;color:#64748b;margin-top:6px;letter-spacing:0.04em;">{display_date}</div>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background:{_WHITE};padding:24px 32px 28px;border-left:1px solid {_BORDER};border-right:1px solid {_BORDER};border-top:none;">

            <!-- Section label -->
            <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:{_MUTED};text-transform:uppercase;margin-bottom:14px;">Today's Match Previews</div>

            {preview_cards_html}
            {all_matches_cta}
            {value_bets_section}

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-radius:0 0 10px 10px;border:1px solid {_BORDER};border-top:none;padding:18px 32px;text-align:center;">
            <p style="margin:0 0 6px;font-size:12px;color:{_MUTED};">
              You're receiving this because you have daily digests enabled in your
              <a href="{SITE_URL}" style="color:{_GREEN};text-decoration:none;font-weight:600;">OddsIntel</a> account.
            </p>
            <p style="margin:0 0 10px;font-size:12px;">
              <a href="{unsubscribe_url}" style="color:{_MUTED};text-decoration:underline;">Manage preferences</a>
              &nbsp;·&nbsp;
              <a href="{SITE_URL}/matches" style="color:{_MUTED};text-decoration:underline;">Today's matches</a>
            </p>
            <p style="margin:0;font-size:11px;color:#94a3b8;line-height:1.5;">
              Not financial or gambling advice. Past model performance does not guarantee future results.<br>Please gamble responsibly.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""

    return subject, html


# ── Resend sender ──────────────────────────────────────────────────────────

def send_via_resend(to_email: str, subject: str, html: str) -> tuple[str | None, str | None]:
    """
    Send email via Resend REST API. Returns (resend_id, error_msg).
    Uses httpx to avoid adding a heavyweight SDK dependency.
    """
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

def run_email_digest(target_date: str | None = None, dry_run: bool = False, limit: int | None = None):
    today = target_date or date.today().isoformat()
    console.print(f"[bold cyan]═══ OddsIntel Email Digest: {today} ═══[/bold cyan]\n")

    if not RESEND_API_KEY and not dry_run:
        console.print("[red]RESEND_API_KEY not set — aborting. Set it in .env or use --dry-run.[/red]")
        return

    # Fetch shared data once
    previews = fetch_todays_previews(today, limit=3)
    value_bets = fetch_value_bets_summary(today)
    users = fetch_subscribed_users()

    console.print(f"Previews available: {len(previews)}")
    console.print(f"Value bets today:   {value_bets['count']}")
    console.print(f"Subscribed users:   {len(users)}\n")

    if not previews:
        console.print("[yellow]No previews generated yet for today. Run match_previews.py first.[/yellow]")
        return

    if limit is not None:
        users = users[:limit]

    sent = 0
    skipped = 0
    failed = 0

    for user in users:
        uid = user["id"]
        email = user["email"]
        raw_tier = user.get("tier", "free")
        # Superadmins get elite content
        tier = "elite" if user.get("is_superadmin") else raw_tier

        if already_sent(uid, today):
            skipped += 1
            continue

        subject, html = build_email_html(email, tier, previews, value_bets, today)

        if dry_run:
            console.print(f"[dim]WOULD SEND to {email} ({tier}):[/dim] {subject}")
            log_send(uid, today, tier, email, None, "skipped", len(previews), value_bets["count"])
            skipped += 1
            continue

        resend_id, error = send_via_resend(email, subject, html)

        if error:
            console.print(f"  [red]✗ {email} ({tier}): {error}[/red]")
            log_send(uid, today, tier, email, None, "failed", len(previews), value_bets["count"], error)
            failed += 1
        else:
            console.print(f"  [green]✓ {email} ({tier}) — id={resend_id}[/green]")
            log_send(uid, today, tier, email, resend_id, "sent", len(previews), value_bets["count"])
            sent += 1

    console.print(f"\n[bold]Done:[/bold] {sent} sent | {skipped} skipped | {failed} failed")
    if dry_run:
        console.print("[yellow](dry-run — no emails sent)[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send daily email digest")
    parser.add_argument("--dry-run", action="store_true", help="Print without sending")
    parser.add_argument("--limit", type=int, default=None, help="Max users to send to")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    run_email_digest(target_date=args.date, dry_run=args.dry_run, limit=args.limit)
