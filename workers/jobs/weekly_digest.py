"""
OddsIntel — Weekly Performance Email (ENG-10)

Sends a Monday morning recap to all subscribed users at 08:00 UTC.

Content by tier:
  Free   — Model week in review (W/L/units) + this week's top matches + upgrade CTA
  Pro    — + User's own picks this week (W/L/units)
  Elite  — + CLV data on their picks + top performing league

Uses `weekly_report` column in user_notification_settings (default true).
Idempotent: one send per user per week via weekly_digest_log.

Usage:
  python -m workers.jobs.weekly_digest           # live run
  python -m workers.jobs.weekly_digest --dry-run # print emails, no sends
  python -m workers.jobs.weekly_digest --limit 5 # send to max 5 users
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import date, datetime, timezone, timedelta

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query, execute_write

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL     = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
SITE_URL       = os.getenv("SITE_URL", "https://oddsintel.app")

# Brand colours — matches email_digest.py
_GREEN      = "#22c55e"
_GREEN_DARK = "#16a34a"
_GREEN_BG   = "#f0fdf4"
_GREEN_BD   = "#bbf7d0"
_SITE_BG    = "#0a0a14"
_SLATE      = "#1e293b"
_MUTED      = "#64748b"
_BORDER     = "#e2e8f0"
_BG         = "#f1f5f9"
_WHITE      = "#ffffff"


# ── Helpers ────────────────────────────────────────────────────────────────

def _week_start(d: date) -> date:
    """Return the Monday of the week containing d (ISO week)."""
    return d - timedelta(days=d.weekday())


def _week_label(start: date) -> str:
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b %d')}–{end.strftime('%d')}"
    return f"{start.strftime('%b %d')} – {end.strftime('%b %d')}"


# ── Data fetchers ──────────────────────────────────────────────────────────

def fetch_model_week_stats(week_start: date) -> dict:
    """Model performance (simulated_bets) for the 7-day window Mon–Sun."""
    week_end = week_start + timedelta(days=6)
    rows = execute_query(
        """
        SELECT result, pnl, clv, market
        FROM simulated_bets
        WHERE created_at::date BETWEEN %s AND %s
          AND result IN ('won', 'lost')
        """,
        [week_start.isoformat(), week_end.isoformat()],
    ) or []

    won   = sum(1 for r in rows if r["result"] == "won")
    lost  = sum(1 for r in rows if r["result"] == "lost")
    total = won + lost
    net_units  = sum(r["pnl"] or 0 for r in rows)
    clv_values = [r["clv"] for r in rows if r.get("clv") is not None]
    avg_clv    = sum(clv_values) / len(clv_values) if clv_values else None

    # Best market by hit rate (min 5 bets)
    from collections import defaultdict
    market_stats: dict[str, list] = defaultdict(list)
    for r in rows:
        market_stats[r["market"]].append(r["result"] == "won")

    best_market = None
    best_hr     = 0.0
    for mkt, results in market_stats.items():
        if len(results) >= 5:
            hr = sum(results) / len(results)
            if hr > best_hr:
                best_hr    = hr
                best_market = mkt

    return {
        "won":        won,
        "lost":       lost,
        "total":      total,
        "net_units":  round(float(net_units), 2),
        "avg_clv":    round(float(avg_clv), 1) if avg_clv is not None else None,
        "best_market":    best_market,
        "best_market_hr": round(best_hr * 100) if best_market else None,
    }


def fetch_user_week_stats(user_id: str, week_start: date) -> dict | None:
    """
    User's own picks for the week (user_picks table).
    Returns None if no settled picks found.
    """
    week_end = week_start + timedelta(days=6)
    rows = execute_query(
        """
        SELECT up.result, up.odds, up.stake, up.selection,
               m.pseudo_clv_home, m.pseudo_clv_draw, m.pseudo_clv_away
        FROM user_picks up
        JOIN matches m ON m.id = up.match_id
        WHERE up.user_id = %s
          AND up.created_at::date BETWEEN %s AND %s
          AND up.result IN ('won', 'lost')
        """,
        [user_id, week_start.isoformat(), week_end.isoformat()],
    ) or []

    if not rows:
        return None

    won  = sum(1 for r in rows if r["result"] == "won")
    lost = sum(1 for r in rows if r["result"] == "lost")

    net_units = 0.0
    clv_values = []
    for r in rows:
        stake = float(r["stake"] or 1)
        odds  = float(r["odds"] or 2.0)
        if r["result"] == "won":
            net_units += (odds - 1) * stake
        else:
            net_units -= stake

        sel = r.get("selection")
        if sel == "home" and r.get("pseudo_clv_home") is not None:
            clv_values.append(float(r["pseudo_clv_home"]))
        elif sel == "draw" and r.get("pseudo_clv_draw") is not None:
            clv_values.append(float(r["pseudo_clv_draw"]))
        elif sel == "away" and r.get("pseudo_clv_away") is not None:
            clv_values.append(float(r["pseudo_clv_away"]))

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None

    return {
        "won":       won,
        "lost":      lost,
        "total":     won + lost,
        "net_units": round(net_units, 2),
        "avg_clv":   round(float(avg_clv) * 100, 1) if avg_clv is not None else None,
    }


def fetch_top_upcoming_matches(week_start: date, limit: int = 4) -> list[dict]:
    """Fetch top upcoming matches for the coming week ordered by league priority."""
    week_end = week_start + timedelta(days=6)
    rows = execute_query(
        """
        SELECT
            m.id,
            ht.name  AS home_team,
            at.name  AS away_team,
            l.name   AS league,
            m.date   AS kickoff
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        WHERE m.date::date BETWEEN %s AND %s
          AND m.status = 'scheduled'
          AND l.priority <= 14
        ORDER BY l.priority ASC, m.date ASC
        LIMIT %s
        """,
        [week_start.isoformat(), week_end.isoformat(), limit],
    )
    return rows or []


def fetch_subscribed_users() -> list[dict]:
    """Return users with weekly_report = true."""
    rows = execute_query(
        """
        SELECT
            p.id,
            p.email,
            p.tier,
            p.is_superadmin
        FROM profiles p
        JOIN user_notification_settings uns ON uns.user_id = p.id
        WHERE uns.weekly_report = true
          AND p.email IS NOT NULL
          AND p.email != ''
        ORDER BY p.created_at ASC
        """,
        [],
    )
    return rows or []


def already_sent(user_id: str, week_start: date) -> bool:
    rows = execute_query(
        "SELECT id FROM weekly_digest_log WHERE user_id = %s AND week_start = %s",
        [user_id, week_start.isoformat()],
    )
    return bool(rows)


def log_send(user_id: str, week_start: date, tier: str, email_to: str,
             resend_id: str | None, status: str, error_msg: str | None = None):
    execute_write(
        """
        INSERT INTO weekly_digest_log
          (user_id, week_start, tier, sent_at, resend_id, email_to, status, error_msg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, week_start) DO NOTHING
        """,
        [
            user_id, week_start.isoformat(), tier,
            datetime.now(timezone.utc).isoformat(),
            resend_id, email_to, status, error_msg,
        ],
    )


# ── Email HTML builder ─────────────────────────────────────────────────────

def _units_fmt(u: float) -> str:
    sign = "+" if u > 0 else ""
    return f"{sign}{u:.1f}u"


def _match_row_html(m: dict) -> str:
    kickoff = m.get("kickoff", "")
    try:
        dt = datetime.fromisoformat(str(kickoff)[:19])
        day_str = dt.strftime("%a %b %d")
    except Exception:
        day_str = str(kickoff)[:10]
    match_url = f"{SITE_URL}/matches/{m['id']}"
    return f"""
    <tr>
      <td style="padding:8px 10px;border-bottom:1px solid {_BG};font-size:13px;color:{_SLATE};">
        <a href="{match_url}" style="color:{_SLATE};text-decoration:none;font-weight:600;">{m['home_team']} vs {m['away_team']}</a>
        <div style="font-size:11px;color:{_MUTED};margin-top:2px;">{m['league']}</div>
      </td>
      <td style="padding:8px 10px;border-bottom:1px solid {_BG};font-size:12px;color:{_MUTED};text-align:right;white-space:nowrap;">{day_str}</td>
    </tr>"""


def build_weekly_email(
    user_email: str,
    tier: str,
    week_start: date,
    model_stats: dict,
    user_stats: dict | None,
    upcoming: list[dict],
) -> tuple[str, str]:
    is_pro   = tier in ("pro", "elite")
    is_elite = tier == "elite"

    week_label = _week_label(week_start - timedelta(days=7))  # previous week label
    display_date = datetime.now().strftime("%B %d, %Y")

    subject = f"OddsIntel · Week in Review — {week_label}"

    unsubscribe_url = f"{SITE_URL}/profile"
    value_bets_url  = f"{SITE_URL}/value-bets"

    # Model stats block
    m = model_stats
    model_color = _GREEN if m["net_units"] >= 0 else "#ef4444"
    model_result_str = (
        f"{m['won']}W / {m['lost']}L"
        if m["total"] > 0 else "No settled bets"
    )
    units_str = _units_fmt(m["net_units"]) if m["total"] > 0 else "—"
    clv_str   = f"+{m['avg_clv']}%" if m.get("avg_clv") and m["avg_clv"] > 0 else (f"{m['avg_clv']}%" if m.get("avg_clv") is not None else "—")

    model_block = f"""
    <div style="background:{_WHITE};border:1px solid {_BORDER};border-left:3px solid {_GREEN};border-radius:8px;padding:18px 20px;margin-bottom:16px;">
      <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:{_MUTED};text-transform:uppercase;margin-bottom:10px;">Model Performance Last Week</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;">
        <div>
          <div style="font-size:22px;font-weight:800;color:{model_color};">{units_str}</div>
          <div style="font-size:11px;color:{_MUTED};margin-top:2px;">Net units</div>
        </div>
        <div>
          <div style="font-size:22px;font-weight:800;color:{_SLATE};">{model_result_str}</div>
          <div style="font-size:11px;color:{_MUTED};margin-top:2px;">{m['total']} settled bets</div>
        </div>
        <div>
          <div style="font-size:22px;font-weight:800;color:{_GREEN if m.get('avg_clv') and m['avg_clv'] > 0 else _MUTED};">{clv_str}</div>
          <div style="font-size:11px;color:{_MUTED};margin-top:2px;">Avg CLV</div>
        </div>
      </div>
      {"" if not m.get("best_market") else f'<div style="margin-top:12px;font-size:12px;color:{_MUTED};">Best market: <strong style="color:{_SLATE};">{m["best_market"]}</strong> — {m["best_market_hr"]}% hit rate this week</div>'}
    </div>"""

    # User stats block (Pro/Elite, or if they have picks)
    user_block = ""
    if is_pro and user_stats:
        u = user_stats
        u_color = _GREEN if u["net_units"] >= 0 else "#ef4444"
        u_units = _units_fmt(u["net_units"])
        u_clv   = f"+{u['avg_clv']}%" if u.get("avg_clv") and u["avg_clv"] > 0 else (f"{u['avg_clv']}%" if u.get("avg_clv") is not None else "—")
        user_block = f"""
    <div style="background:{_WHITE};border:1px solid {_BORDER};border-left:3px solid #8b5cf6;border-radius:8px;padding:18px 20px;margin-bottom:16px;">
      <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:{_MUTED};text-transform:uppercase;margin-bottom:10px;">Your Picks Last Week</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;">
        <div>
          <div style="font-size:22px;font-weight:800;color:{u_color};">{u_units}</div>
          <div style="font-size:11px;color:{_MUTED};margin-top:2px;">Net units</div>
        </div>
        <div>
          <div style="font-size:22px;font-weight:800;color:{_SLATE};">{u['won']}W / {u['lost']}L</div>
          <div style="font-size:11px;color:{_MUTED};margin-top:2px;">{u['total']} settled picks</div>
        </div>
        {"" if not u.get("avg_clv") else f'<div><div style="font-size:22px;font-weight:800;color:{_GREEN if u.get(\"avg_clv\", 0) > 0 else _MUTED};">{u_clv}</div><div style="font-size:11px;color:{_MUTED};margin-top:2px;">Avg closing line value</div></div>'}
      </div>
      <p style="margin:12px 0 0;">
        <a href="{SITE_URL}/my-picks" style="font-size:12px;color:{_GREEN};text-decoration:none;font-weight:600;">View full history →</a>
      </p>
    </div>"""
    elif is_pro and not user_stats:
        user_block = f"""
    <div style="background:#fafafa;border:1px solid {_BORDER};border-radius:8px;padding:14px 20px;margin-bottom:16px;">
      <div style="font-size:13px;color:{_MUTED};">No settled picks last week. <a href="{SITE_URL}/matches" style="color:{_GREEN};text-decoration:none;font-weight:600;">Start tracking your picks →</a></div>
    </div>"""
    elif not is_pro:
        user_block = f"""
    <div style="background:{_GREEN_BG};border:1px solid {_GREEN_BD};border-radius:8px;padding:16px 20px;margin-bottom:16px;">
      <div style="font-size:14px;font-weight:700;color:{_GREEN_DARK};">Track your own picks with Pro</div>
      <p style="color:#166534;font-size:13px;margin:4px 0 12px;">See your weekly W/L, units profit, and closing line value alongside the model's performance.</p>
      <a href="{SITE_URL}/pricing" style="display:inline-block;background:{_GREEN};color:{_WHITE};font-size:12px;font-weight:600;padding:7px 14px;border-radius:6px;text-decoration:none;">See Pro plans →</a>
    </div>"""

    # Upcoming matches table
    upcoming_rows = "".join(_match_row_html(m) for m in upcoming)
    upcoming_block = ""
    if upcoming_rows:
        upcoming_block = f"""
    <div style="margin-top:8px;">
      <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:{_MUTED};text-transform:uppercase;margin-bottom:10px;">This Week's Big Matches</div>
      <table style="width:100%;border-collapse:collapse;background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;overflow:hidden;">
        <tbody>{upcoming_rows}</tbody>
      </table>
      <p style="margin-top:10px;margin-bottom:0;">
        <a href="{SITE_URL}/matches" style="font-size:13px;color:{_GREEN};text-decoration:none;font-weight:600;">View all this week's matches →</a>
      </p>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:{_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
    <tr><td align="center" style="padding:32px 16px 24px;">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="background:{_SITE_BG};border-radius:10px 10px 0 0;padding:24px 32px;text-align:center;">
            <a href="{SITE_URL}" style="text-decoration:none;display:inline-block;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.04em;">ODDS</span><span style="font-size:28px;font-weight:800;color:{_GREEN};letter-spacing:0.04em;">INTEL</span>
            </a>
            <div style="font-size:13px;color:#94a3b8;margin-top:6px;font-weight:600;">Week in Review · {week_label}</div>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background:{_WHITE};padding:24px 32px 28px;border-left:1px solid {_BORDER};border-right:1px solid {_BORDER};border-top:none;">
            {model_block}
            {user_block}
            {upcoming_block}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-radius:0 0 10px 10px;border:1px solid {_BORDER};border-top:none;padding:18px 32px;text-align:center;">
            <p style="margin:0 0 6px;font-size:12px;color:{_MUTED};">
              You're receiving this because weekly reports are enabled in your
              <a href="{SITE_URL}" style="color:{_GREEN};text-decoration:none;font-weight:600;">OddsIntel</a> account.
            </p>
            <p style="margin:0 0 10px;font-size:12px;">
              <a href="{unsubscribe_url}" style="color:{_MUTED};text-decoration:underline;">Manage preferences</a>
              &nbsp;·&nbsp;
              <a href="{value_bets_url}" style="color:{_MUTED};text-decoration:underline;">Value bets</a>
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


# ── Resend sender (reused from email_digest) ───────────────────────────────

def send_via_resend(to_email: str, subject: str, html: str) -> tuple[str | None, str | None]:
    import httpx
    if not RESEND_API_KEY:
        return None, "RESEND_API_KEY not set"
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to_email], "subject": subject, "html": html},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id"), None
        return None, f"Resend {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return None, str(e)[:200]


# ── Main ───────────────────────────────────────────────────────────────────

def run_weekly_digest(dry_run: bool = False, limit: int | None = None, to: str | None = None):
    today      = date.today()
    week_start = _week_start(today)  # This Monday = start of current week
    prev_week  = week_start - timedelta(days=7)  # Previous Monday = week we're reporting on

    console.print(f"[bold cyan]═══ OddsIntel Weekly Digest — week of {_week_label(prev_week)} ═══[/bold cyan]\n")

    if not RESEND_API_KEY and not dry_run:
        console.print("[red]RESEND_API_KEY not set — aborting.[/red]")
        return

    # Fetch shared data
    model_stats = fetch_model_week_stats(prev_week)
    upcoming    = fetch_top_upcoming_matches(week_start)  # current week's upcoming matches

    console.print(f"Model bets last week: {model_stats['total']} ({model_stats['won']}W/{model_stats['lost']}L, {_units_fmt(model_stats['net_units'])})")
    console.print(f"Upcoming top matches: {len(upcoming)}")

    # --to: send a one-off test email without touching the subscriber list or log
    if to:
        console.print(f"\n[yellow]Test send → {to} (elite tier, no log written)[/yellow]")
        subject, html = build_weekly_email(to, "elite", week_start, model_stats, None, upcoming)
        if dry_run:
            console.print(f"[dim]WOULD SEND:[/dim] {subject}")
            return
        resend_id, error = send_via_resend(to, subject, html)
        if error:
            console.print(f"[red]✗ {error}[/red]")
        else:
            console.print(f"[green]✓ Sent — id={resend_id}[/green]")
        return

    users = fetch_subscribed_users()
    console.print(f"Subscribed users:     {len(users)}\n")

    if limit is not None:
        users = users[:limit]

    sent = skipped = failed = 0

    for user in users:
        uid   = user["id"]
        email = user["email"]
        tier  = "elite" if user.get("is_superadmin") else (user.get("tier") or "free")

        if already_sent(uid, week_start):
            skipped += 1
            continue

        user_stats = fetch_user_week_stats(uid, prev_week) if tier in ("pro", "elite") else None
        subject, html = build_weekly_email(email, tier, week_start, model_stats, user_stats, upcoming)

        if dry_run:
            console.print(f"[dim]WOULD SEND to {email} ({tier}):[/dim] {subject}")
            log_send(uid, week_start, tier, email, None, "skipped")
            skipped += 1
            continue

        resend_id, error = send_via_resend(email, subject, html)
        if error:
            console.print(f"  [red]✗ {email} ({tier}): {error}[/red]")
            log_send(uid, week_start, tier, email, None, "failed", error)
            failed += 1
        else:
            console.print(f"  [green]✓ {email} ({tier}) — id={resend_id}[/green]")
            log_send(uid, week_start, tier, email, resend_id, "sent")
            sent += 1

    console.print(f"\n[bold]Done:[/bold] {sent} sent | {skipped} skipped | {failed} failed")
    if dry_run:
        console.print("[yellow](dry-run — no emails sent)[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send weekly performance email digest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit",   type=int, default=None)
    parser.add_argument("--to",      type=str, default=None, help="Send a one-off test email to this address (skips subscriber list and log)")
    args = parser.parse_args()
    run_weekly_digest(dry_run=args.dry_run, limit=args.limit, to=args.to)
