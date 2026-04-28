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

from workers.api_clients.api_football import get_results_for_settlement as get_api_football_results
from workers.scrapers.espn_results import get_finished_matches_espn
from workers.scrapers.flashscore import _get_matches_alternative as get_sofascore_results
from workers.api_clients.supabase_client import (
    get_client,
    store_team_elo,
    store_team_form,
    store_model_evaluation,
    compute_team_form_from_db,
    store_match_stats_full,
    store_match_events_af,
    store_match_player_stats,
)

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


# ─── Post-match enrichment (T4, T8, T12) ─────────────────────────────────────

def fetch_post_match_enrichment(client) -> dict:
    """
    T4: Half-time stats, T8: Match events, T12: Player stats.
    Runs after settlement for all recently finished matches.
    Returns counts dict.
    """
    from workers.api_clients.api_football import (
        get_fixture_statistics, parse_fixture_stats,
        get_fixture_statistics_halftime, parse_fixture_stats_halftime,
        get_fixture_events, parse_fixture_events,
        get_fixture_players, parse_fixture_players,
    )

    counts = {"stats": 0, "halftime": 0, "events": 0, "players": 0}

    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get recently finished matches with AF IDs and team IDs
    db_finished = client.table("matches").select(
        "id, api_football_id, home_team_api_id, away_team_api_id"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

    if not db_finished:
        return counts

    # Which matches already have stats
    match_ids_with_stats = set()
    for m in db_finished:
        existing = client.table("match_stats").select("match_id").eq(
            "match_id", m["id"]
        ).execute()
        if existing.data:
            match_ids_with_stats.add(m["id"])

    for match in db_finished:
        af_id = match.get("api_football_id")
        if not af_id:
            continue

        match_id = match["id"]
        home_api_id = match.get("home_team_api_id")

        # ── T4 + Full stats: fetch full-match stats + half-time ────────────
        try:
            raw_full = get_fixture_statistics(af_id)
            full_stats = parse_fixture_stats(raw_full)

            # Fetch half-time stats (T4)
            ht_response = get_fixture_statistics_halftime(af_id)
            ht_stats = parse_fixture_stats_halftime(ht_response)

            merged_stats = {**full_stats, **ht_stats}

            if merged_stats:
                store_match_stats_full(match_id, merged_stats)
                counts["stats"] += 1
                if ht_stats:
                    counts["halftime"] += 1
        except Exception as e:
            console.print(f"    [yellow]Stats error for fixture {af_id}: {e}[/yellow]")

        # ── T8: Match events ──────────────────────────────────────────────
        try:
            raw_events = get_fixture_events(af_id)
            parsed_events = parse_fixture_events(raw_events)
            if parsed_events:
                stored = store_match_events_af(match_id, parsed_events,
                                               home_team_api_id=home_api_id)
                counts["events"] += stored
        except Exception as e:
            console.print(f"    [yellow]Events error for fixture {af_id}: {e}[/yellow]")

        # ── T12: Player stats ─────────────────────────────────────────────
        try:
            raw_players = get_fixture_players(af_id)
            parsed_players = parse_fixture_players(raw_players,
                                                    home_team_api_id=home_api_id)
            if parsed_players:
                stored = store_match_player_stats(match_id, af_id, parsed_players)
                counts["players"] += stored
        except Exception as e:
            console.print(f"    [yellow]Player stats error for fixture {af_id}: {e}[/yellow]")

    return counts


# ─── Main settlement ──────────────────────────────────────────────────────────

def run_settlement():
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    console.print(f"[bold green]═══ OddsIntel Settlement: {today} ═══[/bold green]\n")

    client = get_client()

    # 1. Get pending bets with match info (may be empty — that's fine)
    console.print("[cyan]Loading pending bets...[/cyan]")
    bets_result = client.table("simulated_bets").select(
        "*, matches(id, date, score_home, score_away, result, status, "
        "home_team:home_team_id(name), away_team:away_team_id(name))"
    ).eq("result", "pending").execute()

    pending = bets_result.data
    console.print(f"  {len(pending)} pending bets")

    # 2. Determine which dates to fetch results for.
    # Always include today + yesterday to catch late finishes.
    # Also include any dates that have pending bets.
    fetch_dates = {today, yesterday}
    for bet in pending:
        match_info = bet.get("matches", {})
        if match_info and match_info.get("date"):
            fetch_dates.add(match_info["date"][:10])

    # 2a. API-Football as primary source (paid, reliable, 1236 leagues)
    console.print(f"\n[cyan]Fetching results from API-Football for {len(fetch_dates)} date(s)...[/cyan]")
    finished = []
    try:
        for d in sorted(fetch_dates):
            af_results = get_api_football_results(d)
            console.print(f"  {d}: {len(af_results)} finished matches from API-Football")
            finished.extend(af_results)
    except Exception as e:
        console.print(f"  [yellow]API-Football error: {e}[/yellow]")

    # 2b. ESPN as backup (free, no auth)
    if len(finished) < 10:
        console.print("[cyan]Trying ESPN as backup...[/cyan]")
        for d in sorted(fetch_dates):
            espn_results = get_finished_matches_espn(d)
            day_finished = [r for r in espn_results
                            if r.get("status") == "FT"
                            and r.get("home_goals") is not None]
            if day_finished:
                console.print(f"  {d}: {len(day_finished)} from ESPN")
                finished.extend(day_finished)

    # 2c. Sofascore as last resort (free, fragile)
    if len(finished) < 10:
        console.print("[cyan]Trying Sofascore as last resort...[/cyan]")
        try:
            for d in sorted(fetch_dates):
                sofascore_results = get_sofascore_results(d)
                sofascore_finished = [r for r in sofascore_results
                                      if r.get("status") == "FT"
                                      and r.get("home_goals") is not None]
                if sofascore_finished:
                    console.print(f"  {d}: {len(sofascore_finished)} from Sofascore")
                    finished.extend(sofascore_finished)
        except Exception as e:
            console.print(f"  [yellow]Sofascore error: {e}[/yellow]")

    console.print(f"  [bold]{len(finished)} total finished matches[/bold]")

    if not finished:
        console.print("[yellow]No finished matches found from any source. Try again later.[/yellow]")
        return

    # 3. Update ALL match results in DB — not just bet matches.
    # Match by api_football_id (direct, reliable) with team-name fallback.
    # This gives us a complete labeled dataset for every match we tracked,
    # regardless of whether any bot placed a bet on it.
    console.print("\n[cyan]Updating all match results in Supabase...[/cyan]")
    db_updated = 0
    db_skipped = 0

    # Build lookup: api_football_id -> result row
    af_id_to_result = {
        int(r["api_football_id"]): r
        for r in finished
        if r.get("api_football_id") and r.get("home_goals") is not None
    }

    # Fetch all DB matches for the fetch window (today + yesterday + bet dates)
    date_min = min(fetch_dates)
    date_max = max(fetch_dates)
    db_matches = client.table("matches").select(
        "id, api_football_id, home_team_id, away_team_id, status"
    ).gte("date", f"{date_min}T00:00:00").lte(
        "date", f"{date_max}T23:59:59"
    ).execute().data

    for db_match in db_matches:
        if db_match.get("status") == "finished":
            continue  # already settled

        result_row = None

        # Primary: match by api_football_id
        af_id = db_match.get("api_football_id")
        if af_id and int(af_id) in af_id_to_result:
            result_row = af_id_to_result[int(af_id)]

        # Fallback: team name lookup (for Sofascore/ESPN-sourced results)
        if not result_row:
            home_r = client.table("teams").select("name").eq(
                "id", db_match["home_team_id"]
            ).execute()
            away_r = client.table("teams").select("name").eq(
                "id", db_match["away_team_id"]
            ).execute()
            if home_r.data and away_r.data:
                result_row = find_result_for_match(
                    home_r.data[0]["name"], away_r.data[0]["name"], finished
                )

        if not result_row:
            db_skipped += 1
            continue

        hg = int(result_row["home_goals"])
        ag = int(result_row["away_goals"])
        result_str = "home" if hg > ag else "away" if ag > hg else "draw"
        client.table("matches").update({
            "score_home": hg, "score_away": ag,
            "result": result_str, "status": "finished",
        }).eq("id", db_match["id"]).execute()
        db_updated += 1

    console.print(f"  {db_updated} matches updated | {db_skipped} no result found yet")

    # 4. Settle each bet (skip gracefully if none pending)
    if not pending:
        console.print("\n[yellow]No pending bets to settle — skipping bet settlement.[/yellow]")
    else:
        _settle_pending_bets(client, pending, finished)

    # Post-match enrichment and analytics always run (not gated on bets)

    # P1.3: Update ELO ratings for all finished matches
    console.print("\n[cyan]Updating ELO ratings...[/cyan]")
    try:
        elo_count = update_elo_ratings(client)
        console.print(f"  {elo_count} team ratings updated")
    except Exception as e:
        console.print(f"  [yellow]ELO update error: {e}[/yellow]")

    # P1.4: Aggregate model evaluations
    console.print("[cyan]Computing model evaluations...[/cyan]")
    try:
        eval_count = compute_model_evaluations(client)
        console.print(f"  {eval_count} evaluation records stored")
    except Exception as e:
        console.print(f"  [yellow]Model evaluation error: {e}[/yellow]")

    # P1.5: Update form cache for teams that played
    console.print("[cyan]Updating team form cache...[/cyan]")
    try:
        form_count = update_team_form_cache(client)
        console.print(f"  {form_count} team forms updated")
    except Exception as e:
        console.print(f"  [yellow]Form cache error: {e}[/yellow]")

    # T4/T8/T12: Post-match enrichment (stats, half-time, events, player stats)
    console.print("[cyan]Fetching post-match enrichment (T4/T8/T12)...[/cyan]")
    try:
        enrichment_counts = fetch_post_match_enrichment(client)
        console.print(
            f"  {enrichment_counts['stats']} match stats | "
            f"{enrichment_counts['halftime']} with half-time | "
            f"{enrichment_counts['events']} events | "
            f"{enrichment_counts['players']} player stat rows"
        )
    except Exception as e:
        console.print(f"  [yellow]Post-match enrichment error: {e}[/yellow]")

    # 11.4: Daily post-mortem LLM analysis (only if bets were settled)
    if pending:
        console.print("\n[cyan]Running AI post-mortem analysis...[/cyan]")
        try:
            run_post_mortem(client)
        except Exception as e:
            console.print(f"  [yellow]Post-mortem error (non-critical): {e}[/yellow]")


def _settle_pending_bets(client, pending: list, finished: list):
    """Settle all pending bets against finished match results."""
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

    return settled


def update_elo_ratings(client):
    """
    P1.3: Update ELO ratings for teams in recently finished matches.
    Simple ELO with K=30, home advantage +100, goal diff multiplier.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get yesterday's and today's finished matches with team IDs
    finished = client.table("matches").select(
        "id, home_team_id, away_team_id, score_home, score_away"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

    if not finished:
        return 0

    # Load current ELO ratings for involved teams
    team_ids = set()
    for m in finished:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    elo_cache: dict[str, float] = {}
    for tid in team_ids:
        result = client.table("team_elo_daily").select(
            "elo_rating"
        ).eq("team_id", tid).order("date", desc=True).limit(1).execute()
        elo_cache[tid] = float(result.data[0]["elo_rating"]) if result.data else 1500.0

    K = 30
    HOME_ADV = 100
    updated = 0

    for m in finished:
        if m["score_home"] is None or m["score_away"] is None:
            continue

        h_id = m["home_team_id"]
        a_id = m["away_team_id"]
        h_elo = elo_cache.get(h_id, 1500.0) + HOME_ADV
        a_elo = elo_cache.get(a_id, 1500.0)

        # Expected scores
        exp_h = 1 / (1 + 10 ** ((a_elo - h_elo) / 400))
        exp_a = 1 - exp_h

        # Actual scores
        gd = abs(m["score_home"] - m["score_away"])
        gd_mult = max(1.0, (gd + 1) ** 0.5)  # goal diff multiplier

        if m["score_home"] > m["score_away"]:
            actual_h, actual_a = 1.0, 0.0
        elif m["score_home"] < m["score_away"]:
            actual_h, actual_a = 0.0, 1.0
        else:
            actual_h, actual_a = 0.5, 0.5

        # Update (remove home advantage from stored rating)
        new_h = (elo_cache.get(h_id, 1500.0) +
                 K * gd_mult * (actual_h - exp_h))
        new_a = (elo_cache.get(a_id, 1500.0) +
                 K * gd_mult * (actual_a - exp_a))

        elo_cache[h_id] = new_h
        elo_cache[a_id] = new_a

        try:
            store_team_elo(h_id, today_str, new_h)
            store_team_elo(a_id, today_str, new_a)
            updated += 2
        except Exception:
            pass

    return updated


def update_team_form_cache(client):
    """
    P1.5: Update form cache for teams that played recently.
    Computes rolling 10-match form from DB and stores in team_form_cache.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    today_str = date.today().isoformat()

    # Get yesterday's and today's finished matches
    finished = client.table("matches").select(
        "home_team_id, away_team_id"
    ).eq("status", "finished").gte(
        "date", f"{yesterday_str}T00:00:00"
    ).lte("date", f"{today_str}T23:59:59").execute().data

    if not finished:
        return 0

    team_ids = set()
    for m in finished:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    updated = 0
    for tid in team_ids:
        form = compute_team_form_from_db(tid, today_str)
        if form:
            try:
                store_team_form(tid, today_str, form)
                updated += 1
            except Exception:
                pass

    return updated


def compute_model_evaluations(client):
    """
    P1.4: Aggregate settled bets into model_evaluations by date/market.
    Runs after all bets are settled for the day.
    """
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # Get recently settled bets with league info
    bets = client.table("simulated_bets").select(
        "id, market, result, pnl, stake, clv, "
        "match:match_id(league_id)"
    ).neq("result", "pending").gte(
        "pick_time", f"{yesterday_str}T00:00:00"
    ).execute().data

    if not bets:
        return 0

    # Group by market
    from collections import defaultdict
    by_market: dict[str, list] = defaultdict(list)
    for b in bets:
        by_market[b["market"]].append(b)

    evals_stored = 0
    for market, market_bets in by_market.items():
        total = len(market_bets)
        hits = sum(1 for b in market_bets if b["result"] == "won")
        total_stake = sum(b["stake"] for b in market_bets)
        total_pnl = sum(b["pnl"] or 0 for b in market_bets)
        roi = (total_pnl / total_stake * 100) if total_stake > 0 else 0
        clv_vals = [b["clv"] for b in market_bets if b.get("clv") is not None]
        avg_clv = sum(clv_vals) / len(clv_vals) if clv_vals else None

        try:
            store_model_evaluation(
                eval_date=today_str,
                league_id=None,  # aggregate across all leagues
                market=market,
                total_bets=total,
                hits=hits,
                roi=roi,
                avg_clv=avg_clv,
                notes=f"Auto-generated from {total} settled bets",
            )
            evals_stored += 1
        except Exception:
            pass

    return evals_stored


def run_post_mortem(client):
    """
    11.4: Daily AI post-mortem analysis.
    After settlement, sends today's settled bets to Gemini for loss classification.
    Classifies each loss as: Variance, Information Gap, Model Error, or Timing.
    Stores classification in model_evaluations.notes for pattern tracking.

    Cost: ~$0.01-0.02/day (one Gemini call with batch context).
    See MODEL_ANALYSIS.md Section 11.4.
    """
    import json
    import re

    today_str = date.today().isoformat()

    # Get today's settled bets with full context
    bets = client.table("simulated_bets").select(
        "id, market, selection, odds_at_pick, model_probability, edge_percent, "
        "result, pnl, stake, clv, calibrated_prob, alignment_class, kelly_fraction, "
        "odds_drift, news_impact_score, reasoning, "
        "matches(score_home, score_away, "
        "home_team:home_team_id(name), away_team:away_team_id(name), "
        "leagues(name, country, tier))"
    ).neq("result", "pending").gte(
        "pick_time", f"{today_str}T00:00:00"
    ).execute().data

    if not bets:
        return

    # Also get match stats if available
    losses = [b for b in bets if b["result"] == "lost"]
    wins = [b for b in bets if b["result"] == "won"]

    if not losses:
        console.print("  [green]No losses today — no post-mortem needed![/green]")
        return

    # Build context for LLM
    bet_summaries = []
    for b in bets:
        match = b.get("matches", {})
        home = match.get("home_team", [{}])
        away = match.get("away_team", [{}])
        league = match.get("leagues", [{}])
        home_name = home[0]["name"] if isinstance(home, list) else home.get("name", "?")
        away_name = away[0]["name"] if isinstance(away, list) else away.get("name", "?")
        league_name = league[0]["name"] if isinstance(league, list) else league.get("name", "?")
        tier = league[0]["tier"] if isinstance(league, list) else league.get("tier", "?")

        summary = (
            f"{'✗ LOST' if b['result'] == 'lost' else '✓ WON'}: "
            f"{home_name} vs {away_name} ({league_name}, T{tier}) "
            f"| Score: {match.get('score_home', '?')}-{match.get('score_away', '?')} "
            f"| Bet: {b['market']} {b['selection']} @{b['odds_at_pick']:.2f} "
            f"| Model prob: {b['model_probability']:.1%}"
        )
        if b.get("calibrated_prob"):
            summary += f", Cal: {b['calibrated_prob']:.1%}"
        if b.get("odds_drift") and b["odds_drift"] != 0:
            summary += f", Drift: {b['odds_drift']:+.3f}"
        if b.get("clv") is not None:
            summary += f", CLV: {b['clv']:+.1%}"
        if b.get("news_impact_score") and b["news_impact_score"] != 0:
            summary += f", News: {b['news_impact_score']:+.2f}"
        if b.get("alignment_class"):
            summary += f", Align: {b['alignment_class']}"
        bet_summaries.append(summary)

    prompt = f"""You are a sports betting analyst performing a daily post-mortem.

TODAY'S SETTLED BETS ({len(bets)} total: {len(wins)} won, {len(losses)} lost):

{chr(10).join(bet_summaries)}

For each LOST bet, classify the likely cause into exactly one category:
- VARIANCE: Model assessment was reasonable (good edge, maybe good CLV) but result went against us. Bad luck, not a model flaw.
- INFORMATION_GAP: Odds moved against us (negative drift) or news impacted the match in a way our model didn't capture. We were missing information.
- MODEL_ERROR: Model probability was significantly wrong — the team was simply not as strong/weak as predicted. The pick was bad, not unlucky.
- TIMING: The pick might have been right earlier but conditions changed (lineup, late injury). Better timing would have helped.

Also provide:
1. A one-paragraph overall assessment of today's performance
2. Any patterns you notice (e.g., "all losses were in Tier 1", "negative CLV on every loss")
3. One specific actionable suggestion for improving tomorrow

Respond with ONLY a JSON object:
{{
  "loss_classifications": [
    {{"match": "Home vs Away", "category": "VARIANCE|INFORMATION_GAP|MODEL_ERROR|TIMING", "reason": "brief explanation"}}
  ],
  "daily_summary": "one paragraph",
  "patterns_noticed": ["pattern 1", "pattern 2"],
  "suggestion": "one specific action"
}}"""

    try:
        from google import genai
        gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()

        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            analysis = json.loads(json_match.group())

            # Display results
            console.print(f"\n  [bold]Post-Mortem ({len(losses)} losses analyzed):[/bold]")

            for lc in analysis.get("loss_classifications", []):
                cat_color = {
                    "VARIANCE": "blue",
                    "INFORMATION_GAP": "yellow",
                    "MODEL_ERROR": "red",
                    "TIMING": "magenta",
                }.get(lc.get("category", ""), "white")
                console.print(f"  [{cat_color}]{lc.get('category', '?'):18s}[/{cat_color}] {lc.get('match', '?')} — {lc.get('reason', '')}")

            console.print(f"\n  [bold]Summary:[/bold] {analysis.get('daily_summary', 'N/A')}")

            patterns = analysis.get("patterns_noticed", [])
            if patterns:
                console.print(f"  [bold]Patterns:[/bold]")
                for p in patterns:
                    console.print(f"    • {p}")

            suggestion = analysis.get("suggestion", "")
            if suggestion:
                console.print(f"  [bold]Suggestion:[/bold] {suggestion}")

            # Store in model_evaluations
            try:
                store_model_evaluation(
                    eval_date=today_str,
                    league_id=None,
                    market="post_mortem",
                    total_bets=len(bets),
                    hits=len(wins),
                    roi=sum(b["pnl"] or 0 for b in bets) / max(sum(b["stake"] for b in bets), 1) * 100,
                    avg_clv=None,
                    notes=json.dumps(analysis, ensure_ascii=False)[:2000],
                )
            except Exception:
                pass

    except Exception as e:
        console.print(f"  [yellow]Post-mortem LLM error: {e}[/yellow]")


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
