"""
OddsIntel — B5 Tier B League Validation
Validates ROI and model edge for Tier B leagues (Scotland, Austria, Ireland lower
divisions, etc.) using settled historical bets stored in the DB.

Leagues with tier >= 2 in the `leagues` table are considered Tier B.

Usage:
  python scripts/backtest_tier_b.py

Outputs:
  - Sorted table of Tier B leagues (most bets first)
  - VALIDATED flag  (settled >= 20, ROI > +5%)
  - AVOID flag      (settled >= 20, ROI < -5%)
  - Summary of how many leagues are validated vs. need more data
  - Saves JSON to data/logs/tier_b_validation_YYYY-MM-DD.json
"""

import sys
import json
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import get_client

TIER_B_MIN = 2          # leagues.tier >= this are Tier B
VALIDATED_MIN_BETS = 20
VALIDATED_MIN_ROI = 5.0   # %
AVOID_MAX_ROI = -5.0      # %

LOG_DIR = Path(__file__).parent.parent / "data" / "logs"

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


def fetch_tier_b_league_stats(client) -> list[dict]:
    """
    Join simulated_bets → matches → leagues where tier >= TIER_B_MIN,
    compute per-league stats over settled bets only.
    """
    # Step 1: Get all Tier B league IDs and names
    leagues_raw = client.table("leagues").select(
        "id, name, country, tier"
    ).gte("tier", TIER_B_MIN).execute().data or []

    if not leagues_raw:
        return []

    leagues_by_id = {lg["id"]: lg for lg in leagues_raw}
    league_ids = list(leagues_by_id.keys())

    # Step 2: Get all matches in Tier B leagues
    # Supabase Python SDK doesn't support IN filters natively in a single call
    # for large sets, but league_ids should be manageable
    all_matches = []
    # Fetch in chunks of 100 to stay within URL length limits
    chunk_size = 100
    for i in range(0, len(league_ids), chunk_size):
        chunk = league_ids[i : i + chunk_size]
        rows = client.table("matches").select(
            "id, league_id"
        ).in_("league_id", chunk).execute().data or []
        all_matches.extend(rows)

    if not all_matches:
        return []

    match_league = {m["id"]: m["league_id"] for m in all_matches}
    match_ids = list(match_league.keys())

    # Step 3: Get all settled simulated_bets for those matches
    all_bets = []
    for i in range(0, len(match_ids), chunk_size):
        chunk = match_ids[i : i + chunk_size]
        rows = client.table("simulated_bets").select(
            "match_id, result, stake, pnl, edge_percent, clv"
        ).in_("match_id", chunk).neq("result", "pending").execute().data or []
        all_bets.extend(rows)

    if not all_bets:
        return []

    # Step 4: Aggregate per league
    league_stats: dict[str, dict] = {}
    for lg in leagues_raw:
        league_stats[lg["id"]] = {
            "league_id": lg["id"],
            "name": lg["name"],
            "country": lg["country"],
            "tier": lg["tier"],
            "settled": 0,
            "won": 0,
            "total_staked": 0.0,
            "total_pnl": 0.0,
            "edge_sum": 0.0,
            "edge_count": 0,
            "clv_sum": 0.0,
            "clv_count": 0,
        }

    for bet in all_bets:
        match_id = bet["match_id"]
        league_id = match_league.get(match_id)
        if not league_id or league_id not in league_stats:
            continue

        s = league_stats[league_id]
        s["settled"] += 1
        stake = float(bet.get("stake") or 0)
        pnl = float(bet.get("pnl") or 0)
        s["total_staked"] += stake
        s["total_pnl"] += pnl

        if bet.get("result") == "won":
            s["won"] += 1

        edge = bet.get("edge_percent")
        if edge is not None:
            s["edge_sum"] += float(edge)
            s["edge_count"] += 1

        clv = bet.get("clv")
        if clv is not None:
            s["clv_sum"] += float(clv)
            s["clv_count"] += 1

    # Step 5: Derive computed fields, filter to leagues that have any data
    results = []
    for s in league_stats.values():
        if s["settled"] == 0:
            continue  # skip leagues with no bets at all

        roi = (s["total_pnl"] / s["total_staked"] * 100) if s["total_staked"] > 0 else 0.0
        win_rate = (s["won"] / s["settled"] * 100) if s["settled"] > 0 else 0.0
        avg_edge = (s["edge_sum"] / s["edge_count"]) if s["edge_count"] > 0 else None
        avg_clv = (s["clv_sum"] / s["clv_count"]) if s["clv_count"] > 0 else None

        validated = s["settled"] >= VALIDATED_MIN_BETS and roi > VALIDATED_MIN_ROI
        avoid = s["settled"] >= VALIDATED_MIN_BETS and roi < AVOID_MAX_ROI
        needs_data = s["settled"] < VALIDATED_MIN_BETS

        results.append({
            "league_id": s["league_id"],
            "league": f"{s['country']} / {s['name']}",
            "name": s["name"],
            "country": s["country"],
            "tier": s["tier"],
            "settled": s["settled"],
            "win_rate": round(win_rate, 1),
            "roi": round(roi, 2),
            "avg_edge": round(avg_edge, 2) if avg_edge is not None else None,
            "avg_clv": round(avg_clv, 4) if avg_clv is not None else None,
            "validated": validated,
            "avoid": avoid,
            "needs_data": needs_data,
        })

    # Sort by settled bets descending
    results.sort(key=lambda x: x["settled"], reverse=True)
    return results


def print_table_rich(league_stats: list[dict]):
    t = Table(
        title="[bold]OddsIntel — Tier B League Validation (B5)[/bold]",
        box=box.ROUNDED,
        show_lines=True,
    )
    t.add_column("League", min_width=30)
    t.add_column("Tier", justify="right")
    t.add_column("Settled", justify="right")
    t.add_column("Win%", justify="right")
    t.add_column("ROI%", justify="right")
    t.add_column("Avg Edge%", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("Verdict", justify="center")

    for s in league_stats:
        roi_str = f"{s['roi']:+.2f}%"
        roi_style = "green" if s["roi"] > 0 else "red"

        edge_str = f"{s['avg_edge']:+.2f}%" if s["avg_edge"] is not None else "N/A"
        clv_str = f"{s['avg_clv']:+.4f}" if s["avg_clv"] is not None else "N/A"

        if s["validated"]:
            verdict = "[bold green]VALIDATED[/bold green] ✓"
        elif s["avoid"]:
            verdict = "[bold red]AVOID[/bold red] ✗"
        elif s["needs_data"]:
            verdict = f"[dim]{s['settled']}/{VALIDATED_MIN_BETS}[/dim]"
        else:
            verdict = "[dim]Neutral[/dim]"

        t.add_row(
            s["league"],
            str(s["tier"]),
            str(s["settled"]),
            f"{s['win_rate']:.1f}%",
            f"[{roi_style}]{roi_str}[/{roi_style}]",
            edge_str,
            clv_str,
            verdict,
        )

    console.print()
    console.print(t)
    console.print()


def print_table_plain(league_stats: list[dict]):
    header = (
        f"{'League':<35} {'Tier':>4} {'Settled':>7} {'Win%':>6} "
        f"{'ROI%':>8} {'AvgEdge%':>9} {'AvgCLV':>9} {'Verdict':<18}"
    )
    sep = "-" * len(header)
    print()
    print("OddsIntel — Tier B League Validation (B5)")
    print(sep)
    print(header)
    print(sep)

    for s in league_stats:
        roi_str = f"{s['roi']:+.2f}%"
        edge_str = f"{s['avg_edge']:+.2f}%" if s["avg_edge"] is not None else "N/A"
        clv_str = f"{s['avg_clv']:+.4f}" if s["avg_clv"] is not None else "N/A"

        if s["validated"]:
            verdict = "VALIDATED ✓"
        elif s["avoid"]:
            verdict = "AVOID ✗"
        elif s["needs_data"]:
            verdict = f"{s['settled']}/{VALIDATED_MIN_BETS} bets"
        else:
            verdict = "Neutral"

        print(
            f"{s['league']:<35} {s['tier']:>4} {s['settled']:>7} {s['win_rate']:>5.1f}% "
            f"{roi_str:>8} {edge_str:>9} {clv_str:>9} {verdict:<18}"
        )

    print(sep)
    print()


def save_results(league_stats: list[dict]) -> Path:
    today = date.today().isoformat()
    out_path = LOG_DIR / f"tier_b_validation_{today}.json"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": today,
        "thresholds": {
            "tier_b_min": TIER_B_MIN,
            "validated_min_bets": VALIDATED_MIN_BETS,
            "validated_min_roi_pct": VALIDATED_MIN_ROI,
            "avoid_max_roi_pct": AVOID_MAX_ROI,
        },
        "leagues": league_stats,
    }

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    return out_path


def main() -> int:
    client = get_client()
    _print("[bold]OddsIntel — B5 Tier B League Validation[/bold]\n", "")

    league_stats = fetch_tier_b_league_stats(client)

    if not league_stats:
        _print(
            "No settled bets found for Tier B leagues yet.\n"
            "Bots started 2026-04-27 — check back after first settlements.",
            "yellow",
        )
        return 0

    if USE_RICH:
        print_table_rich(league_stats)
    else:
        print_table_plain(league_stats)

    # ── Summary ────────────────────────────────────────────────────────────────
    validated = [s for s in league_stats if s["validated"]]
    avoid = [s for s in league_stats if s["avoid"]]
    needs_data = [s for s in league_stats if s["needs_data"]]
    neutral = [s for s in league_stats if not s["validated"] and not s["avoid"] and not s["needs_data"]]

    total = len(league_stats)
    _print(f"Summary: {total} Tier B league(s) with at least 1 settled bet", "bold")
    _print(f"  Validated (>= {VALIDATED_MIN_BETS} bets, ROI > +{VALIDATED_MIN_ROI:.0f}%): {len(validated)}", "green")
    _print(f"  Avoid     (>= {VALIDATED_MIN_BETS} bets, ROI < {AVOID_MAX_ROI:.0f}%): {len(avoid)}", "red")
    _print(f"  Neutral   (>= {VALIDATED_MIN_BETS} bets, ROI in [{AVOID_MAX_ROI:.0f}%, +{VALIDATED_MIN_ROI:.0f}%]): {len(neutral)}", "")
    _print(f"  Need more data (< {VALIDATED_MIN_BETS} bets): {len(needs_data)}", "dim")
    _print("")

    if validated:
        _print("Validated leagues (promote to Tier A consideration):", "bold green")
        for s in validated:
            _print(f"  {s['league']}  — {s['settled']} bets, ROI {s['roi']:+.2f}%", "green")
        _print("")

    if avoid:
        _print("Leagues to avoid (negative edge confirmed):", "bold red")
        for s in avoid:
            _print(f"  {s['league']}  — {s['settled']} bets, ROI {s['roi']:+.2f}%", "red")
        _print("")

    # ── Save JSON ──────────────────────────────────────────────────────────────
    out_path = save_results(league_stats)
    _print(f"Results saved to: {out_path}", "dim")
    _print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
