"""
OddsIntel — AI Pre-Match News Checker
Runs after the morning pipeline places bets. For each match with a pending bet,
fetches recent team news from Sofascore and asks Gemini whether anything
should change the prediction.

This covers the information gap that pure stats cannot:
  - Key player injured or suspended
  - Manager sacked / tactical change
  - Team coming off a midweek European game (fatigue)
  - Weather extremes

Output: updates simulated_bets.reasoning with AI flags, logs a daily report.

Usage:
  python news_checker.py            # Run after morning pipeline
  python news_checker.py --test     # Dry run, don't update DB
"""

import sys
import os
import json
import time
import requests
import argparse
from pathlib import Path
from datetime import datetime, timezone, date
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import google.generativeai as genai
from workers.api_clients.supabase_client import get_client

console = Console()

genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))
GEMINI_MODEL = "gemini-2.5-flash-preview-04-17"

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}


# ─── Sofascore news fetchers ────────────────────────────────────────────────

def fetch_team_news(sofascore_team_id: int) -> list[str]:
    """
    Fetch recent news/form for a team from Sofascore.
    Returns list of plain-text facts (last 3 results, missing players).
    """
    facts = []

    # Last 5 results
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/team/{sofascore_team_id}/events/last/0",
            headers=SOFASCORE_HEADERS, timeout=10,
        )
        if resp.status_code == 200:
            events = resp.json().get("events", [])[:5]
            for e in events:
                home = e.get("homeTeam", {}).get("name", "?")
                away = e.get("awayTeam", {}).get("name", "?")
                hs = e.get("homeScore", {}).get("current", "?")
                as_ = e.get("awayScore", {}).get("current", "?")
                facts.append(f"Recent: {home} {hs}-{as_} {away}")
    except Exception:
        pass

    # Missing players (injuries/suspensions)
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/team/{sofascore_team_id}/players/missing",
            headers=SOFASCORE_HEADERS, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            missing = data.get("missingPlayers", [])
            for p in missing[:5]:
                player = p.get("player", {}).get("name", "Unknown")
                reason = p.get("type", "out")
                position = p.get("player", {}).get("position", "")
                facts.append(f"Missing: {player} ({position}) — {reason}")
    except Exception:
        pass

    time.sleep(0.5)
    return facts


def fetch_match_lineups(sofascore_event_id: int) -> dict:
    """
    Fetch confirmed lineups if available (usually 1h before kickoff).
    Returns {'home_lineup_confirmed': bool, 'home_missing_key': bool, ...}
    """
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/event/{sofascore_event_id}/lineups",
            headers=SOFASCORE_HEADERS, timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            home_lineup = data.get("home", {})
            away_lineup = data.get("away", {})
            return {
                "home_confirmed": bool(home_lineup.get("players")),
                "away_confirmed": bool(away_lineup.get("players")),
                "home_players": [p.get("player", {}).get("name") for p in home_lineup.get("players", [])[:11]],
                "away_players": [p.get("player", {}).get("name") for p in away_lineup.get("players", [])[:11]],
            }
    except Exception:
        pass
    return {}


# ─── Gemini analysis ────────────────────────────────────────────────────────

def analyse_with_gemini(match_context: dict) -> dict:
    """
    Send match context to Gemini and get a confidence adjustment.

    Returns:
        {
            "flag": "ok" | "warning" | "skip",
            "reason": str,
            "confidence_adjustment": float,  # -0.15 to +0.05
            "tokens_used": int
        }
    """
    prompt = f"""You are a football betting analyst. Evaluate whether a pre-match prediction should still be trusted given the latest team news.

MATCH: {match_context['home_team']} vs {match_context['away_team']}
LEAGUE: {match_context['league']} (Tier {match_context['tier']})
KICKOFF: {match_context['kickoff']}

OUR BET: {match_context['market']} — {match_context['selection']} @ {match_context['odds']:.2f}
Model probability: {match_context['model_prob']:.1%}
Implied probability: {match_context['implied_prob']:.1%}
Edge: {match_context['edge']:.1%}

HOME TEAM INTEL:
{chr(10).join(match_context.get('home_facts', ['No data available']))}

AWAY TEAM INTEL:
{chr(10).join(match_context.get('away_facts', ['No data available']))}

{f"CONFIRMED LINEUPS: Home: {', '.join(match_context['lineups'].get('home_players', []))[:100]} | Away: {', '.join(match_context['lineups'].get('away_players', []))[:100]}" if match_context.get('lineups', {}).get('home_confirmed') else "Lineups: Not confirmed yet"}

Based on this information, respond with ONLY a JSON object:
{{
  "flag": "ok" or "warning" or "skip",
  "reason": "one sentence explaining your assessment",
  "confidence_adjustment": a number between -0.15 and 0.05
}}

Rules:
- "ok": news is neutral or positive for our bet, no concerns
- "warning": something worth noting but not a dealbreaker (e.g. one rotation player missing)
- "skip": key information that invalidates the bet (e.g. star striker absent for an Over bet, or key defender out for an Away win bet)
- confidence_adjustment: how much to shift the model probability (negative = less confident)

Only flag real concerns. Don't invent problems if data is sparse."""

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        text = response.text.strip()

        # Extract JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            result["tokens_used"] = getattr(response.usage_metadata, "total_token_count", 0) if hasattr(response, "usage_metadata") else 0
            return result

    except Exception as e:
        console.print(f"  [yellow]Gemini error: {e}[/yellow]")

    return {"flag": "ok", "reason": "AI check unavailable", "confidence_adjustment": 0, "tokens_used": 0}


# ─── Main ────────────────────────────────────────────────────────────────────

def run_news_checker(dry_run: bool = False):
    today = date.today().isoformat()
    console.print(f"[bold cyan]═══ OddsIntel AI News Checker: {today} ═══[/bold cyan]\n")

    client = get_client()

    # Get today's pending bets with match context
    result = client.table("simulated_bets").select(
        "id, market, selection, odds_at_pick, model_probability, edge_percent, reasoning, "
        "matches(id, date, sofascore_event_id, "
        "home_team:home_team_id(name), away_team:away_team_id(name), "
        "leagues(name, country, tier))"
    ).eq("result", "pending").gte(
        "created_at", f"{today}T00:00:00"
    ).execute()

    bets = result.data
    console.print(f"Found {len(bets)} pending bets to check today\n")

    if not bets:
        console.print("[yellow]No pending bets today.[/yellow]")
        return

    total_tokens = 0
    results_table = []

    # Deduplicate: one AI check per match, not per bet
    checked_matches: dict[str, dict] = {}

    for bet in bets:
        match = bet.get("matches", {})
        if not match:
            continue

        match_id = match["id"]
        sofascore_id = match.get("sofascore_event_id")

        home_team_data = match.get("home_team", {})
        away_team_data = match.get("away_team", {})
        league_data = match.get("leagues", {})

        home_team = (home_team_data[0]["name"] if isinstance(home_team_data, list) else home_team_data.get("name", "?"))
        away_team = (away_team_data[0]["name"] if isinstance(away_team_data, list) else away_team_data.get("name", "?"))
        league = (league_data[0]["name"] if isinstance(league_data, list) else league_data.get("name", "?"))
        tier = (league_data[0]["tier"] if isinstance(league_data, list) else league_data.get("tier", 1))

        if match_id not in checked_matches:
            console.print(f"[cyan]Checking: {home_team} vs {away_team}...[/cyan]")

            # Fetch team news from Sofascore
            # We don't have team IDs stored yet, but we can look up by event
            home_facts = []
            away_facts = []
            lineups = {}

            if sofascore_id:
                lineups = fetch_match_lineups(sofascore_id)

            # Get teams from Sofascore by searching event
            if sofascore_id:
                try:
                    resp = requests.get(
                        f"https://api.sofascore.com/api/v1/event/{sofascore_id}",
                        headers=SOFASCORE_HEADERS, timeout=10,
                    )
                    if resp.status_code == 200:
                        event_data = resp.json().get("event", {})
                        home_id = event_data.get("homeTeam", {}).get("id")
                        away_id = event_data.get("awayTeam", {}).get("id")

                        if home_id:
                            home_facts = fetch_team_news(home_id)
                        if away_id:
                            away_facts = fetch_team_news(away_id)
                except Exception as e:
                    console.print(f"  [yellow]Event lookup error: {e}[/yellow]")

            checked_matches[match_id] = {
                "home_team": home_team,
                "away_team": away_team,
                "league": league,
                "tier": tier,
                "kickoff": match.get("date", ""),
                "home_facts": home_facts,
                "away_facts": away_facts,
                "lineups": lineups,
            }

        match_context = {
            **checked_matches[match_id],
            "market": bet["market"],
            "selection": bet["selection"],
            "odds": bet["odds_at_pick"],
            "model_prob": bet["model_probability"],
            "implied_prob": 1 / bet["odds_at_pick"] if bet["odds_at_pick"] > 0 else 0,
            "edge": bet.get("edge_percent", 0),
        }

        # Ask Gemini
        ai_result = analyse_with_gemini(match_context)
        total_tokens += ai_result.get("tokens_used", 0)

        flag = ai_result.get("flag", "ok")
        reason = ai_result.get("reason", "")
        adj = ai_result.get("confidence_adjustment", 0)

        flag_display = {
            "ok": "[green]✓ OK[/green]",
            "warning": "[yellow]⚠ WARNING[/yellow]",
            "skip": "[red]✗ SKIP[/red]",
        }.get(flag, flag)

        console.print(f"  {flag_display} — {reason}")

        results_table.append({
            "match": f"{home_team[:12]} v {away_team[:12]}",
            "bet": f"{bet['market']} {bet['selection']}",
            "flag": flag,
            "reason": reason[:60],
            "adj": adj,
        })

        # Update bet reasoning in DB
        if not dry_run:
            existing_reasoning = bet.get("reasoning", "") or ""
            ai_note = f" | AI [{flag.upper()}]: {reason}"
            if adj != 0:
                ai_note += f" (confidence adj: {adj:+.1%})"

            updated_prob = max(0.05, min(0.95, bet["model_probability"] + adj))

            client.table("simulated_bets").update({
                "reasoning": existing_reasoning + ai_note,
                "model_probability": updated_prob,
                "news_triggered": flag in ("warning", "skip"),
            }).eq("id", bet["id"]).execute()

    # Summary table
    console.print()
    t = Table(title="AI News Check Summary")
    t.add_column("Match", style="cyan")
    t.add_column("Bet")
    t.add_column("Flag")
    t.add_column("Reason")
    t.add_column("Adj", justify="right")

    for r in results_table:
        flag_cell = {"ok": "[green]OK[/green]", "warning": "[yellow]WARN[/yellow]", "skip": "[red]SKIP[/red]"}.get(r["flag"], r["flag"])
        t.add_row(r["match"], r["bet"], flag_cell, r["reason"], f"{r['adj']:+.1%}" if r["adj"] != 0 else "-")

    console.print(t)

    skips = sum(1 for r in results_table if r["flag"] == "skip")
    warnings = sum(1 for r in results_table if r["flag"] == "warning")
    console.print(f"\n[bold]Result:[/bold] {len(results_table)} bets checked | {skips} flagged SKIP | {warnings} WARNING | ~{total_tokens} tokens used")
    console.print(f"[dim]Estimated cost: ${total_tokens * 0.000001:.4f} (gemini-2.5-flash)[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Dry run, don't update DB")
    args = parser.parse_args()
    run_news_checker(dry_run=args.test)
