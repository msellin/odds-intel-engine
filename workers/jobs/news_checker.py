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

from google import genai
from workers.api_clients.supabase_client import get_client, store_prediction_snapshot, store_match_signal

console = Console()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
GEMINI_MODEL = "gemini-2.5-flash"

# ─── Team news fetchers (Sofascore removed — uses DB injuries only now) ─────

def fetch_team_news(team_id: int) -> list[str]:
    """Stub — Sofascore removed. Returns empty list. Injuries come from AF/DB now."""
    return []


def fetch_match_lineups(event_id: int) -> dict:
    """Stub — Sofascore removed. Lineups come from AF/DB now."""
    return {}


# ─── Gemini analysis ────────────────────────────────────────────────────────

def analyse_with_gemini(match_context: dict) -> dict:
    """
    Send match context to Gemini and get structured impact assessment.

    Focuses on qualitative signals only — injuries/suspensions are already
    covered by structured API-Football data processed each morning.

    Returns structured output with:
      - Qualitative flag (ok / warning / skip)
      - Net team impact for home and away (driven by qualitative factors)
      - Lineup confidence from confirmed lineups
      - Overall confidence adjustment

    This is the v3 prompt (qualitative focus, no injury hunting).
    See MODEL_ANALYSIS.md Section 11.1 for design rationale.
    """
    prompt = f"""You are a football betting analyst. Assess how qualitative pre-match news affects a specific bet.

MATCH: {match_context['home_team']} vs {match_context['away_team']}
LEAGUE: {match_context['league']} (Tier {match_context['tier']})
KICKOFF: {match_context['kickoff']}

OUR BET: {match_context['market']} — {match_context['selection']} @ {match_context['odds']:.2f}
Model probability: {match_context['model_prob']:.1%}
Implied probability: {match_context['implied_prob']:.1%}
Edge: {match_context['edge']:.1%}

HOME TEAM RECENT FORM:
{chr(10).join(match_context.get('home_facts', ['No data available']))}

AWAY TEAM RECENT FORM:
{chr(10).join(match_context.get('away_facts', ['No data available']))}

{f"CONFIRMED LINEUPS: Home: {', '.join(match_context['lineups'].get('home_players', []))[:200]} | Away: {', '.join(match_context['lineups'].get('away_players', []))[:200]}" if match_context.get('lineups', {}).get('home_confirmed') else "LINEUPS: Not confirmed yet"}

IMPORTANT: Injury and suspension data is already handled by our structured data pipeline.
DO NOT flag injuries or standard suspensions — focus ONLY on the following qualitative signals:
  1. Manager sacked or changed since last match
  2. Red card in the previous match forcing a key player suspension today
  3. Severe weather forecast (heavy rain, snow, extreme wind) affecting play style
  4. Fan/stadium issues (crowd bans, neutral venue, high-pressure derby atmosphere)
  5. Unexpected tactical shift or publicly announced formation change
  6. Mid-week European fixture fatigue (team played 3 days ago in a cup/European game)

Respond with ONLY a JSON object. No other text.

{{
  "flag": "ok" or "warning" or "skip",
  "reason": "one sentence summary of the most significant qualitative signal found, or 'No notable qualitative signals' (<60 words)",
  "confidence_adjustment": float between -0.15 and +0.05,
  "players_out": [],
  "players_doubtful": [],
  "players_returning": [],
  "lineup_confidence": float 0.0 to 1.0,
  "home_net_impact": float -1.0 to +1.0,
  "away_net_impact": float -1.0 to +1.0
}}

RULES for confidence_adjustment:
- Manager change or tactical overhaul: -0.05 to -0.10
- Severe weather affecting play (e.g. heavy wind for Over/Under): -0.05 to -0.10
- European fatigue (played 3 days ago): -0.03 to -0.07
- Derby/high-pressure atmosphere favouring home side: +0.02 to +0.05
- No notable signal: 0.0

RULES for lineup_confidence:
- Confirmed XI available: 1.0
- Most expected starters known, 1-2 doubts: 0.7-0.9
- Significant uncertainty (3+ unknowns): 0.4-0.6
- No lineup info at all: 0.5 (neutral)

RULES for flag:
- "ok": no notable qualitative signal, or signal neutral for our bet
- "warning": notable qualitative concern but edge may still exist
- "skip": signal that likely invalidates the bet (e.g. manager sacked + unknown tactics for a 1X2 bet)

Always set players_out, players_doubtful, players_returning to empty arrays.
If no qualitative signal is found, set flag "ok", confidence_adjustment 0.0, impacts 0.0. Do NOT invent problems."""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()

        # Extract JSON (may be wrapped in markdown code block)
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            result["tokens_used"] = getattr(response.usage_metadata, "total_token_count", 0) if hasattr(response, "usage_metadata") else 0

            # Ensure required fields have defaults
            result.setdefault("flag", "ok")
            result.setdefault("reason", "")
            result.setdefault("confidence_adjustment", 0)
            result.setdefault("players_out", [])
            result.setdefault("players_doubtful", [])
            result.setdefault("players_returning", [])
            result.setdefault("lineup_confidence", 0.5)
            result.setdefault("home_net_impact", 0.0)
            result.setdefault("away_net_impact", 0.0)

            # Clamp values to valid ranges
            result["confidence_adjustment"] = max(-0.15, min(0.05, float(result["confidence_adjustment"])))
            result["lineup_confidence"] = max(0.0, min(1.0, float(result["lineup_confidence"])))
            result["home_net_impact"] = max(-1.0, min(1.0, float(result["home_net_impact"])))
            result["away_net_impact"] = max(-1.0, min(1.0, float(result["away_net_impact"])))

            return result

    except Exception as e:
        console.print(f"  [yellow]Gemini error: {e}[/yellow]")

    return {
        "flag": "ok", "reason": "AI check unavailable", "confidence_adjustment": 0,
        "tokens_used": 0, "players_out": [], "players_doubtful": [], "players_returning": [],
        "lineup_confidence": 0.5, "home_net_impact": 0.0, "away_net_impact": 0.0,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def run_news_checker(dry_run: bool = False):
    today = date.today().isoformat()
    console.print(f"[bold cyan]═══ OddsIntel AI News Checker: {today} ═══[/bold cyan]\n")

    client = get_client()

    # Get today's pending bets with match context
    result = client.table("simulated_bets").select(
        "id, market, selection, odds_at_pick, model_probability, edge_percent, reasoning, "
        "matches(id, date, "
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
        home_team_data = match.get("home_team", {})
        away_team_data = match.get("away_team", {})
        league_data = match.get("leagues", {})

        home_team = (home_team_data[0]["name"] if isinstance(home_team_data, list) else home_team_data.get("name", "?"))
        away_team = (away_team_data[0]["name"] if isinstance(away_team_data, list) else away_team_data.get("name", "?"))
        league = (league_data[0]["name"] if isinstance(league_data, list) else league_data.get("name", "?"))
        tier = (league_data[0]["tier"] if isinstance(league_data, list) else league_data.get("tier", 1))

        if match_id not in checked_matches:
            console.print(f"[cyan]Checking: {home_team} vs {away_team}...[/cyan]")

            home_facts = []
            away_facts = []
            lineups = {}

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
        lineup_conf = ai_result.get("lineup_confidence", 0.5)
        home_impact = ai_result.get("home_net_impact", 0.0)
        away_impact = ai_result.get("away_net_impact", 0.0)
        players_out = ai_result.get("players_out", [])
        players_doubtful = ai_result.get("players_doubtful", [])
        players_returning = ai_result.get("players_returning", [])

        flag_display = {
            "ok": "[green]✓ OK[/green]",
            "warning": "[yellow]⚠ WARNING[/yellow]",
            "skip": "[red]✗ SKIP[/red]",
        }.get(flag, flag)

        # Show player impacts if any
        player_summary = ""
        if players_out:
            names = [f"{p.get('name', '?')}({p.get('impact', 0):+.2f})" for p in players_out[:3]]
            player_summary = f" | Out: {', '.join(names)}"
        if players_returning:
            names = [f"{p.get('name', '?')}({p.get('impact', 0):+.2f})" for p in players_returning[:2]]
            player_summary += f" | Back: {', '.join(names)}"

        console.print(f"  {flag_display} — {reason} [dim]lineup_conf={lineup_conf:.1f}{player_summary}[/dim]")

        results_table.append({
            "match": f"{home_team[:12]} v {away_team[:12]}",
            "bet": f"{bet['market']} {bet['selection']}",
            "flag": flag,
            "reason": reason[:60],
            "adj": adj,
            "lineup_conf": lineup_conf,
        })

        # Update bet in DB with structured news data
        if not dry_run:
            existing_reasoning = bet.get("reasoning", "") or ""
            ai_note = f" | AI [{flag.upper()}]: {reason}"
            if adj != 0:
                ai_note += f" (confidence adj: {adj:+.1%})"

            updated_prob = max(0.05, min(0.95, bet["model_probability"] + adj))

            # Compute news_impact_score: net impact on the team our bet favors
            # For Home bet: positive home_impact is good, negative away_impact is good
            # For Away bet: positive away_impact is good, negative home_impact is good
            selection = bet.get("selection", "").lower()
            if selection == "home":
                news_impact = home_impact - away_impact
            elif selection == "away":
                news_impact = away_impact - home_impact
            else:
                # O/U: both teams' attacking losses hurt Over, help Under
                news_impact = home_impact + away_impact  # negative = bad for attacking

            bet_update = {
                "reasoning": existing_reasoning + ai_note,
                "model_probability": updated_prob,
                "news_triggered": flag in ("warning", "skip"),
            }

            # Store structured fields (migration 006 columns)
            bet_update["news_impact_score"] = round(news_impact, 4)
            bet_update["lineup_confirmed"] = lineup_conf >= 0.9

            client.table("simulated_bets").update(bet_update).eq("id", bet["id"]).execute()

            # S3: Write signals to match_signals for ML training
            try:
                store_match_signal(match_id, "news_impact_score",
                                   round(news_impact, 4), "information", "gemini")
                store_match_signal(match_id, "lineup_confidence",
                                   round(lineup_conf, 3), "information", "gemini")
                if players_out or players_doubtful:
                    home_out = sum(1 for p in players_out
                                  if p.get("team", "").lower() in ("home", match.get("home_team", "").lower()))
                    away_out = len(players_out) - home_out
                    if home_out > 0:
                        store_match_signal(match_id, "players_out_home_ai",
                                           float(home_out), "information", "gemini")
                    if away_out > 0:
                        store_match_signal(match_id, "players_out_away_ai",
                                           float(away_out), "information", "gemini")
            except Exception:
                pass  # non-critical

            # Store structured AI output in news_events table
            for player_info in players_out + players_doubtful:
                try:
                    impact_type = "injury" if "injur" in player_info.get("reason", "").lower() else "suspension"
                    client.table("news_events").insert({
                        "match_id": match_id,
                        "source": "gemini_news_checker_v2",
                        "raw_text": f"{player_info.get('name', '?')} ({player_info.get('position', '?')}) — {player_info.get('reason', 'out')}",
                        "extracted_entity": player_info.get("name"),
                        "impact_type": impact_type,
                        "impact_magnitude": abs(float(player_info.get("impact", 0))) * 100,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }).execute()
                except Exception:
                    pass  # Duplicate or non-critical

            # Save Stage 2 snapshot: post-AI probability with full structured data
            try:
                odds = bet["odds_at_pick"]
                store_prediction_snapshot(
                    bet_id=bet["id"],
                    stage="post_ai",
                    model_probability=updated_prob,
                    implied_probability=1 / odds if odds > 0 else None,
                    edge_percent=updated_prob - (1 / odds) if odds > 0 else None,
                    odds_at_snapshot=odds,
                    metadata={
                        "ai_flag": flag,
                        "ai_reason": reason[:200],
                        "confidence_adjustment": adj,
                        "lineup_confidence": lineup_conf,
                        "home_net_impact": home_impact,
                        "away_net_impact": away_impact,
                        "news_impact_score": round(news_impact, 4),
                        "players_out_count": len(players_out),
                        "players_doubtful_count": len(players_doubtful),
                        "players_returning_count": len(players_returning),
                        "lineups_confirmed": match_context.get("lineups", {}).get("home_confirmed", False),
                    },
                )
            except Exception:
                pass  # non-critical

            # Save Stage 3 snapshot: pre_kickoff for matches kicking off within 3 hours
            kickoff_str = match.get("date", "")
            if kickoff_str:
                try:
                    kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                    if kickoff_dt.tzinfo is None:
                        kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
                    hours_to_kickoff = (kickoff_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                    if 0 <= hours_to_kickoff <= 3:
                        store_prediction_snapshot(
                            bet_id=bet["id"],
                            stage="pre_kickoff",
                            model_probability=updated_prob,
                            implied_probability=1 / odds if odds > 0 else None,
                            edge_percent=updated_prob - (1 / odds) if odds > 0 else None,
                            odds_at_snapshot=odds,
                            metadata={
                                "hours_to_kickoff": round(hours_to_kickoff, 2),
                                "ai_flag": flag,
                                "lineup_confidence": lineup_conf,
                                "news_impact_score": round(news_impact, 4),
                            },
                        )
                except Exception:
                    pass  # non-critical

    # Summary table
    console.print()
    t = Table(title="AI News Check Summary")
    t.add_column("Match", style="cyan")
    t.add_column("Bet")
    t.add_column("Flag")
    t.add_column("Reason")
    t.add_column("Adj", justify="right")
    t.add_column("Lineup", justify="right")

    for r in results_table:
        flag_cell = {"ok": "[green]OK[/green]", "warning": "[yellow]WARN[/yellow]", "skip": "[red]SKIP[/red]"}.get(r["flag"], r["flag"])
        lc = r.get("lineup_conf", 0.5)
        lc_cell = f"[green]{lc:.0%}[/green]" if lc >= 0.9 else f"[yellow]{lc:.0%}[/yellow]" if lc >= 0.6 else f"[red]{lc:.0%}[/red]"
        t.add_row(r["match"], r["bet"], flag_cell, r["reason"], f"{r['adj']:+.1%}" if r["adj"] != 0 else "-", lc_cell)

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
