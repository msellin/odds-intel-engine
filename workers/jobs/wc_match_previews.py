"""
OddsIntel — World Cup AI Match Previews (WC-AI-PREVIEW)

Generates Gemini-powered 80-120 word pre-match previews for every World Cup
2026 fixture in the next 7 days. Runs daily at 07:30 UTC after the national-
team predictor (04:00 UTC) has settled, gated to the FIFA tournament window
(2026-06-04 → 2026-07-19) so APScheduler doesn't burn quota the other ~340
days of the year.

Reuses the existing `match_previews` table (same shape as ENG-3 club job).
The match-detail page (`getMatchPreview`) and the `/world-cup` schedule +
group cards both render whatever is stored there — no migration needed.

Voice rules (baked into the prompt):
  - Data-driven, no clichés. Strip "clash of titans", "should be a cracker",
    "guaranteed", "sure bet", "banker".
  - 80-120 words. ONE thing to watch. End with the model's pick + the live
    draw % if it's > 25%.

Idempotent: if a preview row exists for the fixture and is < 24h old, skip
the Gemini call. Errors per-fixture are logged and the loop continues —
never crashes the cron.

Usage:
  python -m workers.jobs.wc_match_previews
  python -m workers.jobs.wc_match_previews --dry-run
  python -m workers.jobs.wc_match_previews --days 7
  python -m workers.jobs.wc_match_previews --force        # regenerate all
"""

import sys
import os
import json
import re
import argparse
import time
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google import genai
from workers.api_clients.db import execute_query, execute_write

console = Console()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
# Flash-Lite is the cheapest tier; 728 calls / tournament cycle ≈ $7.
GEMINI_MODEL = "gemini-2.5-flash-lite"

# Tournament constants — keep in sync with workers/jobs/wc_bracket_scoring.py.
WC_LEAGUE_AF_ID = 1
WC_WINDOW_START = date(2026, 6, 11)
WC_WINDOW_END = date(2026, 7, 19)

# Date windows mapping kickoff → bracket stage. Mirrors the windows in
# wc_bracket_scoring.py but with the labels we surface to readers.
STAGE_WINDOWS: list[tuple[str, date, date]] = [
    ("Group stage",      date(2026, 6, 11), date(2026, 6, 27)),
    ("Round of 32",      date(2026, 6, 28), date(2026, 7,  3)),
    ("Round of 16",      date(2026, 7,  4), date(2026, 7,  7)),
    ("Quarter-final",    date(2026, 7,  9), date(2026, 7, 11)),
    ("Semi-final",       date(2026, 7, 14), date(2026, 7, 15)),
    ("Third-place play-off", date(2026, 7, 18), date(2026, 7, 18)),
    ("Final",            date(2026, 7, 19), date(2026, 7, 19)),
]

# Rate limit: 1 request per second (Gemini flash-lite quota is generous,
# but this guards against the daily-quota wall on the free key).
GEMINI_MIN_INTERVAL_S = 1.0

# Default forward window — preview every fixture in the next 7 days.
DEFAULT_DAYS = 7

# Refresh threshold — re-generate any preview older than this.
REFRESH_AFTER_HOURS = 24


# ── Selection ──────────────────────────────────────────────────────────────

def _stage_for(d: date) -> str:
    """Return human-readable WC stage label for a kickoff date."""
    for label, start, end in STAGE_WINDOWS:
        if start <= d <= end:
            return label
    return "World Cup"


def select_wc_fixtures(days: int, force: bool) -> list[dict]:
    """
    Select WC fixtures in the next `days` days (from today, UTC) that
    either have no preview yet or whose preview is stale (older than
    REFRESH_AFTER_HOURS). `force=True` bypasses the staleness check
    and re-generates everything in the window.
    """
    today = date.today()
    horizon = today + timedelta(days=days)

    rows = execute_query(
        """
        SELECT
            m.id,
            m.date AS kickoff,
            m.date::date AS kickoff_date,
            m.venue_name,
            m.h2h_home_wins,
            m.h2h_draws,
            m.h2h_away_wins,
            ht.id   AS home_id,
            ht.name AS home_team,
            at.id   AS away_id,
            at.name AS away_team,
            l.name  AS league,
            l.country,
            MAX(CASE WHEN p.market = '1x2_home' THEN p.model_probability END) AS home_win_prob,
            MAX(CASE WHEN p.market = '1x2_draw' THEN p.model_probability END) AS draw_prob,
            MAX(CASE WHEN p.market = '1x2_away' THEN p.model_probability END) AS away_win_prob,
            mp.generated_at AS preview_generated_at
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
            AND p.source = 'national_team_v1'
        LEFT JOIN match_previews mp ON mp.match_id = m.id
        WHERE l.api_football_id = %s
          AND m.date::date >= %s
          AND m.date::date <= %s
          AND m.status IN ('scheduled', 'live')
        GROUP BY m.id, m.date, m.venue_name, m.h2h_home_wins, m.h2h_draws,
                 m.h2h_away_wins, ht.id, ht.name, at.id, at.name, l.name,
                 l.country, mp.generated_at
        ORDER BY m.date ASC
        """,
        [WC_LEAGUE_AF_ID, today.isoformat(), horizon.isoformat()],
    ) or []

    if force:
        return rows

    cutoff = datetime.now(timezone.utc) - timedelta(hours=REFRESH_AFTER_HOURS)
    fresh: list[dict] = []
    for r in rows:
        gen = r.get("preview_generated_at")
        if gen is None:
            fresh.append(r)
            continue
        # `gen` may be a datetime or a string depending on the driver.
        if isinstance(gen, str):
            try:
                gen = datetime.fromisoformat(gen.replace("Z", "+00:00"))
            except ValueError:
                fresh.append(r)
                continue
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        if gen < cutoff:
            fresh.append(r)
    return fresh


# ── Context fetchers ───────────────────────────────────────────────────────

def fetch_intl_elo(team_id: str) -> float | None:
    """Latest international ELO for a national team, or None."""
    rows = execute_query(
        """
        SELECT elo_rating
        FROM team_elo_international
        WHERE team_id = %s
        ORDER BY match_date DESC
        LIMIT 1
        """,
        [team_id],
    )
    if rows and rows[0].get("elo_rating") is not None:
        return float(rows[0]["elo_rating"])
    return None


def fetch_recent_intl_form(team_id: str, n: int = 5) -> str:
    """Return last-N internationals form string like 'WWDLW' (latest first)."""
    rows = execute_query(
        """
        SELECT m.result, m.home_team_id, m.away_team_id, m.date
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE (m.home_team_id = %s OR m.away_team_id = %s)
          AND m.status = 'finished'
          AND m.result IS NOT NULL
          AND l.api_football_id IN (1, 4, 9, 10, 32, 34, 37)
        ORDER BY m.date DESC
        LIMIT %s
        """,
        [team_id, team_id, n],
    )
    out = []
    for r in (rows or []):
        result = (r.get("result") or "").lower()
        is_home = str(r.get("home_team_id")) == str(team_id)
        if result == "home_win":
            out.append("W" if is_home else "L")
        elif result == "away_win":
            out.append("L" if is_home else "W")
        elif result == "draw":
            out.append("D")
        else:
            # Unknown result code — skip rather than guess.
            continue
    return "".join(out) if out else ""


def fetch_injuries(match_id: str) -> tuple[list[str], list[str]]:
    """Return (home_injuries, away_injuries) — up to 3 names each."""
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
        if r.get("team_side") == "home":
            home_inj.append(r["player_name"])
        else:
            away_inj.append(r["player_name"])
    return home_inj[:3], away_inj[:3]


# ── Prompt build + Gemini call ─────────────────────────────────────────────

# Cliché blacklist — passed to the prompt as an explicit "do not write"
# list and also scrubbed from the model output as a belt-and-braces step.
_BANNED_PHRASES = [
    "clash of titans", "should be a cracker", "guaranteed", "sure bet",
    "banker", "can't lose", "lock", "no-brainer", "must-win",
    "magical night", "epic encounter",
]


def _scrub_cliches(text: str) -> str:
    """Strip the worst offenders if Gemini sneaks one in (case-insensitive)."""
    out = text
    for phrase in _BANNED_PHRASES:
        out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


def _h2h_summary(home: str, away: str, hw: int | None, hd: int | None, aw: int | None) -> str:
    """Build a compact head-to-head line from the matches.h2h_* columns."""
    if hw is None and hd is None and aw is None:
        return "no recent head-to-head data"
    total = (hw or 0) + (hd or 0) + (aw or 0)
    if total == 0:
        return "no recent head-to-head data"
    return f"H2H last {total}: {home} {hw or 0}W · {hd or 0}D · {away} {aw or 0}W"


def generate_preview(match: dict) -> dict | None:
    """Build prompt, call Gemini, return {preview_text, preview_short, tokens_used}."""
    home = match["home_team"]
    away = match["away_team"]
    kickoff_dt = match.get("kickoff")
    if isinstance(kickoff_dt, str):
        kickoff_str = kickoff_dt
        try:
            kickoff_date = datetime.fromisoformat(kickoff_dt.replace("Z", "+00:00")).date()
        except ValueError:
            kickoff_date = date.today()
    else:
        kickoff_str = kickoff_dt.isoformat() if kickoff_dt else "TBD"
        kickoff_date = kickoff_dt.date() if kickoff_dt else date.today()

    venue = match.get("venue_name") or "venue TBD"
    stage = _stage_for(kickoff_date)

    home_elo = fetch_intl_elo(match["home_id"])
    away_elo = fetch_intl_elo(match["away_id"])
    home_form = fetch_recent_intl_form(match["home_id"])
    away_form = fetch_recent_intl_form(match["away_id"])
    home_inj, away_inj = fetch_injuries(match["id"])

    home_pct = round(100 * float(match["home_win_prob"])) if match.get("home_win_prob") else None
    draw_pct = round(100 * float(match["draw_prob"])) if match.get("draw_prob") else None
    away_pct = round(100 * float(match["away_win_prob"])) if match.get("away_win_prob") else None

    pred_line = "Model prediction unavailable (pre-tournament)."
    pick_hint = "no clear pick"
    if home_pct is not None and away_pct is not None:
        probs = {home: home_pct, "draw": (draw_pct or 0), away: away_pct}
        pick = max(probs, key=lambda k: probs[k])
        pred_line = (
            f"Model 1X2: {home} {home_pct}% · draw {draw_pct or 0}% · {away} {away_pct}%"
        )
        if pick == "draw":
            pick_hint = f"model leans toward the draw at {draw_pct}%"
        else:
            other_draw = f", but the draw is live at {draw_pct}%" if (draw_pct or 0) >= 25 else ""
            pick_hint = f"model leans {pick}{other_draw}"

    elo_line = ""
    if home_elo is not None and away_elo is not None:
        elo_line = f"International ELO: {home} {home_elo:.0f} vs {away} {away_elo:.0f} (gap {abs(home_elo - away_elo):.0f})."

    inj_line = ""
    if home_inj:
        inj_line += f"{home} missing: {', '.join(home_inj)}. "
    if away_inj:
        inj_line += f"{away} missing: {', '.join(away_inj)}."

    h2h_line = _h2h_summary(
        home, away,
        match.get("h2h_home_wins"), match.get("h2h_draws"), match.get("h2h_away_wins"),
    )

    banned = ", ".join(f'"{p}"' for p in _BANNED_PHRASES)

    prompt = f"""You are OddsIntel's analyst writing a brief preview of a 2026 FIFA World Cup match. Voice rules:
- Data-driven, specific, no fluff. No hype, no clichés.
- DO NOT use any of these phrases: {banned}.
- Do NOT invent player names — only use the names listed below.
- One paragraph, 80-120 words.
- Highlight ONE thing the reader should actually watch.
- End with a single sentence stating the model's pick.

MATCH: {home} vs {away}
STAGE: {stage}
KICKOFF: {kickoff_str}
VENUE: {venue}

{elo_line}
Recent international form (last 5, latest first): {home}: {home_form or 'n/a'} | {away}: {away_form or 'n/a'}
{h2h_line}
{pred_line}
{('Absences/doubts: ' + inj_line) if inj_line else ''}

Write TWO outputs:

1. FULL_PREVIEW: 80-120 words. Single paragraph. Cover ELO/form gap (if material), what the model sees, one thing to watch. End with the model's pick — phrase it like "Model leans {{team}}{', but the draw is live at X%' if relevant else ''}." or "Model sees a coin flip." if all three sides are within 8 pts.

2. SHORT_TEASER: 30-50 words. The single sharpest angle from the preview, written as a standalone tease (no "in this preview" meta-talk).

Respond with ONLY a JSON object — no other text:
{{
  "full_preview": "...",
  "short_teaser": "..."
}}"""

    try:
        _transient = ("ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded")
        response = None
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                )
                break
            except Exception as exc:
                if type(exc).__name__ in _transient and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        if response is None:
            return None
        text = (response.text or "").strip()
        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            console.print(f"  [yellow]No JSON in Gemini response for {home} vs {away}[/yellow]")
            return None

        result = json.loads(json_match.group())
        full = _scrub_cliches(result.get("full_preview", "").strip())
        short = _scrub_cliches(result.get("short_teaser", "").strip())
        if not full or not short:
            console.print(f"  [yellow]Empty preview fields for {home} vs {away}[/yellow]")
            return None
        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
            tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0
        return {
            "preview_text": full,
            "preview_short": short,
            "tokens_used": tokens,
            "pick_hint": pick_hint,
        }

    except Exception as e:
        # Graceful degradation: log + continue (caller will skip storage).
        console.print(f"  [red]Gemini error for {home} vs {away}: {type(e).__name__}: {e}[/red]")
        return None


# ── Storage ────────────────────────────────────────────────────────────────

def store_preview(match: dict, preview: dict, dry_run: bool) -> bool:
    """Upsert into match_previews. Reuses the ENG-3 club preview table."""
    if dry_run:
        return True
    try:
        # match_date convention from ENG-3 = the kickoff date (UTC).
        kickoff_dt = match.get("kickoff")
        if isinstance(kickoff_dt, str):
            try:
                match_date = datetime.fromisoformat(kickoff_dt.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                match_date = date.today().isoformat()
        else:
            match_date = kickoff_dt.date().isoformat() if kickoff_dt else date.today().isoformat()

        execute_write(
            """
            INSERT INTO match_previews
              (match_id, match_date, preview_text, preview_short,
               signal_count, league_tier, generated_at, model_used, tokens_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id, match_date)
            DO UPDATE SET
              preview_text  = EXCLUDED.preview_text,
              preview_short = EXCLUDED.preview_short,
              generated_at  = EXCLUDED.generated_at,
              model_used    = EXCLUDED.model_used,
              tokens_used   = EXCLUDED.tokens_used
            """,
            [
                match["id"],
                match_date,
                preview["preview_text"],
                preview["preview_short"],
                0,                                # signal_count — N/A for WC
                1,                                # league_tier — top tier
                datetime.now(timezone.utc).isoformat(),
                GEMINI_MODEL,
                preview.get("tokens_used", 0),
            ],
        )
        return True
    except Exception as e:
        console.print(f"  [red]DB error storing WC preview: {e}[/red]")
        return False


# ── Main ───────────────────────────────────────────────────────────────────

def run_wc_match_previews(
    days: int = DEFAULT_DAYS,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Callable entry point — scheduler imports this directly.

    Returns a summary dict: {generated, skipped, failed, tokens, cost_usd}.
    """
    console.print(f"[bold cyan]═══ WC AI Match Previews — next {days}d ═══[/bold cyan]\n")

    fixtures = select_wc_fixtures(days=days, force=force)
    if not fixtures:
        console.print("[yellow]No WC fixtures need a preview right now.[/yellow]")
        return {"generated": 0, "skipped": 0, "failed": 0, "tokens": 0, "cost_usd": 0.0}

    console.print(f"Generating previews for {len(fixtures)} WC fixtures...\n")

    generated = 0
    failed = 0
    total_tokens = 0
    last_call_ts = 0.0

    for i, fx in enumerate(fixtures, 1):
        home = fx["home_team"]
        away = fx["away_team"]
        kickoff = fx.get("kickoff")
        kickoff_disp = kickoff if isinstance(kickoff, str) else (kickoff.isoformat() if kickoff else "?")
        console.print(f"[{i}/{len(fixtures)}] {home} vs {away} — {kickoff_disp}")

        # Rate limit: 1 req/sec
        elapsed = time.monotonic() - last_call_ts
        if elapsed < GEMINI_MIN_INTERVAL_S:
            time.sleep(GEMINI_MIN_INTERVAL_S - elapsed)
        last_call_ts = time.monotonic()

        preview = generate_preview(fx)
        if not preview:
            failed += 1
            continue

        total_tokens += preview.get("tokens_used", 0)

        if dry_run:
            console.print(f"  [dim]TEASER:[/dim] {preview['preview_short']}")
            console.print(f"  [dim]FULL:[/dim]   {preview['preview_text']}\n")
            generated += 1
        else:
            ok = store_preview(fx, preview, dry_run=False)
            if ok:
                console.print(f"  [green]✓ Stored ({preview.get('tokens_used', 0)} tokens)[/green]")
                generated += 1
            else:
                failed += 1

    # Flash-Lite output ≈ $0.40 / 1M tokens; input ≈ $0.10 / 1M. Use
    # a conservative blended $1 / 1M for the headline number.
    cost_est = total_tokens * 0.000001
    console.print(
        f"\n[bold]Done:[/bold] {generated} previews | {failed} failed | "
        f"~{total_tokens:,} tokens | ~${cost_est:.4f}"
    )
    if dry_run:
        console.print("[yellow](dry-run — nothing written to DB)[/yellow]")

    return {
        "generated": generated,
        "skipped": 0,
        "failed": failed,
        "tokens": total_tokens,
        "cost_usd": cost_est,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate WC AI match previews")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print previews without writing to DB")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Forward window in days (default {DEFAULT_DAYS})")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate previews even if a fresh one exists")
    args = parser.parse_args()
    run_wc_match_previews(days=args.days, dry_run=args.dry_run, force=args.force)
