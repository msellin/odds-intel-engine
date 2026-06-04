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

    A preview is also re-generated when its `generated_at` is older
    than the freshest prediction or market-consensus snapshot for the
    fixture — so a freshly blended model or freshly scraped market row
    triggers a rewrite even if the 24h timer hasn't elapsed yet.
    """
    today = date.today()
    horizon = today + timedelta(days=days)

    # Prefer national_team_v1_blended when present (else national_team_v1).
    # We compute MAX(created_at) over both sources so the staleness check
    # can see the latest prediction write (blended job runs after raw).
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
            -- prefer blended; fall back to raw v1 with COALESCE per market
            COALESCE(
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_home' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS home_win_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_draw' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS draw_prob,
            COALESCE(
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1_blended'
                         THEN p.model_probability END),
                MAX(CASE WHEN p.market='1x2_away' AND p.source='national_team_v1'
                         THEN p.model_probability END)
            ) AS away_win_prob,
            -- which source actually fed the picked row above
            CASE
                WHEN BOOL_OR(p.source='national_team_v1_blended') THEN 'national_team_v1_blended'
                WHEN BOOL_OR(p.source='national_team_v1')         THEN 'national_team_v1'
                ELSE NULL
            END AS model_source,
            MAX(p.created_at)         AS prediction_updated_at,
            mc.updated_at             AS market_updated_at,
            mp.generated_at           AS preview_generated_at
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
            AND p.source IN ('national_team_v1', 'national_team_v1_blended')
        LEFT JOIN wc_market_consensus mc ON mc.match_id = m.id
        LEFT JOIN match_previews mp ON mp.match_id = m.id
        WHERE l.api_football_id = %s
          AND m.date::date >= %s
          AND m.date::date <= %s
          AND m.status IN ('scheduled', 'live')
        GROUP BY m.id, m.date, m.venue_name, m.h2h_home_wins, m.h2h_draws,
                 m.h2h_away_wins, ht.id, ht.name, at.id, at.name, l.name,
                 l.country, mc.updated_at, mp.generated_at
        ORDER BY m.date ASC
        """,
        [WC_LEAGUE_AF_ID, today.isoformat(), horizon.isoformat()],
    ) or []

    if force:
        return rows

    cutoff = datetime.now(timezone.utc) - timedelta(hours=REFRESH_AFTER_HOURS)
    fresh: list[dict] = []
    for r in rows:
        gen = _coerce_dt(r.get("preview_generated_at"))
        if gen is None:
            fresh.append(r)
            continue
        if gen < cutoff:
            fresh.append(r)
            continue
        # Even if the preview is younger than 24h, regenerate when newer
        # prediction / market-consensus rows have landed since.
        pred_ts = _coerce_dt(r.get("prediction_updated_at"))
        mkt_ts = _coerce_dt(r.get("market_updated_at"))
        newest_ctx = max([t for t in (pred_ts, mkt_ts) if t is not None],
                         default=None)
        if newest_ctx is not None and newest_ctx > gen:
            fresh.append(r)
    return fresh


def _coerce_dt(value) -> datetime | None:
    """Coerce a DB timestamp (str | datetime | None) to a UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
    return None


# ── Context fetchers ───────────────────────────────────────────────────────

def fetch_intl_elo(team_id: str) -> float | None:
    """Latest international ELO for a national team, or None."""
    info = fetch_intl_elo_row(team_id)
    return info["elo"] if info else None


def fetch_intl_elo_row(team_id: str) -> dict | None:
    """Latest international ELO + sample size for a national team, or None.

    Returns {'elo': float, 'n_matches': int} so the prompt can qualify a
    rating ("Brazil 2050 over 1,200 international matches") without a
    second round-trip.
    """
    rows = execute_query(
        """
        SELECT elo_rating, n_matches
        FROM team_elo_international
        WHERE team_id = %s
        ORDER BY match_date DESC
        LIMIT 1
        """,
        [team_id],
    )
    if not rows:
        return None
    elo = rows[0].get("elo_rating")
    if elo is None:
        return None
    n = rows[0].get("n_matches") or 0
    return {"elo": float(elo), "n_matches": int(n)}


def fetch_roster_strength(team_id: str) -> dict | None:
    """Latest team_roster_strength snapshot. None when WC-A2 hasn't run yet.

    Returns {'squad_value_eur', 'top_player_value_eur', 'avg_xi_club_elo',
    'n_players_resolved'} — any field can be None if the scraper couldn't
    resolve it. Caller should tolerate missing fields per the prompt spec.
    """
    rows = execute_query(
        """
        SELECT total_squad_value_eur,
               top_player_value_eur,
               avg_starting_xi_club_elo,
               n_players_resolved
        FROM team_roster_strength
        WHERE team_id = %s
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        [team_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "squad_value_eur": int(r["total_squad_value_eur"])
            if r.get("total_squad_value_eur") is not None else None,
        "top_player_value_eur": int(r["top_player_value_eur"])
            if r.get("top_player_value_eur") is not None else None,
        "avg_xi_club_elo": float(r["avg_starting_xi_club_elo"])
            if r.get("avg_starting_xi_club_elo") is not None else None,
        "n_players_resolved": int(r.get("n_players_resolved") or 0),
    }


def fetch_market_consensus(match_id: str) -> dict | None:
    """Latest market consensus 1X2 + source count for a fixture, or None."""
    rows = execute_query(
        """
        SELECT home_prob, draw_prob, away_prob, n_sources
        FROM wc_market_consensus
        WHERE match_id = %s
        LIMIT 1
        """,
        [match_id],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "home": float(r["home_prob"]),
        "draw": float(r["draw_prob"]),
        "away": float(r["away_prob"]),
        "n_sources": int(r.get("n_sources") or 0),
    }


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


def _percent(p) -> int | None:
    """Convert a probability (0..1) — possibly Decimal/str — to an integer %."""
    if p is None:
        return None
    try:
        return round(100 * float(p))
    except (TypeError, ValueError):
        return None


def _fmt_eur(value: int | None) -> str | None:
    """Format a EUR amount as a compact human string (e.g. '€1.2B', '€640M')."""
    if value is None:
        return None
    v = float(value)
    if v >= 1e9:
        return f"€{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"€{v / 1e6:.0f}M"
    if v >= 1e3:
        return f"€{v / 1e3:.0f}k"
    return f"€{int(v)}"


def _pick_hint(home: str, away: str,
               home_pct: int | None, draw_pct: int | None, away_pct: int | None) -> str:
    """Render the model's pick as a one-line hint (used by store_preview consumers)."""
    if home_pct is None or away_pct is None:
        return "no clear pick"
    probs = {home: home_pct, "draw": (draw_pct or 0), away: away_pct}
    pick = max(probs, key=lambda k: probs[k])
    if pick == "draw":
        return f"model leans toward the draw at {draw_pct}%"
    other_draw = f", but the draw is live at {draw_pct}%" if (draw_pct or 0) >= 25 else ""
    return f"model leans {pick}{other_draw}"


def build_gemini_prompt(
    match: dict,
    *,
    home_elo_info: dict | None = None,
    away_elo_info: dict | None = None,
    home_form: str = "",
    away_form: str = "",
    home_inj: list[str] | None = None,
    away_inj: list[str] | None = None,
    home_roster: dict | None = None,
    away_roster: dict | None = None,
    market: dict | None = None,
) -> str:
    """Build the full Gemini prompt for a fixture.

    Pure string builder — no DB calls, no network. All context comes in
    via kwargs so the prompt is unit-testable and the rendered string is
    deterministic given identical inputs. Missing sections degrade
    silently (market consensus, roster, injuries are all optional).

    The prompt forces Gemini to reference the actual numbers passed in
    (our model %, market %, ELO gap) rather than write generic prose.
    """
    home = match["home_team"]
    away = match["away_team"]
    kickoff_dt = match.get("kickoff")
    if isinstance(kickoff_dt, str):
        kickoff_str = kickoff_dt
        try:
            kickoff_date = datetime.fromisoformat(kickoff_dt.replace("Z", "+00:00")).date()
        except ValueError:
            kickoff_date = date.today()
    elif isinstance(kickoff_dt, datetime):
        kickoff_str = kickoff_dt.isoformat()
        kickoff_date = kickoff_dt.date()
    else:
        kickoff_str = "TBD"
        kickoff_date = date.today()

    venue = match.get("venue_name") or "venue TBD"
    stage = _stage_for(kickoff_date)

    home_pct = _percent(match.get("home_win_prob"))
    draw_pct = _percent(match.get("draw_prob"))
    away_pct = _percent(match.get("away_win_prob"))

    model_source = match.get("model_source") or "national_team_v1"
    blended = model_source == "national_team_v1_blended"

    # ── Model probability line ────────────────────────────────────────────
    if home_pct is not None and away_pct is not None:
        tag = "blended (own × market)" if blended else "ELO+Poisson, own model only"
        pred_line = (
            f"Our model ({tag}) gives: {home} {home_pct}% · draw {draw_pct or 0}% "
            f"· {away} {away_pct}%."
        )
    else:
        pred_line = "Our model has not generated a 1X2 for this fixture yet."

    # ── Market consensus line ────────────────────────────────────────────
    market_line = ""
    if market is not None:
        m_home = _percent(market.get("home"))
        m_draw = _percent(market.get("draw"))
        m_away = _percent(market.get("away"))
        n_src = market.get("n_sources") or 0
        if m_home is not None and m_away is not None:
            market_line = (
                f"Market consensus ({n_src} source{'s' if n_src != 1 else ''}): "
                f"{home} {m_home}% · draw {m_draw or 0}% · {away} {m_away}%."
            )
            # Highlight disagreement so Gemini surfaces it.
            if home_pct is not None and m_home is not None:
                diff = abs(home_pct - m_home)
                if diff >= 10:
                    leaner = home if (home_pct or 0) > (m_home or 0) else away
                    market_line += (
                        f" Our model and the market disagree materially on {leaner} "
                        f"({diff}-point gap on the home side)."
                    )
    else:
        market_line = "Market consensus not yet scraped for this fixture."

    # ── ELO line (qualitative gap framing) ───────────────────────────────
    elo_line = ""
    if home_elo_info and away_elo_info:
        he = home_elo_info["elo"]
        ae = away_elo_info["elo"]
        gap = he - ae
        if abs(gap) < 20:
            elo_line = (
                f"International ELO is effectively level: {home} {he:.0f} vs "
                f"{away} {ae:.0f} (gap {abs(gap):.0f} points)."
            )
        else:
            leader = home if gap > 0 else away
            elo_line = (
                f"International ELO: {home} {he:.0f} vs {away} {ae:.0f} — "
                f"{leader} holds a {abs(gap):.0f}-point advantage."
            )

    # ── Squad value disparity (only when material: >1.5x) ────────────────
    squad_line = ""
    if home_roster and away_roster:
        hv = home_roster.get("squad_value_eur")
        av = away_roster.get("squad_value_eur")
        if hv and av and min(hv, av) > 0:
            ratio = max(hv, av) / min(hv, av)
            if ratio >= 1.5:
                richer = home if hv > av else away
                squad_line = (
                    f"Squad market value: {home} {_fmt_eur(hv)} vs {away} {_fmt_eur(av)} — "
                    f"{richer}'s pool is {ratio:.1f}× more valuable."
                )

    # ── Top players (star power) ─────────────────────────────────────────
    star_line = ""
    star_bits = []
    if home_roster and home_roster.get("top_player_value_eur"):
        star_bits.append(f"{home}'s top asset {_fmt_eur(home_roster['top_player_value_eur'])}")
    if away_roster and away_roster.get("top_player_value_eur"):
        star_bits.append(f"{away}'s top asset {_fmt_eur(away_roster['top_player_value_eur'])}")
    if star_bits:
        star_line = "Star power: " + "; ".join(star_bits) + "."

    # ── Injuries ─────────────────────────────────────────────────────────
    home_inj = home_inj or []
    away_inj = away_inj or []
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

    # Assemble — every optional line stands on its own row, blank rows
    # collapsed at the end so the prompt stays readable.
    context_lines = [
        pred_line,
        market_line,
        elo_line,
        squad_line,
        star_line,
        f"Recent international form (last 5, latest first): "
        f"{home}: {home_form or 'n/a'} | {away}: {away_form or 'n/a'}",
        h2h_line,
        ("Absences/doubts: " + inj_line) if inj_line else "",
    ]
    context_block = "\n".join(line for line in context_lines if line)

    prompt = f"""You are OddsIntel's analyst writing a brief preview of a 2026 FIFA World Cup match. Voice rules:
- Data-driven and specific. Every claim must reference a number from the CONTEXT block below.
- DO NOT use any of these phrases: {banned}.
- DO NOT invent player names — only the names that appear in CONTEXT.
- DO NOT invent statistics — if a number isn't in CONTEXT, don't write it.
- One paragraph, 80-120 words.
- The preview MUST explicitly cite at least one model probability (e.g. "Our model gives {home} {home_pct if home_pct is not None else 'NN'}%") and, if available, the market consensus (e.g. "the market has {away} at NN%").
- Highlight ONE thing the reader should actually watch.
- End with a single sentence stating the model's pick.

MATCH: {home} vs {away}
STAGE: {stage}
KICKOFF: {kickoff_str}
VENUE: {venue}

CONTEXT:
{context_block}

Write TWO outputs:

1. FULL_PREVIEW: 80-120 words. Single paragraph. Must quote at least one of our model probabilities verbatim (with the % sign) AND the market consensus if available. If our model and the market disagree by 10 points or more, note that disagreement explicitly. End with the model's pick — phrase it like "Model leans {{team}} at NN%." and append ", but the draw is live at X%." if the draw probability is 25% or higher. Use "Model sees a coin flip." instead if all three sides are within 8 points.

2. SHORT_TEASER: 30-50 words. The single sharpest angle from the preview, written as a standalone tease (no "in this preview" meta-talk). Must include at least one percentage from CONTEXT.

Respond with ONLY a JSON object — no other text:
{{
  "full_preview": "...",
  "short_teaser": "..."
}}"""

    return prompt


def generate_preview(match: dict) -> dict | None:
    """Build prompt, call Gemini, return {preview_text, preview_short, tokens_used}."""
    home = match["home_team"]
    away = match["away_team"]

    home_elo_info = fetch_intl_elo_row(match["home_id"])
    away_elo_info = fetch_intl_elo_row(match["away_id"])
    home_form = fetch_recent_intl_form(match["home_id"])
    away_form = fetch_recent_intl_form(match["away_id"])
    home_inj, away_inj = fetch_injuries(match["id"])
    home_roster = fetch_roster_strength(match["home_id"])
    away_roster = fetch_roster_strength(match["away_id"])
    market = fetch_market_consensus(match["id"])

    home_pct = _percent(match.get("home_win_prob"))
    draw_pct = _percent(match.get("draw_prob"))
    away_pct = _percent(match.get("away_win_prob"))
    pick_hint = _pick_hint(home, away, home_pct, draw_pct, away_pct)

    prompt = build_gemini_prompt(
        match,
        home_elo_info=home_elo_info,
        away_elo_info=away_elo_info,
        home_form=home_form,
        away_form=away_form,
        home_inj=home_inj,
        away_inj=away_inj,
        home_roster=home_roster,
        away_roster=away_roster,
        market=market,
    )

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
