"""Coolbet automation daemon — foreground loop you can run all day.

Three things on three independent cadences:
  • KEEPALIVE   — heartbeat every 20 min so the server-side session never times out
  • ODDS SNAPSHOT — value-bet match odds → odds_snapshots every 30 min
  • PLACEMENT   — invoke the placer on qualifying bets every 5 min

About odds refresh cadence:
  Coolbet doesn't publish a documented odds-change rate. Empirically pre-match
  prices on major leagues move multiple times per hour as money flows; small
  leagues move on news only. 30-min snapshot polling is the standard cadence
  used elsewhere in this codebase (AF odds also poll every 30 min). The
  placer does a *live* price check at placement time anyway via
  get_live_odds_and_id — the 30-min snapshot is for time-series / signal use,
  not for placement freshness.

PLACEMENT MODE — IMPORTANT:
  Defaults to --place-mode=dry. The placer's market-resolution code still
  reads the old Coolbet schema (`criterion_label` from Kambi-style betOffers)
  and the new schema removed that field. So even with --place-mode=execute
  the placer currently logs "no_market" for everything and places nothing.
  Auto-placement becomes real after coolbet_placer.find_market_outcome is
  rewritten against the new markets/outcomes shape — tracked as
  COOLBET-PLACER-NEW-SCHEMA in PRIORITY_QUEUE.md.

Run:
  python3 scripts/coolbet_daemon.py                       # safe defaults
  python3 scripts/coolbet_daemon.py --place-mode=record   # write real_bets
  python3 scripts/coolbet_daemon.py --place-mode=execute  # also POST bet to Coolbet (once placer is fixed)
  python3 scripts/coolbet_daemon.py --no-place            # disable placement loop entirely

Ctrl-C stops cleanly.
"""

import argparse
import logging
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("coolbet_daemon")

_STOP = False


def _handle_signal(signum, frame):
    global _STOP
    log.info("Signal %s received — finishing current task and stopping", signum)
    _STOP = True


# ── Tasks ────────────────────────────────────────────────────────────────────


def _task_keepalive(session: CoolbetSession) -> str:
    ok = session.keep_alive()
    ttl = session.jwt_seconds_remaining
    return f"keepalive {'✓' if ok else '✗'}  (JWT TTL ≈ {int(ttl)}s)"


def _task_odds_snapshot() -> str:
    # Import inside so a transient bug in the explorer doesn't block daemon startup.
    from workers.automation.coolbet_explorer import run_bulk
    try:
        run_bulk(days=2, dry_run=False, sleep_s=0.25, limit=None, bets_only=True)
        return "odds snapshot ✓"
    except Exception as e:
        log.warning("odds snapshot raised: %s", e)
        return f"odds snapshot ✗ ({e})"


def _task_place(mode: str) -> str:
    """mode ∈ {'dry', 'record', 'execute'}."""
    from workers.automation.coolbet_placer import place_all_bets
    record  = mode in ("record", "execute")
    execute = mode == "execute"
    try:
        results = place_all_bets(record=record, execute=execute)
        if not results:
            return f"place ({mode}) ✓ — no qualifying bets"
        outcomes: dict[str, int] = {}
        for r in results:
            outcomes[r.get("outcome", "?")] = outcomes.get(r.get("outcome", "?"), 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
        return f"place ({mode}) ✓ — {len(results)} evaluated [{summary}]"
    except Exception as e:
        log.warning("place raised: %s\n%s", e, traceback.format_exc())
        return f"place ({mode}) ✗ ({e})"


# ── Driver ───────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keepalive-min", type=int, default=20,
                    help="Heartbeat cadence in minutes (default 20)")
    ap.add_argument("--odds-min", type=int, default=30,
                    help="Odds snapshot cadence in minutes (default 30)")
    ap.add_argument("--place-min", type=int, default=5,
                    help="Placement loop cadence in minutes (default 5)")
    ap.add_argument("--place-mode", choices=("dry", "record", "execute"),
                    default="dry",
                    help="Placer behaviour (default dry; execute requires the "
                         "new-schema placer fix — see COOLBET-PLACER-NEW-SCHEMA)")
    ap.add_argument("--no-place", action="store_true",
                    help="Disable the placement loop entirely")
    args = ap.parse_args()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("─" * 78)
    log.info("Coolbet daemon starting")
    log.info("  keepalive every %d min", args.keepalive_min)
    log.info("  odds      every %d min", args.odds_min)
    if args.no_place:
        log.info("  place     DISABLED (--no-place)")
    else:
        log.info("  place     every %d min  (mode=%s)", args.place_min, args.place_mode)
    log.info("─" * 78)

    session = CoolbetSession()
    # Force initial login so the first keepalive doesn't surprise on auth.
    log.info(_task_keepalive(session))

    now = time.time()
    next_keepalive = now + args.keepalive_min * 60
    next_odds      = now            # run odds immediately on start
    next_place     = now            # run place immediately on start

    while not _STOP:
        now = time.time()

        if now >= next_keepalive:
            log.info(_task_keepalive(session))
            next_keepalive = now + args.keepalive_min * 60

        if now >= next_odds:
            log.info(_task_odds_snapshot())
            next_odds = now + args.odds_min * 60

        if not args.no_place and now >= next_place:
            log.info(_task_place(args.place_mode))
            next_place = now + args.place_min * 60

        # Sleep until the soonest next task, but check stop signal every 30s.
        next_due = min(
            next_keepalive,
            next_odds,
            float("inf") if args.no_place else next_place,
        )
        sleep_for = max(min(next_due - time.time(), 30.0), 1.0)
        time.sleep(sleep_for)

    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
