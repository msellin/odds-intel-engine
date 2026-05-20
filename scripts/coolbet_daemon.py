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

PLACEMENT MODE:
  Defaults to --place-mode=dry. With --place-mode=execute the placer now
  uses the new Coolbet markets+odds schema (COOLBET-PLACER-NEW-SCHEMA,
  shipped 2026-05-20) and can place real money. Before flipping execute
  for real, ship COOLBET-SAFETY-GUARDRAILS first (--max-bets-per-hour,
  --max-stake-per-bet, --require-confirm). See dev/active/coolbet-roadmap.md.

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
import threading
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
_SWEEP_THREAD: threading.Thread | None = None


def _handle_signal(signum, frame):
    global _STOP
    log.info("Signal %s received — finishing current task and stopping", signum)
    _STOP = True


def _sweep_running() -> bool:
    """True if odds sweep thread is still active (started but not done)."""
    return _SWEEP_THREAD is not None and _SWEEP_THREAD.is_alive()


# ── Tasks ────────────────────────────────────────────────────────────────────


def _task_keepalive(session: CoolbetSession) -> str:
    ok = session.keep_alive()
    ttl = session.jwt_seconds_remaining
    return f"keepalive {'✓' if ok else '✗'}  (JWT TTL ≈ {int(ttl)}s)"


def _task_odds_snapshot(mode: str, days: int, require_pinnacle: bool) -> str:
    """mode: 'wide' | 'bets-only' | 'league-mapped'"""
    try:
        if mode == "league-mapped":
            from workers.automation.coolbet_explorer import run_league_sweep
            run_league_sweep(dry_run=False, sleep_s=1.5, require_pinnacle=require_pinnacle)
            return f"odds snapshot ✓ (league-mapped, today)"
        from workers.automation.coolbet_explorer import run_bulk
        run_bulk(days=days, dry_run=False, sleep_s=0.25, limit=None,
                 bets_only=(mode == "bets-only"))
        return f"odds snapshot ✓ ({mode}, {days}d)"
    except Exception as e:
        log.warning("odds snapshot raised: %s", e)
        return f"odds snapshot ✗ ({e})"


def _task_place(mode: str, guard, min_edge: float | None) -> str:
    """mode ∈ {'dry', 'record', 'execute'}. guard = PlacementGuard or None.
    min_edge: decimal fraction (0.05 = 5%); overrides COOLBET_MIN_EDGE env."""
    from workers.automation.coolbet_placer import place_all_bets
    record  = mode in ("record", "execute")
    execute = mode == "execute"
    try:
        results = place_all_bets(
            record=record, execute=execute, guard=guard, min_edge=min_edge,
        )
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
    ap.add_argument("--odds-mode", choices=("wide", "bets-only", "league-mapped"), default="wide",
                    help="wide=per-match search across all upcoming DB matches "
                         "(legacy, has false negatives). "
                         "bets-only=fetch only matches with pending value bets "
                         "(smallest API load). "
                         "league-mapped=use coolbet_league_mapping.json — group "
                         "today's AF matches by league, fetch each mapped Coolbet "
                         "league once, match within-league. Best coverage + lowest "
                         "API load. RECOMMENDED.")
    ap.add_argument("--require-pinnacle", action="store_true",
                    help="In league-mapped mode, only fetch Coolbet leagues where "
                         "at least one of today's AF matches has Pinnacle odds. "
                         "Smaller set; broader filter when off (default).")
    ap.add_argument("--odds-days", type=int, default=1,
                    help="How far ahead to fetch odds. 1 = today only (UTC, "
                         "strict DATE filter). 2+ = rolling N-day window. "
                         "Default 1 — tomorrow's odds don't help today's "
                         "betting and just burn API budget.")
    ap.add_argument("--place-min", type=int, default=5,
                    help="Placement loop cadence in minutes (default 5)")
    ap.add_argument("--min-edge", type=float, default=0.05,
                    help="Minimum edge to auto-place (decimal — 0.05 = 5%%, "
                         "the default). Overrides COOLBET_MIN_EDGE env. "
                         "Same threshold the admin /place page uses for the "
                         "'Edge ≥5%%' filter chip.")
    ap.add_argument("--place-mode", choices=("dry", "record", "execute"),
                    default="dry",
                    help="Placer behaviour. dry=print only (default). "
                         "record=write real_bets, don't POST to Coolbet. "
                         "execute=write real_bets AND POST to Coolbet (real money). "
                         "Don't flip execute until COOLBET-SAFETY-GUARDRAILS ships.")
    ap.add_argument("--no-place", action="store_true",
                    help="Disable the placement loop entirely")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="Skip COOLBET-PREFLIGHT checks at startup. NOT "
                         "recommended — preflight catches expired cookies "
                         "before the daemon enters its run loop.")
    # COOLBET-SAFETY-GUARDRAILS — only enforced in execute mode (record/dry just
    # use stake-for-display). For first live runs combine: --use-kelly-stake
    # --max-stake-per-bet 5 --max-bets-per-hour 3 --require-confirm.
    g = ap.add_argument_group("safety guardrails (execute mode)")
    g.add_argument("--use-kelly-stake", action="store_true",
                   help="Use Kelly-derived stake from simulated_bets.stake "
                        "(default: fixed €10 from COOLBET_STAKE env)")
    g.add_argument("--stake", type=float,
                   help="Fixed stake when not using Kelly (overrides "
                        "COOLBET_STAKE env)")
    g.add_argument("--max-stake-per-bet", type=float,
                   help="Hard cap on per-bet stake regardless of source")
    g.add_argument("--max-bets-per-hour", type=int,
                   help="Rolling 60-min rate limit on placements")
    g.add_argument("--max-total-stake", type=float,
                   help="Cumulative session-stake cap (pause placement when hit)")
    g.add_argument("--max-edge-pct", type=float,
                   help="Refuse bets with edge above this %% (model-bug guard, "
                        "eg --max-edge-pct 25)")
    g.add_argument("--require-confirm", action="store_true",
                   help="Prompt y/n in the TTY before each real placement. "
                        "Essential for first live runs. Non-TTY = auto-decline.")
    g.add_argument("--bot-filter",
                   help="Comma-separated list of bot names to allow (eg "
                        "'bot_ou15_defensive,bot_btts_conservative')")
    args = ap.parse_args()

    from workers.automation.coolbet_placer import PlacementGuard
    bot_filter = [b.strip() for b in args.bot_filter.split(",")] if args.bot_filter else None
    guard = PlacementGuard(
        use_kelly_stake   = args.use_kelly_stake,
        fixed_stake       = args.stake,
        max_stake_per_bet = args.max_stake_per_bet,
        max_bets_per_hour = args.max_bets_per_hour,
        max_total_stake   = args.max_total_stake,
        max_edge_pct      = args.max_edge_pct,
        require_confirm   = args.require_confirm,
        bot_filter        = bot_filter,
    )

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if not args.skip_preflight:
        log.info("Running COOLBET-PREFLIGHT (use --skip-preflight to bypass)…")
        # Run as a subprocess so its exit code is decisive — and so its
        # output renders cleanly without interleaving with daemon logs.
        import subprocess
        rc = subprocess.call([sys.executable, str(Path(__file__).parent / "coolbet_preflight.py")])
        if rc != 0:
            log.error("Preflight failed (exit=%d). Daemon refusing to start.", rc)
            sys.exit(rc)

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

        # ODDS sweep — runs in a background thread so keepalive + placement
        # keep firing on schedule. If a previous sweep is still running, skip
        # this fire (cycle longer than --odds-min just means we sweep less
        # often, never queue up two sweeps).
        if now >= next_odds:
            global _SWEEP_THREAD
            if _sweep_running():
                log.info("odds snapshot ⏸  previous sweep still running — skipping")
            else:
                def _sweep_runner():
                    try:
                        log.info(_task_odds_snapshot(
                            mode=args.odds_mode,
                            days=args.odds_days,
                            require_pinnacle=args.require_pinnacle,
                        ))
                    except Exception as e:
                        log.warning("sweep thread crashed: %s\n%s", e, traceback.format_exc())
                _SWEEP_THREAD = threading.Thread(target=_sweep_runner, daemon=True,
                                                 name="coolbet-odds-sweep")
                _SWEEP_THREAD.start()
                log.info("odds snapshot ▶  started in background thread")
            next_odds = now + args.odds_min * 60

        # PLACEMENT — skipped while sweep is running so we don't have two
        # Coolbet sessions calling concurrently (each session has its own
        # throttle; concurrent = 2× rate = Imperva risk).
        if not args.no_place and now >= next_place:
            if _sweep_running():
                log.info("place ⏸  skipped (sweep in progress); retry in %d min", args.place_min)
            else:
                log.info(_task_place(args.place_mode, guard, args.min_edge))
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
