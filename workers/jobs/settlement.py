"""
OddsIntel — Settlement Pipeline
Fetches finished match results and settles all pending bets.
Also computes Closing Line Value (CLV) for each settled bet.

Run this in the evening after matches finish (21:00 UTC / midnight EET).

Usage:
  python settlement.py           # Settle today's finished matches
  python settlement.py --report  # Show settled P&L summary
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone, date, timedelta
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.flashscore import get_todays_matches_from_flashscore
from workers.api_clients.supabase_client import get_client

console = Console()


# ─── Result matching ─────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip common suffixes for fuzzy matching"""
    name = name.lower().strip()
    for suffix in [" fc", " sc", " cf", " ac", " fk", " sk", " bk", " if", " afc", " utd", " united"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def match_score(db_name: str, result_name: str) -> float:
    """0-1 similarity score between two team names"""
    a = normalize_name(db_name)
    b = normalize_name(result_name)
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    # Common prefix
    min_len = min(len(a), len(b))
    if min_len >= 4:
        prefix_match = sum(1 for i in range(min_len) if a[i] == b[i])
        if prefix_match >= min_len * 0.7:
            return 0.7
    return 0.0


def find_result_for_match(db_home: str, db_away: str,
                          results: list[dict]) -> dict | None:
    """Find the matching result for a DB match from Sofascore results list"""
    best_score = 0
    best_match = None

    for r in results:
        if r.get("home_goals") is None:
            continue  # not finished

        h_score = match_score(db_home, r["home_team"])
        a_score = match_score(db_away, r["away_team"])
        combined = (h_score + a_score) / 2

        if combined > best_score and combined >= 0.7:
            best_score = combined
            best_match = r

    return best_match


# ─── Bet settlement logic ────────────────────────────────────────────────────

def settle_bet_result(bet: dict, home_goals: int, away_goals: int,
                      closing_odds: float | None) -> dict:
    """
    Determine if a bet won or lost.
    Returns dict with result, pnl, clv.
    """
    market = bet["market"].lower()
    selection = bet["selection"].lower()
    stake = bet["stake"]
    odds = bet["odds_at_pick"]
    total_goals = home_goals + away_goals

    won = False

    if market == "1x2":
        if selection == "home" and home_goals > away_goals:
            won = True
        elif selection in ("draw", "x") and home_goals == away_goals:
            won = True
        elif selection == "away" and away_goals > home_goals:
            won = True

    elif "over_under" in market or "o/u" in market:
        # Extract line from market name: "over_under_25" → 2.5
        line = 2.5
        for part in market.split("_"):
            try:
                line = int(part) / 10 if len(part) == 2 else float(part)
                if 0 < line < 10:
                    break
            except ValueError:
                continue

        if "over" in selection and total_goals > line:
            won = True
        elif "under" in selection and total_goals < line:
            won = True

    pnl = round((odds - 1) * stake if won else -stake, 2)

    # CLV: positive = we got better odds than closing line
    clv = None
    if closing_odds and closing_odds > 0:
        clv = round((odds / closing_odds) - 1, 4)

    return {
        "result": "won" if won else "lost",
        "pnl": pnl,
        "clv": clv,
    }


# ─── Closing odds lookup ─────────────────────────────────────────────────────

def get_closing_odds(client, match_id: str, market: str, selection: str) -> float | None:
    """Get the closing odds for a match/market/selection from odds_snapshots"""
    result = client.table("odds_snapshots").select("odds").eq(
        "match_id", match_id
    ).eq("market", market).eq("selection", selection).eq(
        "is_closing", True
    ).order("timestamp", desc=True).limit(1).execute()

    if result.data:
        return float(result.data[0]["odds"])

    # Fallback: use the latest snapshot (closest to closing)
    result2 = client.table("odds_snapshots").select("odds").eq(
        "match_id", match_id
    ).eq("market", market).eq("selection", selection).order(
        "timestamp", desc=True
    ).limit(1).execute()

    return float(result2.data[0]["odds"]) if result2.data else None


# ─── Main settlement ──────────────────────────────────────────────────────────

def run_settlement():
    today = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Settlement: {today} ═══[/bold green]\n")

    client = get_client()

    # 1. Get pending bets with match info
    console.print("[cyan]Loading pending bets...[/cyan]")
    bets_result = client.table("simulated_bets").select(
        "*, matches(id, date, score_home, score_away, result, status, "
        "home_team:home_team_id(name), away_team:away_team_id(name))"
    ).eq("result", "pending").execute()

    pending = bets_result.data
    console.print(f"  {len(pending)} pending bets")

    if not pending:
        console.print("[yellow]No pending bets to settle.[/yellow]")
        return

    # 2. Fetch today's and yesterday's results from Sofascore
    console.print("\n[cyan]Fetching match results from Sofascore...[/cyan]")
    results = get_todays_matches_from_flashscore()

    # Also check yesterday for late-night matches
    finished = [r for r in results
                if r.get("status") in ("FT", "finished", "6", "7")
                and r.get("home_goals") is not None]
    console.print(f"  {len(finished)} finished matches found")

    if not finished:
        console.print("[yellow]No finished matches yet. Try again later.[/yellow]")
        return

    # 3. Also update match scores in DB directly
    console.print("\n[cyan]Updating match results in Supabase...[/cyan]")
    for r in finished:
        # Try to find matching DB match
        today_str = date.today().isoformat()
        home_result = client.table("teams").select("id").eq("name", r["home_team"]).execute()
        away_result = client.table("teams").select("id").eq("name", r["away_team"]).execute()

        if not home_result.data or not away_result.data:
            continue

        home_id = home_result.data[0]["id"]
        away_id = away_result.data[0]["id"]

        match_q = client.table("matches").select("id").eq(
            "home_team_id", home_id
        ).eq("away_team_id", away_id).gte(
            "date", f"{today_str}T00:00:00"
        ).execute()

        if match_q.data:
            match_id = match_q.data[0]["id"]
            hg, ag = int(r["home_goals"]), int(r["away_goals"])
            result_str = "home" if hg > ag else "away" if ag > hg else "draw"
            client.table("matches").update({
                "score_home": hg, "score_away": ag,
                "result": result_str, "status": "finished",
            }).eq("id", match_id).execute()

    # 4. Settle each bet
    console.print("\n[cyan]Settling bets...[/cyan]\n")

    settled = 0
    skipped = 0
    total_pnl = 0.0
    clv_values = []

    by_bot: dict[str, dict] = {}

    t = Table(title="Settlement Results")
    t.add_column("Match", style="cyan")
    t.add_column("Bet")
    t.add_column("Score")
    t.add_column("Result")
    t.add_column("P&L", justify="right")
    t.add_column("CLV", justify="right")

    for bet in pending:
        match = bet.get("matches", {})
        if not match:
            skipped += 1
            continue

        # Check if match result is already in DB
        score_home = match.get("score_home")
        score_away = match.get("score_away")

        # If not in DB, try to find in Sofascore results
        if score_home is None:
            home_name = (match["home_team"][0]["name"] if isinstance(match.get("home_team"), list)
                        else match.get("home_team", {}).get("name", ""))
            away_name = (match["away_team"][0]["name"] if isinstance(match.get("away_team"), list)
                        else match.get("away_team", {}).get("name", ""))

            result_match = find_result_for_match(home_name, away_name, finished)
            if not result_match:
                skipped += 1
                continue

            score_home = int(result_match["home_goals"])
            score_away = int(result_match["away_goals"])
            home_name_display = home_name
            away_name_display = away_name
        else:
            home_name_display = (match["home_team"][0]["name"] if isinstance(match.get("home_team"), list)
                                else match.get("home_team", {}).get("name", "?"))
            away_name_display = (match["away_team"][0]["name"] if isinstance(match.get("away_team"), list)
                                else match.get("away_team", {}).get("name", "?"))

        # Get closing odds for CLV
        match_id = match["id"]
        market = bet["market"]
        selection = bet["selection"]
        closing_odds = get_closing_odds(client, match_id, market, selection)

        # Settle
        settlement = settle_bet_result(bet, score_home, score_away, closing_odds)

        # Get bot bankroll
        bot_id = bet["bot_id"]
        if bot_id not in by_bot:
            bot_data = client.table("bots").select("current_bankroll, name").eq("id", bot_id).execute()
            by_bot[bot_id] = {
                "bankroll": float(bot_data.data[0]["current_bankroll"]) if bot_data.data else 1000.0,
                "name": bot_data.data[0]["name"] if bot_data.data else "unknown",
            }

        new_bankroll = by_bot[bot_id]["bankroll"] + settlement["pnl"]
        by_bot[bot_id]["bankroll"] = new_bankroll

        # Update DB
        client.table("simulated_bets").update({
            "result": settlement["result"],
            "pnl": settlement["pnl"],
            "bankroll_after": new_bankroll,
            "closing_odds": closing_odds,
            "clv": settlement["clv"],
        }).eq("id", bet["id"]).execute()

        settled += 1
        total_pnl += settlement["pnl"]
        if settlement["clv"] is not None:
            clv_values.append(settlement["clv"])

        result_color = "green" if settlement["result"] == "won" else "red"
        clv_str = f"{settlement['clv']:+.1%}" if settlement["clv"] is not None else "-"

        t.add_row(
            f"{home_name_display[:10]} v {away_name_display[:10]}",
            f"{market} {selection}",
            f"{score_home}-{score_away}",
            f"[{result_color}]{settlement['result'].upper()}[/{result_color}]",
            f"[{result_color}]{settlement['pnl']:+.2f}[/{result_color}]",
            clv_str,
        )

    # Update bot bankrolls
    for bot_id, data in by_bot.items():
        client.table("bots").update({
            "current_bankroll": data["bankroll"]
        }).eq("id", bot_id).execute()

    console.print(t)

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None
    wins = sum(1 for b in pending[:settled] if b.get("result") == "pending")

    console.print(f"\n[bold]Settlement complete:[/bold]")
    console.print(f"  Settled: {settled} | Skipped (no result): {skipped}")
    console.print(f"  Total P&L: [{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.2f}[/]")
    if avg_clv is not None:
        clv_color = "green" if avg_clv > 0 else "red"
        console.print(f"  Avg CLV: [{clv_color}]{avg_clv:+.1%}[/] ({'beating' if avg_clv > 0 else 'behind'} closing line)")


def run_report():
    """Show cumulative P&L and CLV across all settled bets"""
    client = get_client()
    console.print("[bold]═══ OddsIntel P&L Report ═══[/bold]\n")

    bots = client.table("bots").select("id, name, starting_bankroll, current_bankroll").execute().data

    t = Table(title="Bot Performance")
    t.add_column("Bot", style="cyan")
    t.add_column("Bets", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Hit %", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("Bankroll", justify="right")

    for bot in bots:
        bets = client.table("simulated_bets").select(
            "result, pnl, stake, clv"
        ).eq("bot_id", bot["id"]).neq("result", "pending").execute().data

        if not bets:
            continue

        total = len(bets)
        won = sum(1 for b in bets if b["result"] == "won")
        total_stake = sum(b["stake"] for b in bets)
        total_pnl = sum(b["pnl"] or 0 for b in bets)
        roi = total_pnl / total_stake if total_stake > 0 else 0
        clv_vals = [b["clv"] for b in bets if b.get("clv") is not None]
        avg_clv = sum(clv_vals) / len(clv_vals) if clv_vals else None

        roi_color = "green" if roi > 0 else "red"
        clv_str = f"{avg_clv:+.1%}" if avg_clv is not None else "-"

        t.add_row(
            bot["name"],
            str(total),
            str(won),
            f"{won/total:.1%}" if total else "-",
            f"[{roi_color}]{roi:+.1%}[/]",
            f"[{roi_color}]{total_pnl:+.2f}[/]",
            clv_str,
            f"{bot['current_bankroll']:.2f}",
        )

    console.print(t)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Show P&L report")
    args = parser.parse_args()

    if args.report:
        run_report()
    else:
        run_settlement()
