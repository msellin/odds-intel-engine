"""
COOLBET-DAEMON-ALERTS — pre-kickoff catch-net (2026-06-16).

Runs from Railway every 5 min. If a calibrated-bot pick is <90min from
kickoff AND not placed AND not skipped AND the Mac daemon looks stale or
broken, push an urgent Telegram so the operator can place from their
phone.

WHY THIS EXISTS:
The Mac daemon owns placement. The Telegram signaler (sent at pipeline
time, hours before KO) is the operator's awareness layer. But there's a
hole: if the daemon silently fails between the signal and KO — JWT expired,
Chrome logged out, FlareSolverr down — the operator has no second
reminder. On 2026-06-15/16, the daemon failed for 24h+ with no alert
and missed a calibrated pick.

This catch-net is the safety third leg. It runs on Railway so it's
independent of the Mac, queries `coolbet_session_state.mac_daemon_last_tick_at`
to detect daemon health, and only fires when (a) we have an unplaced
calibrated pick approaching KO, and (b) the daemon doesn't appear to be
handling it.

DEDUP: Telegram dedup key per simulated_bet_id with 1h window — each pick
gets at most one urgent message per hour. The regular bet-signal already
pinged the operator hours earlier; this is the "are you still going to
place this?" follow-up.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Pre-kickoff window. Lower bound is small-negative so a pick that just
# kicked off (Coolbet sometimes still accepts ~1-2 min after) still alerts.
# Upper bound is 90 min — gives the operator real time to react. Beyond
# that, the regular signal that fired hours earlier is the right channel.
PREKICKOFF_WINDOW_MIN_MINUTES = int(os.getenv("COOLBET_PREKICKOFF_MIN_MIN", "-5"))
PREKICKOFF_WINDOW_MAX_MINUTES = int(os.getenv("COOLBET_PREKICKOFF_MAX_MIN", "90"))

# How fresh the Mac daemon's last tick must be to be considered "alive".
# Daemon ticks every 30 min; allow 60 min before we conclude it's stuck.
MAC_DAEMON_STALE_AFTER_MINUTES = int(os.getenv("COOLBET_MAC_STALE_AFTER_MIN", "60"))


def _allowed_maturity_labels() -> list[str]:
    """Catch-net default = ['calibrated'] (real-money tier). Mirrors the
    placer's COOLBET_RECORD_ALLOWED_MATURITY env semantics but defaults
    to 'calibrated' when unset — the catch-net is explicitly about money-
    at-risk picks, never about firing on every bot."""
    raw = (os.getenv("COOLBET_RECORD_ALLOWED_MATURITY") or "").strip()
    if not raw or raw == "*":
        return ["calibrated"]
    return [s.strip() for s in raw.split(",") if s.strip()] or ["calibrated"]


def _mac_daemon_is_healthy() -> tuple[bool, str]:
    """Read `coolbet_session_state.mac_daemon_last_tick_at` +
    `mac_daemon_last_tick_result`. Healthy = last tick within
    MAC_DAEMON_STALE_AFTER_MINUTES AND errors=0. Returns (healthy, reason)
    so the alert payload can explain *why* we're firing the catch-net."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT mac_daemon_last_tick_at,
                      mac_daemon_last_tick_result,
                      EXTRACT(EPOCH FROM (NOW() - mac_daemon_last_tick_at)) AS age_s
               FROM coolbet_session_state WHERE id = 1"""
        )
        if not rows:
            return (False, "coolbet_session_state row missing")
        row = rows[0]
        if row.get("mac_daemon_last_tick_at") is None:
            return (False, "Mac daemon has never reported a tick")
        age_s = float(row.get("age_s") or 0)
        if age_s > MAC_DAEMON_STALE_AFTER_MINUTES * 60:
            return (False, f"last tick {int(age_s/60)}m ago (stale)")
        result = row.get("mac_daemon_last_tick_result") or {}
        # mac_daemon_last_tick_result is a JSONB column; psycopg2 may return
        # str or dict depending on the cursor. Normalise.
        if isinstance(result, str):
            try:
                import json as _json
                result = _json.loads(result)
            except Exception:
                result = {}
        if int(result.get("errors") or 0) > 0:
            return (False, "last tick errored")
        return (True, "daemon healthy")
    except Exception as e:
        # Fall closed on DB errors — better to send a possibly-redundant
        # alert than to suppress a real one because of a transient issue.
        log.warning("mac daemon health check failed (assuming stale): %s", e)
        return (False, f"health check failed: {e}")


def load_prekickoff_candidates() -> list[dict]:
    """Pre-KO picks that need the catch-net. Filters in order:

      - bots.maturity_label ∈ allowed list (default ['calibrated'])
      - simulated_bets.result = 'pending'
      - combo_legs IS NULL (singles only — combos out of scope)
      - signaled_at IS NOT NULL (only catch-net signals the operator
        already saw; don't double-up on the regular signaler)
      - user_placed_at IS NULL (operator hasn't tapped ✅ Placed)
      - user_skipped_at IS NULL (operator hasn't tapped ⏭ Skip)
      - NOT EXISTS in real_bets (daemon hasn't placed it)
      - KO within [PREKICKOFF_WINDOW_MIN_MINUTES, PREKICKOFF_WINDOW_MAX_MINUTES]
      - edge_percent ≥ per-market floor (mirrors placer)

    Returns rows sorted by KO ascending so the urgent picks render first."""
    from workers.api_clients.db import execute_query
    from workers.automation.coolbet_placer import _min_edge_for, _MIN_EDGE

    allowed = _allowed_maturity_labels()
    rows = execute_query(
        """
        SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
               sb.id             AS simulated_bet_id,
               sb.match_id,
               sb.market,
               sb.selection,
               sb.odds_at_pick,
               sb.edge_percent,
               sb.stake,
               sb.kelly_fraction,
               b.name            AS bot_name,
               b.maturity_label  AS bot_maturity,
               m.date            AS match_date,
               m.coolbet_match_id AS coolbet_match_id,
               ht.name           AS home_team,
               at2.name          AS away_team,
               l.name            AS league,
               EXTRACT(EPOCH FROM (m.date - NOW()))/60.0 AS minutes_to_ko
          FROM simulated_bets sb
          JOIN bots          b   ON b.id   = sb.bot_id
          JOIN matches       m   ON m.id   = sb.match_id
          JOIN teams         ht  ON ht.id  = m.home_team_id
          JOIN teams         at2 ON at2.id = m.away_team_id
          LEFT JOIN leagues  l   ON l.id   = m.league_id
         WHERE sb.result = 'pending'
           AND sb.combo_legs IS NULL
           AND sb.signaled_at IS NOT NULL
           AND sb.user_placed_at IS NULL
           AND sb.user_skipped_at IS NULL
           AND sb.edge_percent >= %s
           AND b.maturity_label = ANY(%s)
           AND m.date BETWEEN NOW() + (%s * INTERVAL '1 minute')
                          AND NOW() + (%s * INTERVAL '1 minute')
           AND NOT EXISTS (
               SELECT 1 FROM real_bets rb
                WHERE rb.match_id  = sb.match_id
                  AND rb.market    = sb.market
                  AND rb.selection = sb.selection
           )
         ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
        """,
        (_MIN_EDGE, allowed,
         PREKICKOFF_WINDOW_MIN_MINUTES, PREKICKOFF_WINDOW_MAX_MINUTES),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        floor = _min_edge_for(d.get("market"))
        if float(d.get("edge_percent") or 0) < floor:
            continue
        out.append(d)
    out.sort(key=lambda x: x.get("match_date") or datetime.max.replace(tzinfo=timezone.utc))
    return out


def _format_urgent_signal(b: dict, *, daemon_reason: str) -> str:
    """Urgent variant of the regular bet-signal — different emoji + lead
    line so the operator can distinguish "another signal" from "this one
    is about to kick off AND the daemon isn't placing it". Designed for
    lock-screen readability."""
    edge_pct = float(b["edge_percent"] or 0) * 100
    odds = float(b["odds_at_pick"] or 0)
    stake = float(b["stake"] or 0)
    mins = float(b.get("minutes_to_ko") or 0)
    if mins < 0:
        ko_str = f"⏱ KICKED OFF {-int(mins)}m ago"
    else:
        ko_str = f"⏱ KO in {int(mins)}m"

    market = (b.get("market") or "").upper()
    selection = (b.get("selection") or "").upper()
    league = b.get("league") or ""

    cb_id = b.get("coolbet_match_id")
    coolbet_link = (f"https://www.coolbet.com/et/sport/match/{cb_id}"
                    if cb_id else "https://www.coolbet.com/et/sport/jalgpall")

    lines = [
        f"🚨 PLACE MANUALLY — daemon down ({daemon_reason})",
        f"{b['home_team']} vs {b['away_team']}",
        f"{market} → {selection}  @ {odds:.2f}",
        f"💰 €{stake:.2f}  ·  edge +{edge_pct:.1f}%",
        f"{ko_str}  ·  {league}",
        f"🤖 {b.get('bot_name') or '?'} ({b.get('bot_maturity') or '?'})",
        f"🔗 {coolbet_link}",
        f"id: {b['simulated_bet_id']}",
    ]
    return "\n".join(lines)


def run_prekickoff_alert(*, dry_run: bool = False) -> dict:
    """Main entry point. Returns counters so the scheduler wrapper can log
    a one-liner per run.

    Writes prekickoff_last_run_at + prekickoff_last_run_result to
    coolbet_session_state on EVERY invocation (healthy daemon, no
    candidates, sends, errors) so the cron's liveness is DB-observable
    without tailing Railway logs. See COOLBET-PREKICKOFF-HEARTBEAT
    (mig 252)."""
    from workers.notify.telegram import send_telegram

    counters = {"healthy": False, "candidates": 0, "sent": 0, "skipped_dedup": 0}
    try:
        healthy, reason = _mac_daemon_is_healthy()
        counters["healthy"] = healthy
        if healthy:
            # Daemon is alive and last tick was clean — let it handle placement.
            return counters

        candidates = load_prekickoff_candidates()
        counters["candidates"] = len(candidates)
        if not candidates:
            return counters

        log.info("prekickoff catch-net firing — daemon: %s — %d candidate%s",
                 reason, len(candidates), "" if len(candidates) == 1 else "s")

        for b in candidates:
            msg = _format_urgent_signal(b, daemon_reason=reason)
            if dry_run:
                counters["sent"] += 1
                continue
            # 1h dedup so each pick gets at most one urgent push per hour.
            # If still unplaced and KO approaches further, the next firing
            # in another hour will re-send. Avoids 5-min spam.
            tg_id = send_telegram(
                msg,
                dedup_key=f"prekickoff-{b['simulated_bet_id']}",
                dedup_window_s=3600,
            )
            if tg_id is not None:
                counters["sent"] += 1
            else:
                counters["skipped_dedup"] += 1
        return counters
    finally:
        # DB heartbeat — runs on every path including the early
        # `return counters` exits above. Without this, "Railway healthy
        # but catch-net silent" and "Railway crashed" look identical
        # from outside. Best-effort: observability must not break the
        # alerting path itself.
        if not dry_run:
            try:
                from workers.automation.coolbet_state import mark_prekickoff_run
                mark_prekickoff_run(counters)
            except Exception as e:
                log.debug("prekickoff heartbeat write failed (non-fatal): %s", e)


def main() -> int:
    """CLI entrypoint. `--dry-run` skips Telegram + does not consume dedup
    slots; useful for manual probes and smoke runs."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    c = run_prekickoff_alert(dry_run=args.dry_run)
    log.info("prekickoff catch-net: healthy=%s candidates=%d sent=%d skipped_dedup=%d",
             c["healthy"], c["candidates"], c["sent"], c["skipped_dedup"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
