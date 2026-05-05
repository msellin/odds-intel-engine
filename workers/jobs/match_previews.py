"""
OddsIntel — Daily AI Match Previews (ENG-3)

Generates Gemini-powered ~200-word match previews for today's top 10 matches,
ranked by signal count and league tier. Runs at 07:00 UTC after the morning
pipeline completes.

Each preview gets:
  - preview_text  (~200 words, full context — shown to Pro/Elite)
  - preview_short (~50 words teaser — shown to Free users)

Content is triple-duty: on-site match detail cards, email digest, social posts.

Usage:
  python -m workers.jobs.match_previews           # live run
  python -m workers.jobs.match_previews --dry-run # print previews, no DB writes
  python -m workers.jobs.match_previews --limit 3 # generate for top 3 only
"""

import sys
import os
import json
import re
import argparse
from pathlib import Path
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google import genai
from workers.api_clients.db import execute_query, execute_write

console = Console()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
GEMINI_MODEL = "gemini-2.5-flash"

# Top N matches to preview each day
DEFAULT_LIMIT = 10


# ── Match selection ────────────────────────────────────────────────────────

def select_top_matches(target_date: str, limit: int) -> list[dict]:
    """
    Pick today's top matches ranked by:
      1. League tier (lower = better)
      2. Signal count (more = more interesting)
      3. Model confidence (higher = more newsworthy)
    Only scheduled/upcoming matches (not already live or finished).
    """
    rows = execute_query(
        """
        SELECT
            m.id,
            m.date AS kickoff,
            ht.name  AS home_team,
            at.name  AS away_team,
            l.name   AS league,
            l.country,
            l.tier   AS league_tier,
            COALESCE(sig.signal_count, 0) AS signal_count,
            MAX(CASE WHEN p.market = '1x2_home' THEN p.model_probability END) AS home_win_prob,
            MAX(CASE WHEN p.market = '1x2_draw' THEN p.model_probability END) AS draw_prob,
            MAX(CASE WHEN p.market = '1x2_away' THEN p.model_probability END) AS away_win_prob
        FROM matches m
        JOIN teams  ht ON ht.id = m.home_team_id
        JOIN teams  at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
            AND p.source = 'ensemble'
        LEFT JOIN (
            SELECT match_id, COUNT(*) AS signal_count
            FROM match_signals
            GROUP BY match_id
        ) sig ON sig.match_id = m.id
        WHERE m.date::date = %s
          AND m.status IN ('scheduled', 'live')
          AND l.is_active = true
        GROUP BY m.id, m.date, ht.name, at.name, l.name, l.country, l.tier, sig.signal_count
        ORDER BY l.tier ASC, sig.signal_count DESC NULLS LAST
        LIMIT %s
        """,
        [target_date, limit],
    )
    return rows or []


# ── Signal context ─────────────────────────────────────────────────────────

def fetch_match_signals(match_id: str) -> dict:
    """Fetch key signals for a match as a flat dict."""
    rows = execute_query(
        """
        SELECT signal_name, signal_value
        FROM match_signals
        WHERE match_id = %s
        ORDER BY signal_name
        """,
        [match_id],
    )
    return {r["signal_name"]: r["signal_value"] for r in (rows or [])}


def fetch_recent_form(match_id: str) -> tuple[str, str]:
    """Pull recent form strings from match_signals if stored."""
    rows = execute_query(
        """
        SELECT signal_name, signal_value
        FROM match_signals
        WHERE match_id = %s
          AND signal_name IN ('home_form_string', 'away_form_string',
                               'home_wins_last5', 'home_draws_last5', 'home_losses_last5',
                               'away_wins_last5', 'away_draws_last5', 'away_losses_last5')
        """,
        [match_id],
    )
    sigs = {r["signal_name"]: r["signal_value"] for r in (rows or [])}
    home_form = sigs.get("home_form_string", "")
    away_form = sigs.get("away_form_string", "")
    if not home_form:
        hw = int(sigs.get("home_wins_last5", 0) or 0)
        hd = int(sigs.get("home_draws_last5", 0) or 0)
        hl = int(sigs.get("home_losses_last5", 0) or 0)
        if hw + hd + hl > 0:
            home_form = f"{hw}W {hd}D {hl}L in last 5"
    if not away_form:
        aw = int(sigs.get("away_wins_last5", 0) or 0)
        ad = int(sigs.get("away_draws_last5", 0) or 0)
        al = int(sigs.get("away_losses_last5", 0) or 0)
        if aw + ad + al > 0:
            away_form = f"{aw}W {ad}D {al}L in last 5"
    return home_form, away_form


def fetch_odds_summary(match_id: str) -> dict:
    """Pull latest 1X2 odds for a match, pivoted to home/draw/away."""
    rows = execute_query(
        """
        SELECT
            MAX(CASE WHEN selection = 'home' THEN odds END) AS home_odds,
            MAX(CASE WHEN selection = 'draw' THEN odds END) AS draw_odds,
            MAX(CASE WHEN selection = 'away' THEN odds END) AS away_odds,
            MIN(bookmaker) AS bookmaker
        FROM odds_snapshots
        WHERE match_id = %s
          AND market = '1x2'
          AND timestamp = (
              SELECT MAX(timestamp) FROM odds_snapshots
              WHERE match_id = %s AND market = '1x2'
          )
        """,
        [match_id, match_id],
    )
    if rows and rows[0].get("home_odds"):
        return rows[0]
    return {}


def fetch_injuries(match_id: str) -> tuple[list[str], list[str]]:
    """Return (home_injuries, away_injuries) player name lists."""
    rows = execute_query(
        """
        SELECT player_name, team_side
        FROM match_injuries
        WHERE match_id = %s
          AND status IN ('injured', 'doubtful', 'missing')
        LIMIT 10
        """,
        [match_id],
    )
    home_inj, away_inj = [], []
    for r in (rows or []):
        if r["team_side"] == "home":
            home_inj.append(r["player_name"])
        else:
            away_inj.append(r["player_name"])
    return home_inj, away_inj


# ── Gemini preview generation ──────────────────────────────────────────────

def generate_preview(match: dict) -> dict | None:
    """
    Call Gemini to produce a full preview + short teaser for one match.
    Returns dict with preview_text, preview_short, tokens_used or None on error.
    """
    home = match["home_team"]
    away = match["away_team"]
    league = match["league"]
    country = match.get("country", "")
    kickoff = match.get("kickoff", "")
    home_form, away_form = fetch_recent_form(match["id"])
    odds = fetch_odds_summary(match["id"])
    home_inj, away_inj = fetch_injuries(match["id"])
    signals = fetch_match_signals(match["id"])

    # Build odds line
    odds_line = ""
    if odds:
        odds_line = (
            f"Odds ({odds.get('bookmaker', 'avg')}): "
            f"Home {odds.get('home_odds', '?')} | "
            f"Draw {odds.get('draw_odds', '?')} | "
            f"Away {odds.get('away_odds', '?')}"
        )

    # Model prediction block
    pred_line = ""
    if match.get("home_win_prob"):
        pred_line = (
            f"Model probabilities: Home {match['home_win_prob']:.0%} | "
            f"Draw {match['draw_prob']:.0%} | "
            f"Away {match['away_win_prob']:.0%}"
        )
        if match.get("predicted_home_score") is not None:
            pred_line += (
                f" | Predicted score: {match['predicted_home_score']:.1f}–{match['predicted_away_score']:.1f}"
            )

    # Key signals
    notable_signals = []
    if signals.get("bdm_score", 0) and float(signals.get("bdm_score", 0)) > 0.10:
        notable_signals.append(f"bookmaker disagreement {float(signals['bdm_score']):.2f}")
    if signals.get("olm_score", 0) and float(signals.get("olm_score", 0)) > 0.05:
        notable_signals.append(f"late odds movement {float(signals['olm_score']):.2f}")
    if signals.get("home_elo") and signals.get("away_elo"):
        elo_diff = float(signals["home_elo"]) - float(signals["away_elo"])
        if abs(elo_diff) > 50:
            notable_signals.append(f"ELO gap {abs(elo_diff):.0f} pts ({'home' if elo_diff > 0 else 'away'} stronger)")

    injury_block = ""
    if home_inj:
        injury_block += f"{home}: {', '.join(home_inj[:3])} unavailable. "
    if away_inj:
        injury_block += f"{away}: {', '.join(away_inj[:3])} unavailable."

    prompt = f"""You are a sharp football analyst writing a concise, data-driven match preview for OddsIntel, a betting intelligence platform. Use a professional, analytical tone — no hype, no "guaranteed" language, no gambling encouragement.

MATCH: {home} vs {away}
COMPETITION: {league}, {country}
KICKOFF: {kickoff}

RECENT FORM:
- {home}: {home_form or 'form data unavailable'}
- {away}: {away_form or 'form data unavailable'}

{odds_line}
{pred_line}

{"KEY MODEL SIGNALS: " + ", ".join(notable_signals) if notable_signals else ""}
{"INJURIES/ABSENCES: " + injury_block if injury_block else ""}

Write TWO outputs:

1. FULL_PREVIEW: A 180–220 word analytical preview. Cover: current form and momentum, tactical context (if evident from data), what the odds imply, key factors that could decide the match. End with one sentence on what to watch. Factual, analyst tone. Do NOT invent specific player names beyond what's provided. Do NOT use "guaranteed", "sure bet", "can't lose", "banker".

2. SHORT_TEASER: A 40–55 word teaser summary highlighting the single most interesting angle from the preview. Used for free users and email subject lines.

Respond with ONLY a JSON object — no other text:
{{
  "full_preview": "...",
  "short_teaser": "..."
}}"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            console.print(f"  [yellow]No JSON in Gemini response for {home} vs {away}[/yellow]")
            return None

        result = json.loads(json_match.group())
        full = result.get("full_preview", "").strip()
        short = result.get("short_teaser", "").strip()
        tokens = getattr(response.usage_metadata, "total_token_count", 0) if hasattr(response, "usage_metadata") else 0

        if not full or not short:
            console.print(f"  [yellow]Empty preview fields for {home} vs {away}[/yellow]")
            return None

        return {"preview_text": full, "preview_short": short, "tokens_used": tokens}

    except Exception as e:
        console.print(f"  [red]Gemini error for {home} vs {away}: {e}[/red]")
        return None


# ── DB upsert ──────────────────────────────────────────────────────────────

def store_preview(match: dict, preview: dict, target_date: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    try:
        execute_write(
            """
            INSERT INTO match_previews
              (match_id, match_date, preview_text, preview_short,
               signal_count, league_tier, generated_at, tokens_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id, match_date)
            DO UPDATE SET
              preview_text  = EXCLUDED.preview_text,
              preview_short = EXCLUDED.preview_short,
              signal_count  = EXCLUDED.signal_count,
              generated_at  = EXCLUDED.generated_at,
              tokens_used   = EXCLUDED.tokens_used
            """,
            [
                match["id"],
                target_date,
                preview["preview_text"],
                preview["preview_short"],
                match.get("signal_count", 0),
                match.get("league_tier", 1),
                datetime.now(timezone.utc).isoformat(),
                preview.get("tokens_used", 0),
            ],
        )
        return True
    except Exception as e:
        console.print(f"  [red]DB error storing preview: {e}[/red]")
        return False


# ── Main ───────────────────────────────────────────────────────────────────

def run_match_previews(target_date: str | None = None, limit: int = DEFAULT_LIMIT, dry_run: bool = False):
    today = target_date or date.today().isoformat()
    console.print(f"[bold cyan]═══ OddsIntel Match Previews: {today} ═══[/bold cyan]\n")

    matches = select_top_matches(today, limit)
    if not matches:
        console.print("[yellow]No matches found for today — nothing to preview.[/yellow]")
        return

    console.print(f"Generating previews for {len(matches)} matches...\n")

    total_tokens = 0
    generated = 0
    failed = 0

    for i, match in enumerate(matches, 1):
        home = match["home_team"]
        away = match["away_team"]
        league = match["league"]
        tier = match.get("league_tier", "?")
        sigs = match.get("signal_count", 0)

        console.print(f"[{i}/{len(matches)}] {home} vs {away} ({league}, Tier {tier}, {sigs} signals)")

        preview = generate_preview(match)
        if not preview:
            failed += 1
            continue

        tokens = preview.get("tokens_used", 0)
        total_tokens += tokens

        if dry_run:
            console.print(f"  [dim]TEASER:[/dim] {preview['preview_short'][:120]}")
            console.print(f"  [dim]FULL (first 150):[/dim] {preview['preview_text'][:150]}...")
            console.print(f"  [dim]~{tokens} tokens[/dim]\n")
        else:
            ok = store_preview(match, preview, today, dry_run=False)
            if ok:
                console.print(f"  [green]✓ Stored ({tokens} tokens)[/green]")
                generated += 1
            else:
                failed += 1

    cost_est = total_tokens * 0.000001
    console.print(f"\n[bold]Done:[/bold] {generated} previews stored | {failed} failed | ~{total_tokens:,} tokens | ~${cost_est:.4f}")
    if dry_run:
        console.print("[yellow](dry-run — nothing written to DB)[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate daily AI match previews")
    parser.add_argument("--dry-run", action="store_true", help="Print previews without writing to DB")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Max matches to preview (default {DEFAULT_LIMIT})")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    run_match_previews(target_date=args.date, limit=args.limit, dry_run=args.dry_run)
