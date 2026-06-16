"""
Coolbet Mac-side placement daemon (COOLBET-MAC-DAEMON, 2026-06-12).

Runs on the operator's Mac at home. Polls the DB for qualified picks
and places them against Coolbet via CoolbetSession — same code path the
Railway-side placer used, but FROM A RESIDENTIAL IP so Imperva's
/s/auth/login cloud-IP block doesn't apply.

WHY THIS EXISTS:
The auto-placer chain (Imperva 403 from Railway IPs → FS Chrome tab →
30-min JWT → SMS-2FA on re-login) was structurally fragile when run
from Railway. The 100+ SMS spam on 2026-06-11 night was the breaking
point. Moving the placement leg to a Mac at home fixes the root cause
— residential IP, persistent local Chrome profile (volume-mounted),
no remote container OOM crashes.

CO-EXISTENCE WITH SIGNALER:
The signaler still fires on every pipeline tick — those Telegram
messages remain the operator's safety net even when this daemon is
running. The daemon writes to real_bets on successful placement; the
signaler's NOT EXISTS query naturally skips placed picks on its next
run. If the daemon is offline (Mac asleep, Docker stopped), only the
signal fires — operator places manually from phone.

POLLING vs CRON:
APScheduler in-process loop. macOS launchd starts ONE python process
that lives forever; the loop runs every POLL_INTERVAL_S seconds and
catches up on whatever's qualified. Simpler than cron (no separate
process per tick, no race conditions between overlapping runs) and
keeps the JWT/cookies warm in memory between ticks.

WHAT IT DOES NOT DO:
- No SMS-trust enrollment. That stays a one-time manual flow via
  scripts/coolbet/flaresolverr_login_enroll.py.
- No JWT refresh outside CoolbetSession's existing renew-token logic.
- No bookmaker switching. Coolbet only.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Polling cadence — every 5 min by default. Tighter than the betting
# pipeline's 1.5-hour cohort tick so we don't sit on edges that just
# qualified. Loose enough that we don't hammer Coolbet's anon search
# endpoint.
# Default 30 min — matches the betting pipeline's cohort cadence (06:00,
# 09:30, 11:00, 13:30, 15:00, 17:30, 19:00, 20:30 UTC + the 1h windows
# in between). Shorter than that wastes resources and pops up Chrome
# windows the operator doesn't want. Longer risks missing new edges by
# more than one cohort tick.
POLL_INTERVAL_S = int(os.getenv("COOLBET_MAC_POLL_S", "1800"))

# Sanity: if the daemon spent more than this without a successful
# placement attempt, log a loud warning so the operator notices if
# something's wedged silently (Docker stopped, network out, etc).
HEALTH_WARN_AFTER_S = int(os.getenv("COOLBET_MAC_HEALTH_WARN_S", "1800"))

# COOLBET-DAEMON-ALERTS (2026-06-16): push a Telegram alert when the
# daemon errors on N consecutive ticks. Two is the right floor — a
# single transient blip (network hiccup, Imperva 503) shouldn't page
# the operator, but two in a row almost always means a structural
# break (JWT expired + CDP logged out, Docker down, etc) that needs
# human intervention. Dedup by hour-of-day so a sustained outage
# alerts at most once per hour during waking hours, not every 30 min.
ALERT_AFTER_CONSECUTIVE_ERRORS = int(os.getenv("COOLBET_MAC_ALERT_AFTER_ERRORS", "2"))

_stop = False


def _notify_placement(result: dict, *, dry_run: bool) -> None:
    """Send a Telegram message after the daemon successfully places a
    bet. Lives in the daemon (not the placer) so the placer can stay
    Telegram-free for manual-CLI callers — see TELE-BET-NOTIFY pin.

    Format mirrors the existing signaler messages so the operator can
    visually correlate "I got a signal" → "the bot placed it"."""
    from workers.notify.telegram import send_telegram

    home = result.get("home_team") or "?"
    away = result.get("away_team") or "?"
    market = result.get("market") or "?"
    selection = result.get("selection") or "?"
    live_odds = result.get("live_odds")
    stake = result.get("stake")
    edge_pct = result.get("edge_percent")
    bot_name = result.get("bot_name") or "?"
    ticket_id = result.get("ticket_id") or ""
    real_bet_id = result.get("real_bet_id") or ""

    odds_str = f"{float(live_odds):.3f}" if live_odds is not None else "?"
    stake_str = f"€{float(stake):.2f}" if stake is not None else "?"
    edge_str = f"+{float(edge_pct):.2f}%" if edge_pct is not None else "?"
    ticket_short = (str(ticket_id)[:16] + "…") if ticket_id else "(none)"

    body = (
        f"🤖 <b>Auto-placed bet</b>{' (DRY)' if dry_run else ''}\n"
        f"\n"
        f"{home} vs {away}\n"
        f"{market} / {selection}\n"
        f"@ {odds_str} · stake {stake_str} · edge {edge_str}\n"
        f"bot: {bot_name}\n"
        f"ticket: <code>{ticket_short}</code>"
    )
    # Dedup key per real_bet so a retry / log replay can't re-send.
    # Dedup key per real_bet so a retry / log replay can't re-send. The
    # send_telegram helper already hardcodes parse_mode="HTML".
    dedup = f"auto-placed-{real_bet_id}" if real_bet_id else None
    send_telegram(body, dedup_key=dedup)


def _notify_consecutive_failures(*, consecutive: int, first_error_at: float) -> None:
    """Push a Telegram when the daemon errors on N consecutive ticks. Called
    from run_forever() once the counter crosses ALERT_AFTER_CONSECUTIVE_ERRORS.
    Classifies the underlying CDP-JWT state so the operator gets an actionable
    recovery hint, not just "daemon is dead".

    Dedup key is hour-of-day (`daemon-fail-burst-YYYYMMDDHH`) so a sustained
    outage produces at most one push per hour — enough to keep the operator
    informed without becoming notification spam. The send_telegram helper's
    in-process _LAST_SENT dict survives across ticks (same process)."""
    from workers.notify.telegram import send_telegram
    from workers.automation.coolbet_browser_sync import diagnose_cdp_jwt_state

    try:
        diag = diagnose_cdp_jwt_state()
    except Exception as e:  # diagnosis must never break the alert
        diag = {"state": "unknown", "detail": f"diagnosis failed: {e}", "ttl_s": None}

    state = diag.get("state") or "unknown"
    detail = diag.get("detail") or ""

    # Compact recovery hint per state. Keep one line — the operator reads
    # this on a phone lock screen.
    recovery = {
        "chrome_down":    "Run ./local/launch_chrome_for_sync.sh on the Mac.",
        "no_coolbet_tab": "Open a coolbet.com tab in CDP-Chrome.",
        "logged_out":     "Log into coolbet.com in CDP-Chrome.",
        "jwt_expired":    "Refresh the coolbet.com tab (or log in again).",
        "valid":          "JWT looks valid — check FlareSolverr / network / Coolbet status.",
        "unknown":        "Tail dev/active/coolbet-mac-daemon.log for the underlying error.",
    }.get(state, "Tail dev/active/coolbet-mac-daemon.log.")

    # send_telegram hardcodes parse_mode=HTML, so any "<" / ">" / "&" inside
    # the dynamic strings (especially detail, which can legitimately contain
    # "<=", "<60s", etc.) must be HTML-escaped or the API returns 400
    # "can't parse entities". The recovery + static labels are safe by
    # construction but escaping them too costs nothing.
    import html as _html
    state_safe = _html.escape(state, quote=False)
    detail_safe = _html.escape(detail, quote=False)
    recovery_safe = _html.escape(recovery, quote=False)

    mins_failing = int((time.time() - first_error_at) / 60.0) if first_error_at else 0
    body = (
        f"🚨 <b>Coolbet daemon failing</b>\n"
        f"\n"
        f"{consecutive} consecutive ticks errored ({mins_failing}m).\n"
        f"State: <b>{state_safe}</b>\n"
        f"{detail_safe}\n"
        f"\n"
        f"➡ {recovery_safe}"
    )

    # Dedup on hour-of-day so each hour of sustained outage produces at
    # most one alert. dedup_window_s=4000 covers a full hour with margin.
    hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    send_telegram(body, dedup_key=f"daemon-fail-burst-{hour_key}",
                  dedup_window_s=4000)


def _handle_sigterm(signum, frame):
    """Graceful shutdown — finish current tick before exiting. launchd's
    KeepAlive will restart us after exit, so this just keeps Coolbet
    HTTP calls from being interrupted mid-flight."""
    global _stop
    log.info("SIGTERM/SIGINT received — finishing current tick then exiting.")
    _stop = True


def _sync_placed_bets_from_coolbet() -> int:
    """Fetch the operator's actual Coolbet pending bets via CDP and mark
    matching simulated_bets as user_placed_at — so the placement loop
    doesn't re-attempt anything already on Coolbet. This is the
    structural dedup against actual Coolbet state, separate from the
    Telegram-button dedup. Returns count of newly-marked rows.

    Conservative on errors: if CDP is unavailable, the daemon proceeds
    with placement using the existing real_bets + user_placed_at dedup
    paths. Only the auto-sync layer goes silent."""
    try:
        from workers.automation.coolbet_browser_sync import (
            fetch_pending_bets_via_cdp, normalize_for_dedup,
            match_coolbet_to_simulated,
        )
        from workers.api_clients.db import execute_query, execute_write
    except Exception as e:
        log.warning("CDP sync deps missing: %s", e)
        return 0

    coolbet_tickets = fetch_pending_bets_via_cdp()
    if not coolbet_tickets:
        return 0

    # Load the simulated_bets that aren't yet placed/skipped/in-real_bets
    # — these are the candidates that COULD be marked as placed by sync.
    candidates = execute_query(
        """SELECT sb.id AS simulated_bet_id,
                  sb.match_id, sb.market, sb.selection,
                  ht.name AS home_team, at2.name AS away_team,
                  m.date AS match_date
           FROM simulated_bets sb
           JOIN matches m   ON m.id  = sb.match_id
           JOIN teams   ht  ON ht.id = m.home_team_id
           JOIN teams   at2 ON at2.id = m.away_team_id
           WHERE sb.combo_legs IS NULL
             AND sb.user_placed_at IS NULL
             AND sb.user_skipped_at IS NULL
             AND m.date > NOW() - INTERVAL '12 hours'
             AND m.date < NOW() + INTERVAL '48 hours'
             AND NOT EXISTS (
                 SELECT 1 FROM real_bets rb
                 WHERE rb.match_id=sb.match_id
                   AND rb.market=sb.market AND rb.selection=sb.selection
             )"""
    )
    candidates = [dict(r) for r in candidates]
    if not candidates:
        return 0

    marked = 0
    for ticket in coolbet_tickets:
        norm = normalize_for_dedup(ticket)
        if not norm:
            continue
        if norm.get("is_combo"):
            # Combo dedup is harder — every leg would have to fuzzy-match.
            # Skip for now; rely on real_bets + user_placed_at button-tap.
            continue
        matched = match_coolbet_to_simulated(norm, candidates)
        if not matched:
            log.debug("no DB match for Coolbet ticket %s (%s)",
                       norm.get("ticket_id"), norm.get("match_name"))
            continue
        # Fan out to ALL sibling bot rows for the same combo so future
        # ticks don't re-fire from any of them. Mirrors the signaler's
        # _mark_signaled fan-out pattern.
        try:
            execute_write(
                """UPDATE simulated_bets
                   SET user_placed_at = NOW()
                   WHERE match_id = %s AND market = %s AND selection = %s
                     AND user_placed_at IS NULL""",
                (matched["match_id"], matched["market"], matched["selection"]),
            )
            log.info("synced from Coolbet: %s | %s/%s | ticket=%s",
                      matched["home_team"] + " vs " + matched["away_team"],
                      matched["market"], matched["selection"],
                      norm.get("ticket_id"))
            marked += 1
        except Exception as e:
            log.warning("user_placed_at write failed for %s: %s",
                         matched.get("simulated_bet_id"), e)
    return marked


def _tick(*, dry_run: bool = False) -> dict:
    """One placement pass. Returns counters so the loop can decide whether
    to log loudly or silently this round. Catches all exceptions — a
    single broken tick must NOT bring down the daemon (launchd would
    restart but we'd lose the in-process JWT cache)."""
    started_at = datetime.now(timezone.utc)
    counters = {
        "started_at": started_at.isoformat(),
        "qualified": 0,
        "placed": 0,
        "errors": 0,
        "skipped": 0,
        "elapsed_s": 0.0,
    }
    try:
        # SILENT-WHEN-EMPTY (2026-06-12): cheap DB check first — if no
        # qualifying picks exist, exit the tick before touching Coolbet
        # or CDP-Chrome. The popping-up cost (and resource use) of a
        # CDP-Chrome navigation is unjustified when there's nothing
        # to dedup or place.
        from workers.automation.coolbet_placer import (
            load_qualified_bets, place_all_bets,
        )
        candidates = load_qualified_bets()
        counters["qualified"] = len(candidates)
        if not candidates:
            return counters

        # COOLBET-CDP-SYNC (2026-06-12): only run the CDP sync when we
        # actually have qualifying candidates that might be placed —
        # otherwise the CDP page-load + XHR-capture is wasted work and
        # an unwanted Chrome-window flash. Sync narrows the candidate
        # set by marking ones the operator already placed manually.
        try:
            synced = _sync_placed_bets_from_coolbet()
            counters["synced_from_coolbet"] = synced
            if synced:
                log.info("CDP-sync marked %d sim_bets as already-placed", synced)
                # Re-load candidates after sync since some may now be
                # excluded (user_placed_at IS NULL filter in placer).
                candidates = load_qualified_bets()
                counters["qualified_after_sync"] = len(candidates)
                if not candidates:
                    return counters
        except Exception as e:
            log.warning("CDP sync failed (proceeding without): %s", e)
            counters["synced_from_coolbet"] = 0
        if not candidates:
            return counters
        # record=True writes a real_bets row (the audit trail).
        # execute=True actually POSTs to Coolbet — that's the whole point.
        # dry_run override is for smoke: NO side effects — skip the
        # real_bets write too, otherwise the paper row's NOT EXISTS guard
        # would block the real placement attempt on the next live tick.
        if dry_run:
            results = place_all_bets(record=False, execute=False)
        else:
            results = place_all_bets(record=True, execute=True)
        for r in results:
            outcome = r.get("outcome")
            if outcome == "placed":
                counters["placed"] += 1
                # AUTO-PLACE-TG-NOTIFY (2026-06-12): the operator asked
                # for a Telegram message every time the daemon places a
                # bet without their button-tap — so they're never
                # surprised by a real_bets row that "just appeared".
                # Best-effort; failure here MUST NOT mark the tick as
                # errored (the bet succeeded; the notification is
                # observability).
                try:
                    _notify_placement(r, dry_run=dry_run)
                except Exception as e:
                    log.debug("placement Telegram notify failed: %s", e)
            elif outcome in ("dry_run", "no_event", "no_market",
                             "edge_eroded", "guard_skip"):
                counters["skipped"] += 1
            else:
                counters["errors"] += 1
    except Exception as e:
        log.exception("mac daemon tick failed: %s", e)
        counters["errors"] += 1
    finally:
        # HEARTBEAT (2026-06-12): write the tick result to DB so the
        # Telegram /status command on Vercel can answer "is the daemon
        # actually running?". Best-effort — observability shouldn't break
        # placement.
        #
        # HEARTBEAT-ON-EMPTY (2026-06-16): wrapped in finally so the three
        # `if not candidates: return counters` early-exits inside the try
        # also reach the heartbeat write. Without this, a healthy daemon
        # finding zero qualified picks for >60min would look "stale" to
        # the COOLBET-DAEMON-ALERTS pre-kickoff catch-net and trigger a
        # false-positive "PLACE MANUALLY — daemon down" Telegram.
        counters["elapsed_s"] = (datetime.now(timezone.utc) - started_at).total_seconds()
        try:
            from workers.automation.coolbet_state import mark_mac_daemon_tick
            mark_mac_daemon_tick(counters)
        except Exception as e:
            log.debug("mac_daemon heartbeat write failed (non-fatal): %s", e)
    return counters


def run_forever() -> None:
    """Main loop. Blocks until SIGTERM/SIGINT. launchd's KeepAlive
    semantics handle process restarts on crash; we just need to not
    leak resources between ticks."""
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log.info("Coolbet Mac daemon starting — poll every %ds", POLL_INTERVAL_S)
    log.info("FLARESOLVERR_URL=%s", os.getenv("FLARESOLVERR_URL"))

    last_active_at = time.time()
    tick_count = 0
    # COOLBET-DAEMON-ALERTS (2026-06-16): silent-failure guard. A streak
    # of failed ticks pushes a Telegram on the Nth one with classification +
    # recovery hint. Cleared on the first OK tick so a recovered daemon
    # doesn't keep alerting.
    consecutive_errors = 0
    first_error_at: float = 0.0
    alert_fired_this_burst = False
    while not _stop:
        tick_count += 1
        c = _tick()
        if c["qualified"] or c["errors"]:
            log.info(
                "tick %d — qualified=%d placed=%d skipped=%d errors=%d elapsed=%.1fs",
                tick_count, c["qualified"], c["placed"], c["skipped"],
                c["errors"], c["elapsed_s"],
            )
        if c["placed"] or c["errors"]:
            last_active_at = time.time()

        if c["errors"]:
            if consecutive_errors == 0:
                first_error_at = time.time()
            consecutive_errors += 1
            if (consecutive_errors >= ALERT_AFTER_CONSECUTIVE_ERRORS
                    and not alert_fired_this_burst):
                try:
                    _notify_consecutive_failures(
                        consecutive=consecutive_errors,
                        first_error_at=first_error_at,
                    )
                    alert_fired_this_burst = True
                except Exception as e:
                    log.warning("consecutive-failure Telegram alert failed: %s", e)
        else:
            # First clean tick resets the streak. A subsequent failure
            # burst will alert again (correct — that's a new incident).
            if consecutive_errors > 0:
                log.info("daemon recovered after %d consecutive errors", consecutive_errors)
            consecutive_errors = 0
            first_error_at = 0.0
            alert_fired_this_burst = False

        if time.time() - last_active_at > HEALTH_WARN_AFTER_S:
            log.warning(
                "no placement activity for %.0fm — verify Coolbet pipeline "
                "is producing simulated_bets and qualified_load is finding them",
                (time.time() - last_active_at) / 60,
            )
            last_active_at = time.time()  # avoid spamming the warning every tick

        # Sleep in short slices so SIGTERM is responsive
        slept = 0.0
        while slept < POLL_INTERVAL_S and not _stop:
            time.sleep(1.0)
            slept += 1.0

    log.info("Coolbet Mac daemon exiting cleanly.")


def main() -> int:
    """CLI entrypoint. Mainly here so launchd can invoke `python -m
    workers.automation.coolbet_mac_daemon` instead of needing a
    separate wrapper script."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--once", action="store_true",
                    help="Run a single tick then exit (smoke/debug).")
    p.add_argument("--dry-run", action="store_true",
                    help="Skip the Coolbet POST — DB writes still happen "
                         "(record=True). Used for smoke + manual probes.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.once:
        c = _tick(dry_run=args.dry_run)
        print(f"tick result: {c}")
        return 0

    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
