"""
OddsIntel — B7 Bot Validation Milestone Tracker
Checks if any paper-trading bot has crossed the Elite tier launch threshold:
  - 60+ settled bets
  - Positive ROI

Usage:
  python scripts/check_bot_validation.py

Exit codes:
  0 — no bot has crossed the launch threshold yet
  1 — at least one bot is ready for Elite launch
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import get_client

LAUNCH_MIN_BETS = 60
LAUNCH_MIN_ROI = 0.0  # > 0%

# ── optional rich ──────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    USE_RICH = True
except ImportError:
    USE_RICH = False


def _print(msg: str, style: str = ""):
    if USE_RICH:
        console.print(f"[{style}]{msg}[/{style}]" if style else msg)
    else:
        print(msg)


def _days_since(iso_str: str) -> float:
    """Return fractional days since an ISO datetime string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return 0.0


def fetch_bot_stats(client) -> list[dict]:
    """
    Pull all settled bets (won + lost) joined with bots, compute per-bot stats.
    """
    # All bots
    bots_raw = client.table("bots").select("id, name, strategy").execute().data or []
    bots_by_id = {b["id"]: b for b in bots_raw}

    # All bets — including pending so we can compute earliest pick_time per bot
    all_bets = client.table("simulated_bets").select(
        "bot_id, result, odds_at_pick, stake, pnl, clv, pick_time"
    ).execute().data or []

    # Group by bot
    stats: dict[str, dict] = {}

    for b in bots_by_id.values():
        stats[b["id"]] = {
            "bot_id": b["id"],
            "name": b["name"],
            "strategy": b.get("strategy", ""),
            "settled": 0,
            "won": 0,
            "lost": 0,
            "total_staked": 0.0,
            "total_pnl": 0.0,
            "clv_sum": 0.0,
            "clv_count": 0,
            "pending": 0,
            "earliest_pick": None,
        }

    for bet in all_bets:
        bid = bet["bot_id"]
        if bid not in stats:
            # Orphaned bet — bot not in bots table, add minimal entry
            stats[bid] = {
                "bot_id": bid,
                "name": f"unknown ({bid[:8]})",
                "strategy": "",
                "settled": 0,
                "won": 0,
                "lost": 0,
                "total_staked": 0.0,
                "total_pnl": 0.0,
                "clv_sum": 0.0,
                "clv_count": 0,
                "pending": 0,
                "earliest_pick": None,
            }

        s = stats[bid]

        # Track earliest pick for days-running calculation
        pick_time = bet.get("pick_time")
        if pick_time:
            if s["earliest_pick"] is None or pick_time < s["earliest_pick"]:
                s["earliest_pick"] = pick_time

        result = bet.get("result", "pending")
        if result == "pending":
            s["pending"] += 1
            continue

        # Settled bet
        s["settled"] += 1
        stake = float(bet.get("stake") or 0)
        pnl = float(bet.get("pnl") or 0)
        s["total_staked"] += stake
        s["total_pnl"] += pnl

        if result == "won":
            s["won"] += 1
        elif result == "lost":
            s["lost"] += 1

        clv = bet.get("clv")
        if clv is not None:
            s["clv_sum"] += float(clv)
            s["clv_count"] += 1

    # Derive ROI, win rate, avg CLV, days running
    results = []
    for s in stats.values():
        roi = (s["total_pnl"] / s["total_staked"] * 100) if s["total_staked"] > 0 else 0.0
        win_rate = (s["won"] / s["settled"] * 100) if s["settled"] > 0 else 0.0
        avg_clv = (s["clv_sum"] / s["clv_count"]) if s["clv_count"] > 0 else None
        days_running = _days_since(s["earliest_pick"]) if s["earliest_pick"] else 0.0

        results.append({
            **s,
            "roi": round(roi, 2),
            "win_rate": round(win_rate, 1),
            "avg_clv": round(avg_clv, 4) if avg_clv is not None else None,
            "days_running": round(days_running, 1),
            "launch_ready": s["settled"] >= LAUNCH_MIN_BETS and roi > LAUNCH_MIN_ROI,
        })

    # Sort by settled bets descending
    results.sort(key=lambda x: x["settled"], reverse=True)
    return results


def print_table_rich(bot_stats: list[dict]):
    t = Table(
        title="[bold]OddsIntel — Bot Validation Status (B7)[/bold]",
        box=box.ROUNDED,
        show_lines=True,
    )
    t.add_column("Bot", style="cyan", min_width=22)
    t.add_column("Settled", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Lost", justify="right")
    t.add_column("Win%", justify="right")
    t.add_column("ROI%", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("Days", justify="right")
    t.add_column("Status", justify="center")

    for s in bot_stats:
        roi_str = f"{s['roi']:+.2f}%"
        roi_style = "green" if s["roi"] > 0 else "red"

        clv_str = f"{s['avg_clv']:+.4f}" if s["avg_clv"] is not None else "N/A"
        status = "[bold green]READY[/bold green]" if s["launch_ready"] else "[dim]Not yet[/dim]"

        t.add_row(
            s["name"],
            str(s["settled"]),
            str(s["won"]),
            str(s["lost"]),
            f"{s['win_rate']:.1f}%",
            f"[{roi_style}]{roi_str}[/{roi_style}]",
            clv_str,
            str(s["days_running"]),
            status,
        )

    console.print()
    console.print(t)
    console.print()


def print_table_plain(bot_stats: list[dict]):
    header = (
        f"{'Bot':<25} {'Settled':>7} {'Won':>5} {'Lost':>5} "
        f"{'Win%':>6} {'ROI%':>8} {'AvgCLV':>9} {'Days':>5} {'Status':<12}"
    )
    sep = "-" * len(header)
    print()
    print("OddsIntel — Bot Validation Status (B7)")
    print(sep)
    print(header)
    print(sep)

    for s in bot_stats:
        roi_str = f"{s['roi']:+.2f}%"
        clv_str = f"{s['avg_clv']:+.4f}" if s["avg_clv"] is not None else "N/A"
        status = "*** READY ***" if s["launch_ready"] else "-"
        print(
            f"{s['name']:<25} {s['settled']:>7} {s['won']:>5} {s['lost']:>5} "
            f"{s['win_rate']:>5.1f}% {roi_str:>8} {clv_str:>9} {s['days_running']:>5} {status:<12}"
        )

    print(sep)
    print()


def main() -> int:
    client = get_client()
    _print("[bold]OddsIntel — B7 Bot Validation Milestone Tracker[/bold]\n", "")

    bot_stats = fetch_bot_stats(client)

    if not bot_stats:
        _print("No bots found in the database.", "yellow")
        return 0

    if USE_RICH:
        print_table_rich(bot_stats)
    else:
        print_table_plain(bot_stats)

    # ── Progress report: bot closest to threshold ──────────────────────────────
    best = max(bot_stats, key=lambda x: x["settled"])
    progress_pct = min(100, round(best["settled"] / LAUNCH_MIN_BETS * 100, 1))
    _print(
        f"Progress (closest to threshold): "
        f"{best['name']} — {best['settled']}/{LAUNCH_MIN_BETS} bets settled "
        f"({progress_pct}%)",
        "cyan",
    )
    _print("")

    # ── Launch threshold checks ────────────────────────────────────────────────
    ready_bots = [s for s in bot_stats if s["launch_ready"]]
    any_ready = len(ready_bots) > 0

    if any_ready:
        for s in ready_bots:
            msg = (
                f"LAUNCH THRESHOLD MET — {s['name'].upper()} IS READY FOR ELITE LAUNCH  "
                f"({s['settled']} bets, ROI {s['roi']:+.2f}%)"
            )
            _print(msg, "bold green")
    else:
        _print(
            f"LAUNCH THRESHOLD NOT YET MET  "
            f"(need {LAUNCH_MIN_BETS}+ settled bets AND positive ROI per bot)",
            "yellow",
        )
        _print(
            f"Threshold: {LAUNCH_MIN_BETS} settled bets, ROI > {LAUNCH_MIN_ROI:.0f}%",
            "dim",
        )

    _print("")

    # Exit 1 if any bot is ready (useful for CI gating)
    return 1 if any_ready else 0


if __name__ == "__main__":
    sys.exit(main())
