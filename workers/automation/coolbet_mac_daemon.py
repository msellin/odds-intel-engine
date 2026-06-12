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
POLL_INTERVAL_S = int(os.getenv("COOLBET_MAC_POLL_S", "300"))

# Sanity: if the daemon spent more than this without a successful
# placement attempt, log a loud warning so the operator notices if
# something's wedged silently (Docker stopped, network out, etc).
HEALTH_WARN_AFTER_S = int(os.getenv("COOLBET_MAC_HEALTH_WARN_S", "1800"))

_stop = False


def _handle_sigterm(signum, frame):
    """Graceful shutdown — finish current tick before exiting. launchd's
    KeepAlive will restart us after exit, so this just keeps Coolbet
    HTTP calls from being interrupted mid-flight."""
    global _stop
    log.info("SIGTERM/SIGINT received — finishing current tick then exiting.")
    _stop = True


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
        # Late import — keeps the daemon process slim until first tick,
        # and avoids paying CoolbetSession's env-var validation cost
        # before we know there's work to do.
        from workers.automation.coolbet_placer import (
            load_qualified_bets, place_all_bets,
        )
        candidates = load_qualified_bets()
        counters["qualified"] = len(candidates)
        if not candidates:
            return counters
        # record=True writes a real_bets row.
        # execute=True actually POSTs to Coolbet — that's the whole point.
        # dry_run override is for the smoke test.
        results = place_all_bets(record=True, execute=not dry_run)
        for r in results:
            outcome = r.get("outcome")
            if outcome == "placed":
                counters["placed"] += 1
            elif outcome in ("dry_run", "no_event", "no_market",
                             "edge_eroded", "guard_skip"):
                counters["skipped"] += 1
            else:
                counters["errors"] += 1
    except Exception as e:
        log.exception("mac daemon tick failed: %s", e)
        counters["errors"] += 1
    counters["elapsed_s"] = (datetime.now(timezone.utc) - started_at).total_seconds()
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
