"""
COOLBET-DAILY-SUMMARY (C1, 2026-06-16).

One Telegram per day at 08:00 UTC: tells the operator at a glance whether
the placement stack is healthy, what fired in the last 24h, and what's
queued for today. Designed for lock-screen readability — ~10 lines max.

Why this exists:
COOLBET-DAEMON-ALERTS pushes Telegram on FAILURES. C2 added DB heartbeats
so the catch-net's liveness is observable. Neither answers "is everything
fine right now?" without prompting from the operator. The daily summary
is the proactive confirmation — if you DON'T receive it at 08:00 UTC, the
scheduler itself is down; if you DO receive it and the numbers look
normal, you can ignore the system until tonight.

What's in scope:
- Daemon health (last tick age, last tick result, JWT TTL)
- Catch-net liveness (last run age, recent sends)
- 24h activity (real_bets placed + settled + PnL)
- Today's calibrated queue (count, first KO)
- Warnings (only when applicable — silent when healthy)

Out of scope:
- Full performance dashboard (bots page covers that)
- Per-pick breakdown (signaler covers that)
- CS2 / tennis / other sports (this is Coolbet soccer placement specifically)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Stale thresholds — beyond these, surface a warning line. Match the
# operational cadences they relate to:
#   • Mac daemon ticks every 30 min → flag at 60 min
#   • Catch-net runs every 5 min → flag at 15 min
#   • Scheduler heartbeat every 5 min → flag at 15 min
DAEMON_STALE_MIN = int(os.getenv("COOLBET_DAILY_DAEMON_STALE_MIN", "60"))
CATCHNET_STALE_MIN = int(os.getenv("COOLBET_DAILY_CATCHNET_STALE_MIN", "15"))
SCHEDULER_HB_STALE_MIN = int(os.getenv("COOLBET_DAILY_SCHEDULER_STALE_MIN", "15"))
JWT_TTL_WARN_MIN = int(os.getenv("COOLBET_DAILY_JWT_TTL_WARN_MIN", "5"))


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _gather_state() -> dict:
    """Pull all the numbers in one DB hit (or two). Returns a flat dict
    the formatter can iterate over without re-querying."""
    from workers.api_clients.db import execute_query

    state = execute_query(
        """SELECT NOW() AS now_utc,
                  last_heartbeat_at, last_heartbeat_ok,
                  jwt_exp_at,
                  mac_daemon_last_tick_at, mac_daemon_last_tick_result,
                  prekickoff_last_run_at, prekickoff_last_run_result,
                  placement_paused, placement_paused_reason,
                  EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at))       AS hb_age_s,
                  EXTRACT(EPOCH FROM (jwt_exp_at - NOW()))               AS jwt_ttl_s,
                  EXTRACT(EPOCH FROM (NOW() - mac_daemon_last_tick_at)) AS daemon_age_s,
                  EXTRACT(EPOCH FROM (NOW() - prekickoff_last_run_at))  AS prekickoff_age_s
             FROM coolbet_session_state WHERE id = 1"""
    )
    out: dict = dict(state[0]) if state else {}

    # 24h real-bet activity. Count + W/L/P + PnL. coolbet only — explicit
    # bookmaker filter to keep the line semantically correct.
    activity = execute_query(
        """SELECT COUNT(*)                                    AS placed,
                  COUNT(*) FILTER (WHERE result = 'won')      AS won,
                  COUNT(*) FILTER (WHERE result = 'lost')     AS lost,
                  COUNT(*) FILTER (WHERE result = 'pending')  AS pending,
                  COALESCE(SUM(pnl) FILTER (WHERE result IN ('won','lost','push')), 0) AS pnl_24h
             FROM real_bets
            WHERE bookmaker = 'coolbet'
              AND placed_at >= NOW() - INTERVAL '24 hours'"""
    )
    out["activity_24h"] = dict(activity[0]) if activity else {}

    # Today's calibrated queue. Mirror the cherry-pick placer's gate so
    # the count means the same thing it does to the daemon.
    queue = execute_query(
        """SELECT COUNT(*)              AS queued,
                  MIN(m.date)           AS first_ko
             FROM simulated_bets sb
             JOIN bots          b   ON b.id = sb.bot_id
             JOIN matches       m   ON m.id = sb.match_id
            WHERE sb.result = 'pending'
              AND sb.combo_legs IS NULL
              AND sb.user_placed_at IS NULL
              AND sb.user_skipped_at IS NULL
              AND b.maturity_label = 'calibrated'
              AND m.date > NOW()
              AND m.date < NOW() + INTERVAL '24 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM real_bets rb
                   WHERE rb.match_id  = sb.match_id
                     AND rb.market    = sb.market
                     AND rb.selection = sb.selection
              )"""
    )
    out["queue_today"] = dict(queue[0]) if queue else {}

    return out


def _format_summary(s: dict) -> str:
    """Compose the lock-screen message. Keep it ≤12 lines so a phone
    notification preview shows the headline lines without truncation."""
    import html as _html

    daemon_age = s.get("daemon_age_s")
    hb_age = s.get("hb_age_s")
    prekickoff_age = s.get("prekickoff_age_s")
    jwt_ttl = s.get("jwt_ttl_s")

    daemon_result = s.get("mac_daemon_last_tick_result") or {}
    if isinstance(daemon_result, str):
        import json as _json
        try:
            daemon_result = _json.loads(daemon_result)
        except Exception:
            daemon_result = {}
    prek_result = s.get("prekickoff_last_run_result") or {}
    if isinstance(prek_result, str):
        import json as _json
        try:
            prek_result = _json.loads(prek_result)
        except Exception:
            prek_result = {}

    daemon_errs = int(daemon_result.get("errors") or 0) if daemon_result else 0
    daemon_placed = int(daemon_result.get("placed") or 0) if daemon_result else 0
    prek_sent = int(prek_result.get("sent") or 0) if prek_result else 0

    activity = s.get("activity_24h") or {}
    queue = s.get("queue_today") or {}

    placed_24h = int(activity.get("placed") or 0)
    won_24h = int(activity.get("won") or 0)
    lost_24h = int(activity.get("lost") or 0)
    pnl_24h = float(activity.get("pnl_24h") or 0)

    queued = int(queue.get("queued") or 0)
    first_ko = queue.get("first_ko")
    first_ko_str = ""
    if first_ko:
        if isinstance(first_ko, datetime):
            first_ko_str = first_ko.astimezone(timezone.utc).strftime("%H:%M UTC")
        else:
            first_ko_str = str(first_ko)

    # Health glyph: green if everything fresh + no errors, yellow if any
    # warning, red if anything actively broken or paused. Compose AFTER
    # gathering warnings so we don't double-evaluate.
    warnings: list[str] = []
    if s.get("placement_paused"):
        warnings.append(f"⛔ placement_paused: {s.get('placement_paused_reason') or 'unknown'}")
    if daemon_age is None or daemon_age > DAEMON_STALE_MIN * 60:
        warnings.append(f"🛑 daemon last tick {_fmt_age(daemon_age)} ago (stale)")
    elif daemon_errs > 0:
        warnings.append(f"⚠ daemon last tick errored")
    if hb_age is None or hb_age > SCHEDULER_HB_STALE_MIN * 60:
        warnings.append(f"🛑 Scheduler heartbeat {_fmt_age(hb_age)} ago (stale)")
    if prekickoff_age is not None and prekickoff_age > CATCHNET_STALE_MIN * 60:
        warnings.append(f"🛑 catch-net {_fmt_age(prekickoff_age)} ago (cron silent)")
    # Only flag JWT-low when token is still valid but TTL is shrinking
    # (proactive refresh window). A fully expired JWT is already covered
    # by the daemon-stale / the VPS-HB warnings above; a duplicate
    # "TTL -93399s" line just adds noise.
    if jwt_ttl is not None and 0 < jwt_ttl < JWT_TTL_WARN_MIN * 60:
        warnings.append(f"⚠ JWT TTL {_fmt_age(jwt_ttl)} (proactive refresh should kick in)")

    glyph = "🟢" if not warnings else ("🟡" if all(w.startswith("⚠") for w in warnings) else "🔴")

    lines = [
        f"{glyph} <b>Coolbet daily — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}</b>",
        f"",
        f"🤖 Daemon: tick {_fmt_age(daemon_age)} ago · last: placed={daemon_placed} errors={daemon_errs}",
        f"🔑 JWT: TTL {_fmt_age(jwt_ttl) if (jwt_ttl is not None and jwt_ttl > 0) else 'expired'}",
        f"🛰 Scheduler HB: {_fmt_age(hb_age)} ago · ok={bool(s.get('last_heartbeat_ok'))}",
        f"🚨 Catch-net: {_fmt_age(prekickoff_age)} ago · sent={prek_sent}",
        f"",
        f"📊 24h: {placed_24h} placed · W{won_24h}/L{lost_24h} · pnl €{pnl_24h:+.2f}",
        f"📅 Today: {queued} calibrated picks queued"
        + (f" · first KO {first_ko_str}" if first_ko_str else ""),
    ]
    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(_html.escape(w, quote=False))
    return "\n".join(lines)


def run_daily_summary(*, dry_run: bool = False) -> dict:
    """Compose + send the daily Telegram summary. Returns a small dict so
    the scheduler wrapper can log a one-liner. dry_run skips the Telegram
    send AND returns the message body so manual probes can preview without
    pushing to the operator's phone."""
    from workers.notify.telegram import send_telegram

    state = _gather_state()
    msg = _format_summary(state)

    if dry_run:
        return {"sent": False, "preview": msg}

    # Dedup by date so a re-fire on the same day doesn't double-send.
    # Window 23h ensures the dedup releases before the next morning's run.
    #
    # INLINE-HEAL-BUTTONS (2026-06-17): attach action buttons so the
    # operator can heal/pause/resume from the morning summary too —
    # not just from failure alerts. Same callback prefixes as the
    # daemon-fail-burst alert.
    reply_markup = {
        "inline_keyboard": [[
            {"text": "🔄 Heal",   "callback_data": "coolbet-heal:"},
            {"text": "⏸ Pause",  "callback_data": "coolbet-pause:"},
            {"text": "▶ Resume", "callback_data": "coolbet-resume:"},
        ]],
    }
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    tg_id = send_telegram(
        msg,
        dedup_key=f"daily-summary-{date_key}",
        dedup_window_s=82800,  # 23h
        reply_markup=reply_markup,
    )
    return {"sent": tg_id is not None, "telegram_message_id": tg_id}


def main() -> int:
    """CLI entrypoint. --dry-run previews the message without sending."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                    help="Preview the message without sending Telegram.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    r = run_daily_summary(dry_run=args.dry_run)
    if args.dry_run:
        print(r.get("preview"))
    log.info("daily summary: sent=%s", r.get("sent"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
