"""One-shot daemon health snapshot.

Reads existing tables (no new state) and prints session/sweep/placement
overview. Run anytime from terminal:

    python3 scripts/coolbet_status.py

Useful when you've stepped away from tmux and want to know if anything's
still happening, plus how productive the daemon has been.

Doesn't talk to Coolbet — all info comes from local DB + env. Safe to
run repeatedly without API cost.
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

from workers.api_clients.supabase_client import execute_query

console = Console()


def _fmt_age(dt) -> str:
    """'2 min ago', '3h 14m ago', '6h ago', ..."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = (datetime.now(timezone.utc) - dt).total_seconds()
    if sec < 60:
        return f"{int(sec)}s ago"
    if sec < 3600:
        return f"{int(sec/60)} min ago"
    if sec < 86400:
        return f"{int(sec/3600)}h {int((sec%3600)/60)}m ago"
    return f"{int(sec/86400)}d ago"


def _fmt_time(dt) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%H:%M:%S UTC")


def section_daemon_state() -> None:
    """Read ~/.coolbet-daemon/state.json (written by daemon) to show internal
    timing — sweep cycle, last placement attempt, etc."""
    import json
    state_path = Path.home() / ".coolbet-daemon" / "state.json"
    if not state_path.exists():
        return  # daemon hasn't written one yet
    try:
        st = json.loads(state_path.read_text())
    except Exception:
        return
    console.print("\n[bold cyan]Daemon state (from state.json)[/bold cyan]")
    def _ts(key):
        e = st.get(key)
        if not e: return None
        try:
            return datetime.fromisoformat(e["ts"])
        except Exception:
            return None
    start = _ts("last_start")
    ka    = _ts("last_keepalive")
    s_s   = _ts("last_sweep_started")
    s_f   = _ts("last_sweep_finished")
    p_a   = _ts("last_place_attempt")
    if start: console.print(f"  started:           {_fmt_time(start)} ({_fmt_age(start)})")
    if ka:    console.print(f"  last keepalive:    {_fmt_time(ka)} ({_fmt_age(ka)})  "
                            f"JWT TTL ≈ {st.get('last_keepalive',{}).get('jwt_ttl_s','?')}s")
    if s_s:
        line = f"  last sweep started: {_fmt_time(s_s)} ({_fmt_age(s_s)})"
        if s_f and s_f >= s_s:
            line += f"  → finished {_fmt_age(s_f)}"
        else:
            line += "  [yellow](in progress)[/yellow]"
        console.print(line)
    if p_a:   console.print(f"  last placement attempt: {_fmt_time(p_a)} ({_fmt_age(p_a)})")


def section_session() -> None:
    console.print("\n[bold cyan]Session[/bold cyan]")
    # The daemon's keepalive doesn't write to any table directly — but every
    # successful Coolbet API call (search, fo-category, sidebets, odds) is
    # evidence that the session is alive. So "most recent Coolbet odds row" is
    # a strong proxy for "session is working".
    rows = execute_query(
        "SELECT MAX(timestamp) AS last_cb_ts FROM odds_snapshots WHERE bookmaker = 'Coolbet'",
        (),
    )
    last_cb = rows[0]["last_cb_ts"] if rows else None
    if last_cb is None:
        console.print("  [yellow]No Coolbet rows ever. Daemon never ran or always failed.[/yellow]")
        return
    age_sec = (datetime.now(timezone.utc) - (last_cb if last_cb.tzinfo else last_cb.replace(tzinfo=timezone.utc))).total_seconds()
    status_icon = "✓" if age_sec < 3600 else ("⚠" if age_sec < 6 * 3600 else "✗")
    console.print(f"  {status_icon}  last Coolbet API write: {_fmt_time(last_cb)} ({_fmt_age(last_cb)})")
    if age_sec >= 3600:
        console.print("     [yellow]> 1h since last write — daemon may be stalled or Imperva-blocked[/yellow]")


def section_sweep() -> None:
    console.print("\n[bold cyan]Odds ingest (Coolbet)[/bold cyan]")
    rows = execute_query(
        """
        SELECT
          COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '1 hour')   AS rows_1h,
          COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '6 hours')  AS rows_6h,
          COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '24 hours') AS rows_24h,
          COUNT(DISTINCT match_id) FILTER (WHERE timestamp > NOW() - INTERVAL '1 hour')   AS matches_1h,
          COUNT(DISTINCT match_id) FILTER (WHERE timestamp > NOW() - INTERVAL '24 hours') AS matches_24h
        FROM odds_snapshots
        WHERE bookmaker = 'Coolbet'
        """,
        (),
    )
    r = rows[0] if rows else {}
    t = Table(show_header=True)
    t.add_column("Window")
    t.add_column("Rows", justify="right")
    t.add_column("Distinct matches", justify="right")
    t.add_row("last 1h",  str(r.get("rows_1h", 0) or 0),  str(r.get("matches_1h", 0) or 0))
    t.add_row("last 6h",  str(r.get("rows_6h", 0) or 0),  "—")
    t.add_row("last 24h", str(r.get("rows_24h", 0) or 0), str(r.get("matches_24h", 0) or 0))
    console.print(t)


def section_placement() -> None:
    console.print("\n[bold cyan]Placements (real_bets, Coolbet, auto)[/bold cyan]")
    rows = execute_query(
        """
        SELECT
          COUNT(*)             AS today_total,
          COALESCE(SUM(stake), 0)::float AS today_stake,
          MAX(placed_at)       AS last_placed
        FROM real_bets
        WHERE bookmaker = 'Coolbet'
          AND DATE(placed_at) = CURRENT_DATE
          AND notes LIKE 'auto ticket=%%'
        """,
        (),
    )
    r = rows[0] if rows else {}
    console.print(f"  today auto-placed: {r.get('today_total', 0)}  "
                  f"(total stake €{float(r.get('today_stake') or 0):.2f})  "
                  f"last: {_fmt_age(r.get('last_placed'))}")

    # Show the last 5
    rows = execute_query(
        """
        SELECT rb.placed_at, rb.stake, rb.actual_odds, rb.market, rb.selection,
               ht.name AS home, at2.name AS away,
               rb.result, rb.pnl
        FROM real_bets rb
        JOIN matches m  ON m.id = rb.match_id
        JOIN teams ht   ON ht.id = m.home_team_id
        JOIN teams at2  ON at2.id = m.away_team_id
        WHERE rb.bookmaker = 'Coolbet'
          AND rb.notes LIKE 'auto ticket=%%'
        ORDER BY rb.placed_at DESC
        LIMIT 5
        """,
        (),
    )
    if rows:
        t = Table(show_header=True, title="Last 5 auto-placements")
        t.add_column("Time")
        t.add_column("Match")
        t.add_column("Bet")
        t.add_column("€ stake", justify="right")
        t.add_column("Odds", justify="right")
        t.add_column("Result", justify="right")
        for r in rows:
            res = r.get("result") or "pending"
            pnl = f" ({float(r['pnl']):+.2f})" if r.get("pnl") is not None else ""
            t.add_row(
                _fmt_time(r["placed_at"]),
                f"{r['home'][:20]} vs {r['away'][:20]}",
                f"{r['market']} {r['selection']}",
                f"{float(r['stake']):.2f}",
                f"{float(r['actual_odds']):.2f}",
                f"{res}{pnl}",
            )
        console.print(t)


def section_pending() -> None:
    console.print("\n[bold cyan]Pending value bets (would auto-place)[/bold cyan]")
    rows = execute_query(
        """
        SELECT
          COUNT(*) AS pending_today,
          COUNT(*) FILTER (WHERE sb.edge_percent >= 0.05) AS at_or_above_5pct,
          COUNT(*) FILTER (WHERE sb.edge_percent >= 0.05
                           AND NOT EXISTS (
                             SELECT 1 FROM real_bets rb
                             WHERE rb.simulated_bet_id = sb.id
                               AND DATE(rb.placed_at) = CURRENT_DATE
                           )) AS not_yet_placed
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result = 'pending'
          AND DATE(m.date) = CURRENT_DATE
          AND m.date > NOW()
        """,
        (),
    )
    r = rows[0] if rows else {}
    console.print(f"  pending bets today: {r.get('pending_today', 0)}  |  "
                  f"≥5% edge: {r.get('at_or_above_5pct', 0)}  |  "
                  f"not yet placed: {r.get('not_yet_placed', 0)}")


def section_tmux() -> None:
    """Best-effort tmux check (just shows whether the session exists)."""
    import subprocess
    console.print("\n[bold cyan]Daemon process[/bold cyan]")
    try:
        out = subprocess.run(["tmux", "ls"], capture_output=True, text=True, timeout=2)
        lines = [l for l in out.stdout.splitlines() if "coolbet" in l]
        if lines:
            console.print(f"  ✓  tmux session: {lines[0]}")
            console.print("     attach: [dim]tmux attach -t coolbet[/dim]")
        else:
            console.print("  [yellow]✗ No tmux session named 'coolbet' — daemon not running[/yellow]")
    except Exception:
        console.print("  (tmux not available — skip)")


def main() -> None:
    console.print(f"\n[bold green]Coolbet daemon status[/bold green] · "
                  f"now {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    section_session()
    section_daemon_state()
    section_tmux()
    section_sweep()
    section_pending()
    section_placement()
    console.print()


if __name__ == "__main__":
    main()
