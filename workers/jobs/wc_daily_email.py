"""
OddsIntel — WC2026 Daily Preview Email (WC-F4)

Sends one email per opted-in user per day during the World Cup window. Cron
fires at 07:30 UTC, after:
    - 04:00 morning_pipeline (raw predictions + national-team v1)
    - 06:00 wc_market_consensus (eloratings + Pinnacle + Smarkets scrape)
    - 06:30 wc_monte_carlo
    - 06:30 write_blended_predictions (own × market via wc_blender)

…so every fixture in today's slate has the freshest 1X2 the model produces.

Sections in the email:
    1) "Yesterday's results" — hit/miss summary across yesterday's settled
       WC fixtures: did the model's pick match the actual outcome?
    2) "Today's WC matches" — kickoff, both sides, our pick + confidence,
       and the market-disagreement footnote when meaningful (≥10pp).
    3) "Biggest market disagreement" — one featured matchup where our model
       and the market consensus differ by ≥10pp on the home side. Skipped
       entirely when wc_market_consensus is empty or no fixture qualifies.
    4) CTA to /world-cup/predictions-record so users can audit our track
       record after a few rounds.

Opt-in: reuses user_notification_settings.email_digest_enabled — anyone who
has the regular OddsIntel digest enabled receives the WC variant during the
tournament window. The two emails are independent (separate dedupe tables),
so a user can get both on the same day without one blocking the other.

Idempotency: `wc_email_log` UNIQUE(user_id, email_date). Cron misfires or a
manual rerun see the lock and skip silently.

Tolerates:
    - empty wc_market_consensus (A3 scraper hasn't run yet) → skips market
      disagreement section, still sends with model picks only.
    - missing blended predictions → falls back to national_team_v1 raw.
    - zero settled-yesterday rows → omits the results section.
    - zero today fixtures → sends nothing (no point spamming on rest days).

Usage:
    python -m workers.jobs.wc_daily_email
    python -m workers.jobs.wc_daily_email --dry-run        # print, no send
    python -m workers.jobs.wc_daily_email --limit 3        # send to ≤3 users
    python -m workers.jobs.wc_daily_email --date 2026-06-15 # ad-hoc backfill
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query, execute_write
# Reuse the existing Resend client (httpx-backed) rather than adding a new one.
from workers.jobs.email_digest import (
    send_via_resend,
    fetch_subscribed_users,
    RESEND_API_KEY,
    SITE_URL,
)

console = Console()

# Tournament constants — keep in sync with workers/jobs/wc_match_previews.py.
# We intentionally use the broader WINDOW_START (2026-06-04, friendlies +
# pre-tournament coverage) so the email lights up a few days before the
# official kickoff on 2026-06-11.
WC_LEAGUE_AF_ID = 1
WC_EMAIL_WINDOW_START = date(2026, 6, 4)
WC_EMAIL_WINDOW_END = date(2026, 7, 19)

# Materiality threshold for the "biggest disagreement" section, in
# percentage points on the home side. <10pp is noise once you account for
# Pinnacle vig and 6.6k-international ELO error bars.
DISAGREEMENT_PCT_THRESHOLD = 10


# ── Data fetchers ──────────────────────────────────────────────────────────

def fetch_today_wc_fixtures(target_date: str) -> list[dict]:
    """
    Return today's scheduled WC fixtures with the freshest 1X2 we have.

    Prefers `national_team_v1_blended` (own × market) over the raw
    `national_team_v1` — matches the source-preference logic in
    workers/jobs/wc_match_previews.py so the email shows the same numbers
    users see on the match-detail page.
    """
    rows = execute_query(
        """
        SELECT
            m.id            AS match_id,
            m.date          AS kickoff,
            m.venue_name,
            ht.name         AS home_team,
            at.name         AS away_team,
            l.name          AS league,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS home_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS draw_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS away_prob,
            CASE
                WHEN BOOL_OR(p.source='national_team_v1_blended') THEN 'national_team_v1_blended'
                WHEN BOOL_OR(p.source='national_team_v1')         THEN 'national_team_v1'
                ELSE NULL
            END AS model_source
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
            AND p.source IN ('national_team_v1', 'national_team_v1_blended')
        WHERE l.api_football_id = %s
          AND m.status = 'scheduled'
          AND m.date::date = %s
        GROUP BY m.id, m.date, m.venue_name, ht.name, at.name, l.name
        ORDER BY m.date ASC
        """,
        [WC_LEAGUE_AF_ID, target_date],
    )
    return rows or []


def fetch_market_consensus_map(match_ids: list[str]) -> dict[str, dict]:
    """Bulk-load market consensus for a list of fixtures. Empty dict if none."""
    if not match_ids:
        return {}
    # Cast to uuid[] explicitly — psycopg2 sends the Python list as text[],
    # which fails the uuid = text comparison without an explicit cast.
    rows = execute_query(
        """
        SELECT match_id, home_prob, draw_prob, away_prob, n_sources
        FROM wc_market_consensus
        WHERE match_id = ANY(%s::uuid[])
        """,
        [match_ids],
    )
    out: dict[str, dict] = {}
    for r in rows or []:
        out[str(r["match_id"])] = {
            "home": float(r["home_prob"]),
            "draw": float(r["draw_prob"]),
            "away": float(r["away_prob"]),
            "n_sources": int(r.get("n_sources") or 0),
        }
    return out


def fetch_yesterday_results(yesterday: str) -> list[dict]:
    """
    Return yesterday's settled WC fixtures with our model's pick (from the
    same source-preference) joined to the actual `result` so the caller can
    compute hits/misses without a second query.
    """
    rows = execute_query(
        """
        SELECT
            m.id            AS match_id,
            ht.name         AS home_team,
            at.name         AS away_team,
            m.result        AS actual_result,
            m.score_home,
            m.score_away,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS home_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS draw_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS away_prob
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
            AND p.source IN ('national_team_v1', 'national_team_v1_blended')
        WHERE l.api_football_id = %s
          AND m.status = 'finished'
          AND m.date::date = %s
          AND m.result IS NOT NULL
        GROUP BY m.id, ht.name, at.name, m.result, m.score_home, m.score_away
        ORDER BY m.date ASC
        """,
        [WC_LEAGUE_AF_ID, yesterday],
    )
    return rows or []


# ── Idempotency / log helpers ──────────────────────────────────────────────

def already_sent(user_id: str, email_date: str) -> bool:
    rows = execute_query(
        "SELECT id FROM wc_email_log WHERE user_id = %s AND email_date = %s",
        [user_id, email_date],
    )
    return bool(rows)


def log_send(user_id: str, email_date: str, tier: str, email_to: str,
             resend_id: str | None, status: str, fixture_count: int,
             settled_count: int, error_msg: str | None = None) -> None:
    execute_write(
        """
        INSERT INTO wc_email_log
          (user_id, email_date, tier, sent_at, resend_id, email_to,
           status, fixture_count, settled_count, error_msg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, email_date) DO NOTHING
        """,
        [
            user_id, email_date, tier,
            datetime.now(timezone.utc).isoformat(),
            resend_id, email_to, status, fixture_count, settled_count,
            error_msg,
        ],
    )


# ── Formatting helpers ─────────────────────────────────────────────────────

def _pct(p) -> int | None:
    if p is None:
        return None
    try:
        return round(100 * float(p))
    except (TypeError, ValueError):
        return None


def _kickoff_hhmm(raw) -> str:
    """'2026-06-15T19:00:00+00:00' → '19:00 UTC'."""
    if raw is None:
        return "TBD"
    try:
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = raw
        return dt.strftime("%H:%M UTC")
    except Exception:
        return str(raw)[11:16] + " UTC"


def _pick_from_probs(home: str, away: str, home_pct: int | None,
                     draw_pct: int | None, away_pct: int | None) -> dict | None:
    """Return {'label', 'side', 'confidence'} for the model's 1X2 pick, or None."""
    if home_pct is None or draw_pct is None or away_pct is None:
        return None
    options = [
        ("home", home, home_pct),
        ("draw", "Draw", draw_pct),
        ("away", away, away_pct),
    ]
    side, label, confidence = max(options, key=lambda t: t[2])
    return {"side": side, "label": label, "confidence": confidence}


def _result_letter(actual: str | None, pick_side: str | None) -> str:
    """HIT / MISS / — depending on whether the model's pick matched reality."""
    if actual is None or pick_side is None:
        return "—"
    return "HIT" if actual == pick_side else "MISS"


# ── Markdown body builder ──────────────────────────────────────────────────

def _format_match_line(m: dict, market: dict | None) -> str:
    """One bullet for the "Today's WC matches" section."""
    home = m["home_team"]
    away = m["away_team"]
    kickoff = _kickoff_hhmm(m.get("kickoff"))

    home_pct = _pct(m.get("home_prob"))
    draw_pct = _pct(m.get("draw_prob"))
    away_pct = _pct(m.get("away_prob"))
    pick = _pick_from_probs(home, away, home_pct, draw_pct, away_pct)

    if pick is None:
        return f"- **{kickoff}** — {home} vs {away}: model has no 1X2 yet"

    pick_str = f"**{pick['label']}** @ {pick['confidence']}%"
    line = f"- **{kickoff}** — {home} vs {away}: pick {pick_str}"

    # Footnote on material disagreement (≥10pp on the home side).
    if market is not None and home_pct is not None:
        m_home = _pct(market.get("home"))
        if m_home is not None and abs(home_pct - m_home) >= DISAGREEMENT_PCT_THRESHOLD:
            line += (
                f" _(market has {home} at {m_home}%, "
                f"we say {home_pct}% — {abs(home_pct - m_home)}pp gap)_"
            )
    return line


def _find_biggest_disagreement(fixtures: list[dict],
                               market_map: dict[str, dict]) -> dict | None:
    """Pick the single fixture with the largest |our_home% − market_home%| ≥ threshold."""
    best = None
    best_gap = 0
    for fx in fixtures:
        mc = market_map.get(str(fx["match_id"]))
        if not mc:
            continue
        home_pct = _pct(fx.get("home_prob"))
        m_home = _pct(mc.get("home"))
        if home_pct is None or m_home is None:
            continue
        gap = abs(home_pct - m_home)
        if gap >= DISAGREEMENT_PCT_THRESHOLD and gap > best_gap:
            best_gap = gap
            best = {"fixture": fx, "market": mc, "gap": gap, "home_pct": home_pct, "m_home": m_home}
    return best


def _settled_summary(settled: list[dict]) -> tuple[int, int, list[str]]:
    """Return (hits, misses, lines) for the yesterday section."""
    hits = 0
    misses = 0
    lines: list[str] = []
    for r in settled:
        home = r["home_team"]
        away = r["away_team"]
        actual = r.get("actual_result")
        pick = _pick_from_probs(
            home, away,
            _pct(r.get("home_prob")),
            _pct(r.get("draw_prob")),
            _pct(r.get("away_prob")),
        )
        sh = r.get("score_home")
        sa = r.get("score_away")
        score_str = f"{sh}-{sa}" if sh is not None and sa is not None else "?-?"
        outcome = _result_letter(actual, pick["side"] if pick else None)
        if outcome == "HIT":
            hits += 1
        elif outcome == "MISS":
            misses += 1
        pick_part = (
            f" — picked **{pick['label']}** @ {pick['confidence']}%"
            if pick else " — no pick"
        )
        lines.append(f"- [{outcome}] {home} {score_str} {away}{pick_part}")
    return hits, misses, lines


def build_email_markdown(
    target_date: str,
    fixtures: list[dict],
    market_map: dict[str, dict],
    settled: list[dict],
) -> tuple[str, str]:
    """
    Return (subject, markdown_body). Keep it plain Markdown — the existing
    digest renders HTML, but the WC preview is a different beast (designed
    to skim on a phone before kickoff) so Markdown reads cleaner. Resend
    accepts plain text or HTML — we send the markdown as text/html wrapped
    in a `<pre>` so client rendering stays consistent.
    """
    display_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%b %d")
    n_fixtures = len(fixtures)
    subject = (
        f"OddsIntel WC — {n_fixtures} match{'es' if n_fixtures != 1 else ''} "
        f"today ({display_date})"
    )

    parts: list[str] = []
    parts.append(f"# OddsIntel WC2026 — {display_date}\n")

    # ── Section 1: Yesterday's results (only when there were any) ──────
    if settled:
        hits, misses, lines = _settled_summary(settled)
        total = hits + misses
        rate = f"{hits}/{total}" if total else "0/0"
        parts.append(f"## Yesterday's results — {rate}\n")
        parts.extend(lines)
        parts.append("")  # blank line

    # ── Section 2: Today's WC matches ──────────────────────────────────
    parts.append(f"## Today's WC matches — {display_date}\n")
    if not fixtures:
        parts.append("_No World Cup fixtures today — see you tomorrow._\n")
    else:
        for fx in fixtures:
            mc = market_map.get(str(fx["match_id"]))
            parts.append(_format_match_line(fx, mc))
        parts.append("")

    # ── Section 3: Biggest disagreement (when material) ────────────────
    if fixtures and market_map:
        big = _find_biggest_disagreement(fixtures, market_map)
        if big:
            fx = big["fixture"]
            home = fx["home_team"]
            away = fx["away_team"]
            home_pct = big["home_pct"]
            m_home = big["m_home"]
            n_src = big["market"].get("n_sources", 0)
            leaner = home if home_pct > m_home else away
            parts.append("## Biggest market disagreement\n")
            parts.append(
                f"**{home} vs {away}** — our model leans {leaner}. "
                f"We have {home} at **{home_pct}%**; the market consensus "
                f"({n_src} source{'s' if n_src != 1 else ''}) has {home} at "
                f"**{m_home}%** — a **{big['gap']}-point** gap on the home side.\n"
            )

    # ── Footer / CTA ───────────────────────────────────────────────────
    parts.append(
        f"---\n"
        f"\n[See our full WC predictions record →]({SITE_URL}/world-cup/predictions-record)\n"
        f"\n_You're receiving this because you have daily emails enabled at OddsIntel. "
        f"[Manage preferences]({SITE_URL}/profile?tab=notifications)._\n"
        f"\n_Not financial or gambling advice. Please gamble responsibly._\n"
    )

    return subject, "\n".join(parts)


def markdown_to_html(md: str) -> str:
    """
    Minimal Markdown → HTML wrapper. We don't pull in a real renderer
    because the body is tiny and the structure is fully under our control
    (headings, bullets, bold/italic, links, hr). Resend accepts text/html.
    """
    import html as _h
    import re

    lines = md.split("\n")
    out: list[str] = []
    in_ul = False

    def _close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.rstrip()

        if not line:
            _close_ul()
            out.append("")
            continue

        # Horizontal rule
        if line.strip() == "---":
            _close_ul()
            out.append("<hr style='border:none;border-top:1px solid #e2e8f0;margin:18px 0;'>")
            continue

        # Headings
        if line.startswith("# "):
            _close_ul()
            text = _inline(line[2:])
            out.append(f"<h1 style='font-size:22px;color:#0f172a;margin:0 0 12px;'>{text}</h1>")
            continue
        if line.startswith("## "):
            _close_ul()
            text = _inline(line[3:])
            out.append(
                "<h2 style='font-size:15px;color:#16a34a;"
                "text-transform:uppercase;letter-spacing:0.06em;"
                f"margin:18px 0 8px;'>{text}</h2>"
            )
            continue

        # Bullets
        if line.startswith("- "):
            if not in_ul:
                out.append("<ul style='margin:0 0 8px 18px;padding:0;color:#1e293b;font-size:14px;line-height:1.6;'>")
                in_ul = True
            text = _inline(line[2:])
            out.append(f"<li>{text}</li>")
            continue

        # Paragraph
        _close_ul()
        out.append(
            f"<p style='font-size:14px;color:#1e293b;line-height:1.6;margin:0 0 10px;'>"
            f"{_inline(line)}</p>"
        )

    _close_ul()

    body = "\n".join(out)
    return (
        "<!DOCTYPE html><html><body style='margin:0;padding:24px;"
        "background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"
        "\"Segoe UI\",Helvetica,Arial,sans-serif;'>"
        "<div style='max-width:640px;margin:0 auto;background:#ffffff;"
        "border-radius:10px;padding:28px 32px;border:1px solid #e2e8f0;'>"
        f"{body}"
        "</div></body></html>"
    )


def _inline(text: str) -> str:
    """Inline Markdown: **bold**, _italic_, [link](url). Escape stray HTML."""
    import html as _h
    import re

    escaped = _h.escape(text, quote=False)

    # Links — render before bold so [text](url) inside ** doesn't double-process.
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f"<a href=\"{m.group(2)}\" style=\"color:#16a34a;text-decoration:none;font-weight:600;\">{m.group(1)}</a>",
        escaped,
    )
    # Bold
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    # Italic — use _underscore_ form only; *star* italic collides with bold.
    escaped = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", escaped)

    return escaped


# ── Main ───────────────────────────────────────────────────────────────────

def run_wc_daily_email(target_date: str | None = None, dry_run: bool = False,
                       limit: int | None = None) -> dict:
    """
    Send the WC daily preview email to all opted-in users.

    Returns a summary dict so the scheduler / CLI / tests can introspect:
        {"sent": int, "skipped": int, "failed": int,
         "fixture_count": int, "settled_count": int, "subject": str|None}
    """
    today = target_date or date.today().isoformat()
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()

    console.print(f"[bold cyan]═══ WC Daily Email: {today} ═══[/bold cyan]\n")

    # Soft env guard — same posture as email_digest.py: dry-run works without
    # the key, live sends abort cleanly with a clear message.
    if not RESEND_API_KEY and not dry_run:
        console.print("[red]RESEND_API_KEY not set — aborting. Use --dry-run to preview.[/red]")
        return {"sent": 0, "skipped": 0, "failed": 0,
                "fixture_count": 0, "settled_count": 0, "subject": None}

    fixtures = fetch_today_wc_fixtures(today)
    settled = fetch_yesterday_results(yesterday)
    match_ids = [str(f["match_id"]) for f in fixtures]
    market_map = fetch_market_consensus_map(match_ids)

    console.print(f"Today fixtures:        {len(fixtures)}")
    console.print(f"Yesterday settled:     {len(settled)}")
    console.print(f"Market consensus rows: {len(market_map)}")

    if not fixtures and not settled:
        console.print("[yellow]No WC content today (no fixtures + no settled yesterday). Skipping.[/yellow]")
        return {"sent": 0, "skipped": 0, "failed": 0,
                "fixture_count": 0, "settled_count": 0, "subject": None}

    subject, md_body = build_email_markdown(today, fixtures, market_map, settled)
    html_body = markdown_to_html(md_body)

    users = fetch_subscribed_users()
    if limit is not None:
        users = users[:limit]
    console.print(f"Subscribed users:      {len(users)}\n")

    if dry_run:
        console.print(f"[dim]SUBJECT:[/dim] {subject}")
        console.print("[dim]── MARKDOWN BODY ──[/dim]")
        console.print(md_body)
        console.print("[dim]── END ──[/dim]\n")

    sent = skipped = failed = 0
    for user in users:
        uid = user["id"]
        email = user["email"]
        raw_tier = user.get("tier", "free")
        tier = "elite" if user.get("is_superadmin") else raw_tier

        if dry_run:
            # Don't touch the dedupe table in dry-run — lets the operator
            # preview without requiring migration 182 to be applied locally.
            console.print(f"[dim]WOULD SEND[/dim] → {email} ({tier})")
            skipped += 1
            continue

        if already_sent(uid, today):
            skipped += 1
            continue

        resend_id, error = send_via_resend(email, subject, html_body)
        if error:
            console.print(f"  [red]✗ {email} ({tier}): {error}[/red]")
            log_send(uid, today, tier, email, None, "failed",
                     len(fixtures), len(settled), error)
            failed += 1
        else:
            console.print(f"  [green]✓ {email} ({tier}) — id={resend_id}[/green]")
            log_send(uid, today, tier, email, resend_id, "sent",
                     len(fixtures), len(settled))
            sent += 1

    console.print(f"\n[bold]Done:[/bold] {sent} sent | {skipped} skipped | {failed} failed")
    if dry_run:
        console.print("[yellow](dry-run — no emails sent)[/yellow]")

    return {
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "fixture_count": len(fixtures),
        "settled_count": len(settled),
        "subject": subject,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the WC2026 daily preview email")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the email body without calling Resend or writing logs")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max users to send to (smoke / staged rollout)")
    parser.add_argument("--date", type=str, default=None,
                        help="Target date YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args()
    run_wc_daily_email(target_date=args.date, dry_run=args.dry_run, limit=args.limit)
