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
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession

# ── Logging: stdout + rotating daily file (COOLBET-PERSISTENT-LOG) ───────────
# Daily rotation, keep 14 days. File path: ~/.coolbet-daemon/coolbet.log.
# Survives tmux kills, daemon restarts, mac reboots.
_LOG_DIR = Path.home() / ".coolbet-daemon"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_PATH = _LOG_DIR / "coolbet.log"

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s",
                         datefmt="%Y-%m-%d %H:%M:%S")
_stdout = logging.StreamHandler(sys.stdout)
_stdout.setFormatter(_fmt)
_file = logging.handlers.TimedRotatingFileHandler(
    _LOG_PATH, when="midnight", backupCount=14, encoding="utf-8",
)
_file.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_stdout, _file])
log = logging.getLogger("coolbet_daemon")

# ── State persistence (COOLBET-STATE-PERSISTENCE) ────────────────────────────
# JSON state lives at ~/.coolbet-daemon/state.json. Restarts pick up
# last-event timestamps + counters from disk so the status CLI keeps a
# coherent history across daemon kills.
_STATE_PATH = _LOG_DIR / "state.json"


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        # Write-then-rename for atomic replacement
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, _STATE_PATH)
    except Exception as e:
        log.warning("state.json write failed: %s", e)


def _stamp(state: dict, key: str, extra: dict | None = None) -> None:
    """Update state[key] = {ts: now, ...extra} and persist."""
    state[key] = {"ts": datetime.now(timezone.utc).isoformat(), **(extra or {})}
    _save_state(state)

_STOP = False
_SWEEP_THREAD: threading.Thread | None = None

# ── Runtime control (TELEGRAM-COMMANDS) ──────────────────────────────────────
# Mutable flags Telegram commands can flip mid-run. Daemon main loop reads
# these on each tick. Guarded by simple GIL semantics — single-process daemon
# means we don't need a lock for these scalar reads.
_CTRL = {
    "paused":       False,             # /pause sets True, /resume sets False
    "place_mode":   None,              # /place_mode overrides args.place_mode
    "force_login":  False,             # /relogin sets True; daemon resets after consuming
    "force_summary": False,            # /summary sets True; daemon fires + clears
    "inplay_mode":  None,              # /inplay_mode overrides args.inplay_mode (capture/paper/execute)
}


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
    if not ok:
        # Imperva 403 / cookie expiry / network blip — alert the operator
        # so they don't discover overnight that the daemon was zombied.
        # Dedup so a multi-hour outage sends 1 alert, not 60.
        from workers.notify.telegram import send_telegram
        send_telegram(
            "⚠ Coolbet keepalive failed — likely Imperva 403 / cookies expired. "
            "Stop daemon (<code>tmux kill-session -t coolbet</code>), refresh "
            "<code>COOLBET_COOKIE_*</code> in .env from browser, run "
            "<code>python3 scripts/coolbet_preflight.py</code>, restart.",
            dedup_key="coolbet-keepalive-fail",
            dedup_window_s=3600,
        )
    return f"keepalive {'✓' if ok else '✗'}  (JWT TTL ≈ {int(ttl)}s)"


def _task_odds_snapshot(mode: str, days: int, require_pinnacle: bool) -> str:
    """mode: 'wide' | 'bets-only' | 'league-mapped'"""
    try:
        if mode == "league-mapped":
            from workers.automation.coolbet_explorer import run_league_sweep
            run_league_sweep(dry_run=False, sleep_s=1.5, require_pinnacle=require_pinnacle)
            return f"odds snapshot ✓ (league-mapped, today)"
        from workers.automation.coolbet_explorer import run_bulk
        run_bulk(days=days, dry_run=False, sleep_s=3.0, limit=None,
                 bets_only=(mode == "bets-only"))
        return f"odds snapshot ✓ ({mode}, {days}d)"
    except Exception as e:
        log.warning("odds snapshot raised: %s", e)
        from workers.notify.telegram import send_telegram
        send_telegram(
            f"⚠ Coolbet odds snapshot failed: {str(e)[:300]}",
            dedup_key="coolbet-odds-snapshot-fail",
            dedup_window_s=3600,
        )
        raise


def _task_jwt_browser_refresh(session: CoolbetSession) -> str:
    """COOLBET-JWT-API-RENEW — call Coolbet's /s/auth/renew-token endpoint
    directly from Python. No browser, no Imperva challenge, no Smart-ID.

    The function is still named `_browser_refresh` for back-compat with the
    smoke test + Telegram /relogin wiring, but the implementation is now a
    pure-API call. Coolbet's frontend uses this same endpoint every ~20 min
    while a user is browsing, so it's a normal traffic pattern.

    On 401/403 (current JWT is dead) we Telegram-alert the operator to
    Smart-ID again and paste a fresh JWT; that's the only manual touchpoint
    left in the operation loop.
    """
    from workers.notify.telegram import send_telegram
    try:
        ttl = session.renew_jwt_via_api()
        return f"jwt_renew ✓ (TTL ≈ {int(ttl)}s)"
    except Exception as e:
        msg = str(e)
        # Dead-JWT case: 401/403 means our current JWT is past its grace
        # window — only way back is a fresh Smart-ID login.
        if "401" in msg or "403" in msg or "refused" in msg.lower():
            log.warning("jwt_renew: current JWT dead — operator must re-Smart-ID")
            send_telegram(
                "🔐 <b>Coolbet JWT dead</b>\n"
                "Renewal refused — current JWT has expired past the grace window.\n\n"
                "Recover (~30 sec):\n"
                "1. Log into coolbet.com via Smart-ID (PIN1 on phone)\n"
                "2. DevTools → Network → any request → copy <code>cbauth</code> Bearer\n"
                "3. Update <code>COOLBET_MANUAL_JWT</code> in .env\n"
                "4. Daemon picks it up automatically on next renewal cycle",
                dedup_key="coolbet-jwt-dead",
                dedup_window_s=3600,
            )
            return "jwt_renew ✗ (current JWT dead — operator alerted)"
        log.warning("jwt_renew: %s", msg[:200])
        return f"jwt_renew ✗ ({msg[:120]})"


def _send_inplay_ping(snap: dict, mode: str) -> None:
    """Telegram notification for a successful inplay snapshot capture.
    Only called for capture_outcome=captured (filtered upstream)."""
    try:
        from workers.notify.telegram import send_telegram
        home   = snap.get("_home_team") or "?"
        away   = snap.get("_away_team") or "?"
        market = snap.get("_market") or "?"
        sel    = snap.get("_selection") or "?"
        bot    = snap.get("_bot_name") or "?"
        stake  = snap.get("_stake") or 0
        m_odds = snap.get("model_odds")
        c_odds = snap.get("coolbet_odds")
        latency = snap.get("latency_ms") or 0

        slippage_part = ""
        if m_odds and c_odds:
            slip_pct = (m_odds - c_odds) / m_odds * 100
            slippage_part = f"  model {m_odds:.3f} → coolbet {c_odds:.3f} (Δ{slip_pct:+.1f}%)\n"
        else:
            slippage_part = f"  coolbet {c_odds:.3f}\n" if c_odds else ""

        label_map = {
            "capture": "📡 <b>INPLAY</b> snapshot (data only)",
            "paper":   "📡 <b>INPLAY</b> paper trade",
            "execute": "💸 <b>INPLAY · REAL MONEY</b>",
        }
        label = label_map.get(mode, "📡 INPLAY")
        msg = (
            f"{label}\n"
            f"  <b>{home} vs {away}</b>\n"
            f"  {market} {sel}\n"
            f"{slippage_part}"
            f"  €{stake:.2f}  ·  bot {bot}  ·  latency {latency}ms"
        )
        send_telegram(msg)
    except Exception as e:
        log.warning("inplay telegram ping failed: %s", e)


def _inplay_listener_loop(session, default_mode: str) -> None:
    """Long-running listener thread for inplay snapshot capture.

    Connects to Postgres via a dedicated psycopg2 connection (NOT pooled —
    LISTEN holds the conn for the thread's lifetime), runs `LISTEN
    inplay_bet_fired`, and dispatches each notification to
    capture_inplay_snapshot() + insert_snapshot().

    The effective mode is read PER NOTIFICATION from _CTRL["inplay_mode"]
    or, if unset, from the daemon's CLI default. This lets Telegram
    /inplay_mode flip the mode mid-run without restarting the listener.

    Connection survives DB reconnect storms by sleeping briefly and
    re-establishing on InterfaceError/OperationalError.
    """
    import json as _json
    import select as _select
    import psycopg2 as _psycopg2
    import psycopg2.extensions as _pgext
    from workers.automation.coolbet_inplay import (
        capture_inplay_snapshot, insert_snapshot,
    )

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        log.warning("inplay listener: DATABASE_URL not set — skipping")
        return

    while not _STOP:
        conn = None
        try:
            conn = _psycopg2.connect(db_url)
            conn.set_isolation_level(_pgext.ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute("LISTEN inplay_bet_fired;")
            log.info("inplay listener: LISTEN inplay_bet_fired (default_mode=%s)", default_mode)
            while not _STOP:
                # 5-sec select() so we re-check _STOP for clean shutdown
                if _select.select([conn], [], [], 5) == ([], [], []):
                    continue
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        payload = _json.loads(notify.payload)
                        mode = _CTRL.get("inplay_mode") or default_mode
                        snap = capture_inplay_snapshot(session, payload, mode=mode)
                        snap_id = insert_snapshot(snap)
                        log.info(
                            "inplay snapshot %s outcome=%s latency=%dms mode=%s bet=%s",
                            snap_id, snap["capture_outcome"], snap["latency_ms"],
                            mode, snap["simulated_bet_id"],
                        )
                        # Notify only on successful captures (skip no_match /
                        # no_market / api_error / odds_drop_too_large — those
                        # are noise) AND skip if conflict-skipped (snap_id None).
                        if snap_id and snap.get("capture_outcome") == "captured":
                            _send_inplay_ping(snap, mode)
                    except Exception as e:
                        log.error("inplay listener: payload %s raised %s",
                                  (notify.payload or "")[:200], e)
        except (_psycopg2.InterfaceError, _psycopg2.OperationalError) as e:
            log.warning("inplay listener: DB connection lost (%s) — reconnecting in 10s", e)
            time.sleep(10)
        except Exception as e:
            log.error("inplay listener: unexpected error %s — restarting loop in 30s", e)
            time.sleep(30)
        finally:
            if conn is not None:
                try: conn.close()
                except Exception: pass


def _task_daily_summary() -> bool:
    """COOLBET-DAILY-SUMMARY — once per UTC day, Telegram digest of:
      • Coolbet odds rows + distinct matches ingested today
      • auto-placed real_bets today + total stake
      • pending value bets at ≥5% edge not yet placed
      • last keepalive timestamp (so operator knows session is alive)

    Returns True on send, False if skipped / failed."""
    from workers.api_clients.supabase_client import execute_query
    from workers.notify.telegram import send_telegram
    try:
        # Today's Coolbet ingest volume
        r1 = (execute_query("""
            SELECT COUNT(*) AS rows, COUNT(DISTINCT match_id) AS matches
            FROM odds_snapshots
            WHERE bookmaker = 'Coolbet'
              AND DATE(timestamp AT TIME ZONE 'UTC') = CURRENT_DATE
        """, ()) or [{}])[0]
        # Today's auto-placements
        r2 = (execute_query("""
            SELECT COUNT(*) AS bets, COALESCE(SUM(stake),0)::float AS stake
            FROM real_bets
            WHERE bookmaker = 'Coolbet'
              AND DATE(placed_at) = CURRENT_DATE
              AND notes LIKE 'auto ticket=%%'
        """, ()) or [{}])[0]
        # Pending unplaced
        r3 = (execute_query("""
            SELECT COUNT(*) AS unplaced
            FROM simulated_bets sb
            JOIN matches m ON m.id = sb.match_id
            WHERE sb.result = 'pending'
              AND DATE(m.date) = CURRENT_DATE
              AND m.date > NOW()
              AND sb.edge_percent >= 0.05
              AND NOT EXISTS (
                SELECT 1 FROM real_bets rb
                WHERE rb.simulated_bet_id = sb.id
                  AND DATE(rb.placed_at) = CURRENT_DATE
              )
        """, ()) or [{}])[0]
        msg = (
            "📊 <b>Coolbet daemon — daily summary</b>\n"
            f"Odds ingested today: <b>{r1.get('rows', 0)}</b> rows "
            f"on <b>{r1.get('matches', 0)}</b> matches\n"
            f"Auto-placed: <b>{r2.get('bets', 0)}</b> bets, "
            f"total stake €<b>{float(r2.get('stake') or 0):.2f}</b>\n"
            f"Pending ≥5% edge, not yet placed: <b>{r3.get('unplaced', 0)}</b>"
        )
        return send_telegram(msg, silent=True)
    except Exception as e:
        log.warning("daily summary raised: %s", e)
        return False


def _task_place(mode: str, guard, min_edge: float | None) -> str:
    """mode ∈ {'dry', 'record', 'execute'}. guard = PlacementGuard or None.
    min_edge: decimal fraction (0.05 = 5%); overrides COOLBET_MIN_EDGE env."""
    from workers.automation.coolbet_placer import place_all_bets
    from workers.notify.telegram import send_telegram
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

        # Notify on every successful placement in record/execute mode so the
        # operator sees bets landing in real time. No dedup — each placement
        # is its own event.
        if mode in ("record", "execute"):
            placed = [r for r in results if r.get("outcome") == "placed"]
            # Header icon + label tells the operator at a glance whether this
            # was a paper trade (record, no money moved) or real placement
            # (execute, money moved at Coolbet).
            if mode == "execute":
                icon, label = "💸", "<b>REAL MONEY</b> @ Coolbet"
            else:
                icon, label = "📝", "PAPER (record-only, no money moved)"
            for r in placed:
                stake = float(r.get("stake") or 0)
                edge  = float(r.get("edge_percent") or 0) * 100
                odds  = float(r.get("live_odds") or r.get("ev_odds") or 0)
                ticket = r.get("ticket_id") or "(record only)"
                send_telegram(
                    f"{icon} {label}\n"
                    f"  <b>{r.get('home_team','?')} vs {r.get('away_team','?')}</b>\n"
                    f"  {r.get('market','?')} {r.get('selection','?')} @ {odds:.3f}\n"
                    f"  €{stake:.2f}  ·  edge {edge:+.1f}%  ·  bot {r.get('bot_name','?')}\n"
                    f"  ticket {ticket}",
                )
        return f"place ({mode}) ✓ — {len(results)} evaluated [{summary}]"
    except Exception as e:
        log.warning("place raised: %s\n%s", e, traceback.format_exc())
        send_telegram(
            f"❌ place ({mode}) raised: {str(e)[:300]}",
            dedup_key="coolbet-place-crash",
            dedup_window_s=600,
        )
        return f"place ({mode}) ✗ ({e})"


# ── Driver ───────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keepalive-min", type=int, default=5,
                    help="Heartbeat cadence in minutes (default 5 — matches "
                         "Coolbet frontend's 5-min maintenance ping for "
                         "perfect fingerprint alignment with browser traffic)")
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
    ap.add_argument("--summary-hour", type=int, default=21,
                    help="UTC hour to send the daily Telegram summary (default 21 = 23/24 EEST)")
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
    ap.add_argument("--inplay-mode", choices=("capture", "paper", "execute"),
                    default="paper",
                    help="Inplay snapshot mode. capture=write snapshot row with Coolbet "
                         "odds at decision moment, NO real_bets row, no POST (pure data). "
                         "paper (default)=capture + write real_bets row with "
                         "notes='inplay paper' so it surfaces in existing dashboards / "
                         "daily summary / bot ROI reports, no POST. "
                         "execute=paper + POST /s/bets/bets (REAL MONEY) + record ticket. "
                         "Toggleable via Telegram /inplay_mode.")
    ap.add_argument("--no-inplay-listener", action="store_true",
                    help="Disable the inplay LISTEN/NOTIFY listener thread entirely.")
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
    log.info("  odds      every %d min  (mode=%s)", args.odds_min, args.odds_mode)
    if args.no_place:
        log.info("  place     DISABLED (--no-place)")
    else:
        log.info("  place     every %d min  (mode=%s)", args.place_min, args.place_mode)
    log.info("─" * 78)

    # Startup ping so the operator knows the daemon (re)started. Silent so it
    # doesn't buzz unnecessarily when relaunched mid-day. No-op when
    # TELEGRAM_* env vars aren't set.
    try:
        from workers.notify.telegram import send_telegram
        send_telegram(
            f"🟢 Coolbet daemon started — odds={args.odds_mode}/{args.odds_min}m, "
            f"place={args.place_mode}/{args.place_min}m, min_edge={args.min_edge}",
            silent=True,
        )
    except Exception:
        pass  # never block startup on notify

    # TELEGRAM-COMMANDS — start the two-way bot listener (background thread).
    # Lets the operator control the daemon from their phone:
    #   /help, /status, /pause, /resume, /place_mode <dry|record|execute>,
    #   /relogin, /summary
    try:
        from workers.notify.telegram_bot import start_listener
        from scripts._daemon_handlers import build_handlers  # noqa: F401
        ok = start_listener(build_handlers(args, _CTRL))
        if ok:
            log.info("Telegram command listener started")
    except Exception as e:
        log.warning("Telegram command listener failed to start: %s", e)

    state = _load_state()
    _stamp(state, "last_start", {
        "odds_mode": args.odds_mode, "odds_min": args.odds_min,
        "place_mode": args.place_mode, "place_min": args.place_min,
        "min_edge": args.min_edge,
    })

    session = CoolbetSession()
    # Force initial login so the first keepalive doesn't surprise on auth.
    log.info(_task_keepalive(session))
    _stamp(state, "last_keepalive", {"ok": True, "jwt_ttl_s": int(session.jwt_seconds_remaining)})

    # COOLBET-INPLAY-SNAPSHOTS — start the LISTEN/NOTIFY listener thread.
    # Event-driven: each inplay bot decision INSERT fires a Postgres NOTIFY
    # which this thread picks up and dispatches to capture_inplay_snapshot().
    # Mode defaults to args.inplay_mode (CLI), runtime-overridable via
    # Telegram /inplay_mode. Thread is daemon=True so process exit kills it.
    if not args.no_inplay_listener:
        inplay_thread = threading.Thread(
            target=_inplay_listener_loop, args=(session, args.inplay_mode),
            daemon=True, name="coolbet-inplay-listener",
        )
        inplay_thread.start()
        log.info("Inplay listener thread started (mode=%s)", args.inplay_mode)

    now = time.time()
    next_keepalive   = now + args.keepalive_min * 60
    next_odds        = now            # run odds immediately on start
    next_place       = now            # run place immediately on start
    # JWT auto-renewal cadence: 20 min matches Coolbet's frontend (per the
    # JWT's `renewal_date` field, which sits at the 20-min mark). JWT TTL
    # is 30 min, so renewing at 20 min gives 10 min headroom and mimics the
    # normal browser traffic pattern. First refresh fires at start+20m;
    # the initial JWT from .env carries the daemon through that.
    next_jwt_refresh = now + 20 * 60

    while not _STOP:
        now = time.time()

        if now >= next_keepalive:
            log.info(_task_keepalive(session))
            _stamp(state, "last_keepalive", {"jwt_ttl_s": int(session.jwt_seconds_remaining)})
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
                _stamp(state, "last_sweep_started", {"mode": args.odds_mode})
                def _sweep_runner():
                    try:
                        log.info(_task_odds_snapshot(
                            mode=args.odds_mode,
                            days=args.odds_days,
                            require_pinnacle=args.require_pinnacle,
                        ))
                        _stamp(state, "last_sweep_finished", {"ok": True})
                    except Exception as e:
                        log.warning("sweep thread crashed: %s\n%s", e, traceback.format_exc())
                        _stamp(state, "last_sweep_finished", {"ok": False, "error": str(e)[:200]})
                _SWEEP_THREAD = threading.Thread(target=_sweep_runner, daemon=True,
                                                 name="coolbet-odds-sweep")
                _SWEEP_THREAD.start()
                log.info("odds snapshot ▶  started in background thread")
            next_odds = now + args.odds_min * 60

        # PLACEMENT — skipped while sweep is running so we don't have two
        # Coolbet sessions calling concurrently (each session has its own
        # throttle; concurrent = 2× rate = Imperva risk).

        # Periodic JWT refresh via headless Chrome (manual-JWT mode only).
        # Skipped silently if Chrome / undetected-chromedriver / profile aren't
        # set up — operator hasn't bootstrapped browser auth yet.
        if now >= next_jwt_refresh:
            log.info(_task_jwt_browser_refresh(session))
            _stamp(state, "last_jwt_refresh", {"jwt_ttl_s": int(session.jwt_seconds_remaining)})
            next_jwt_refresh = now + 20 * 60

        # Respect Telegram /relogin — same path as periodic refresh
        if _CTRL.get("force_login"):
            _CTRL["force_login"] = False
            log.info("Telegram /relogin: %s", _task_jwt_browser_refresh(session))
            _stamp(state, "last_jwt_refresh", {"jwt_ttl_s": int(session.jwt_seconds_remaining)})
            next_jwt_refresh = now + 20 * 60

        # Respect Telegram /summary — force the daily summary out now
        if _CTRL.get("force_summary"):
            _CTRL["force_summary"] = False
            if _task_daily_summary():
                log.info("Telegram /summary: pushed daily summary")

        if not args.no_place and now >= next_place:
            if _CTRL.get("paused"):
                log.info("place ⏸  skipped (Telegram /pause)")
            elif _sweep_running():
                log.info("place ⏸  skipped (sweep in progress); retry in %d min", args.place_min)
            else:
                # Telegram /place_mode override takes precedence over CLI arg
                effective_mode = _CTRL.get("place_mode") or args.place_mode
                log.info(_task_place(effective_mode, guard, args.min_edge))
                _stamp(state, "last_place_attempt", {"mode": effective_mode})
            next_place = now + args.place_min * 60

        # DAILY-SUMMARY — once per UTC day at the configured hour. Uses state
        # to track "last summary date" so a daemon restart doesn't re-send
        # the same day's summary.
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour >= args.summary_hour:
            today_iso = now_utc.date().isoformat()
            last_done = (state.get("last_daily_summary") or {}).get("date")
            if last_done != today_iso:
                if _task_daily_summary():
                    log.info("daily summary ✓ pushed to Telegram")
                state["last_daily_summary"] = {
                    "ts": now_utc.isoformat(), "date": today_iso,
                }
                _save_state(state)

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
