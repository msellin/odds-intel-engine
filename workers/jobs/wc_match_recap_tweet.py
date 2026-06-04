"""WC-F2 (2026-06-04) — Twitter / X auto-post on World Cup match resolution.

When settlement finishes a WC2026 fixture, this module composes a
one-liner recap ("OddsIntel predicted Brazil 55%, market gave them 60%.
Brazil won 2-1. ✓") and posts it from our @ handle via the v2 Twitter
API. The link in the tweet points to the canonical match-detail page
(oddsintel.app/matches/{id}) so Twitter's link-card unfurler picks up
the per-match OG image (WC-F5 — separate task, ships independently).

Idempotency: `wc_match_tweets` (PK on match_id) is the lock. We skip
silently if a row already exists, so a settlement re-run on the same
day does not double-post.

Failure handling: every external call (DB lookup, prediction lookup,
Twitter POST) is wrapped — a failure logs a warning and returns False
from `post_wc_match_recap` so the settlement loop never aborts on a
recap failure. Twitter creds missing → silently skip (post_tweet
returns None; we treat that as "no tweet, no audit row").

Tweet template:
    "<HomeFlag> <Home> <hg>-<ag> <AwayFlag> <Away> (<stage>).
     OddsIntel predicted <Winner> at <pct>%. <✓/✗>
     oddsintel.app/matches/<id>"

Length budget: 280 chars total. The URL counts as 23 chars per Twitter's
t.co wrapping rule regardless of original length, so we treat it as a
fixed 23-char overhead. Team names are truncated if the tweet would
exceed 280 — we shrink the league/stage suffix first, then names.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from workers.api_clients.db import execute_query, execute_write
from workers.api_clients.twitter import post_tweet

log = logging.getLogger(__name__)

# Frontend canonical domain — match the value-bets / match-detail pages.
SITE_BASE = "https://oddsintel.app"

# Twitter wraps every URL to a 23-char t.co token, no matter the original
# length (https://developer.x.com/en/docs/counting-characters).
TWITTER_URL_LENGTH = 23

# Hard cap. We aim for ≤270 in practice so future emoji width quirks
# don't push us over.
TWEET_MAX = 280

# FIFA 2026 schedule — mirrored from wc_bracket_scoring to avoid an import
# cycle (scoring imports a lot of bracket-only helpers; we just need round
# labels). Inclusive on both ends.
_ROUND_WINDOWS: list[tuple[str, date, date]] = [
    ("Group stage",   date(2026, 6, 11), date(2026, 6, 27)),
    ("Round of 32",   date(2026, 6, 28), date(2026, 7,  3)),
    ("Round of 16",   date(2026, 7,  4), date(2026, 7,  7)),
    ("Quarter-final", date(2026, 7,  9), date(2026, 7, 11)),
    ("Semi-final",    date(2026, 7, 14), date(2026, 7, 15)),
    ("Final",         date(2026, 7, 19), date(2026, 7, 19)),
]


def _stage_label(kickoff_date: Optional[date]) -> str:
    """Pretty round label for the tweet, or "World Cup" fallback."""
    if kickoff_date is None:
        return "World Cup"
    for label, start, end in _ROUND_WINDOWS:
        if start <= kickoff_date <= end:
            return label
    return "World Cup"


# Minimal country → flag emoji map covering every confirmed WC2026
# qualifier. Falls back to the soccer-ball emoji for any unknown name —
# keeps the tweet pretty without breaking on a rename / surprise host
# wildcard. Keys are matched case-insensitively against the team name.
#
# Source: FIFA WC2026 confirmed qualifiers (auto-qualified hosts +
# qualifying CONCACAF/UEFA/CONMEBOL/CAF/AFC/OFC slots) as of 2026-06-04.
# Edit when a final qualifying slot is decided.
_FLAGS: dict[str, str] = {
    # Hosts
    "united states": "🇺🇸", "usa": "🇺🇸", "us": "🇺🇸",
    "canada": "🇨🇦",
    "mexico": "🇲🇽",
    # UEFA
    "england": "🏴‍☠️",  # st-george placeholder; many fonts render the regional flag
    "france": "🇫🇷",
    "spain": "🇪🇸",
    "germany": "🇩🇪",
    "italy": "🇮🇹",
    "portugal": "🇵🇹",
    "netherlands": "🇳🇱",
    "belgium": "🇧🇪",
    "croatia": "🇭🇷",
    "switzerland": "🇨🇭",
    "denmark": "🇩🇰",
    "poland": "🇵🇱",
    "austria": "🇦🇹",
    "serbia": "🇷🇸",
    "turkey": "🇹🇷",
    "ukraine": "🇺🇦",
    "wales": "🏴‍☠️",
    "scotland": "🏴‍☠️",
    "norway": "🇳🇴",
    "sweden": "🇸🇪",
    # CONMEBOL
    "brazil": "🇧🇷",
    "argentina": "🇦🇷",
    "uruguay": "🇺🇾",
    "colombia": "🇨🇴",
    "ecuador": "🇪🇨",
    "paraguay": "🇵🇾",
    "chile": "🇨🇱",
    "peru": "🇵🇪",
    "bolivia": "🇧🇴",
    "venezuela": "🇻🇪",
    # CONCACAF
    "costa rica": "🇨🇷",
    "panama": "🇵🇦",
    "jamaica": "🇯🇲",
    "honduras": "🇭🇳",
    "el salvador": "🇸🇻",
    # CAF
    "morocco": "🇲🇦",
    "senegal": "🇸🇳",
    "tunisia": "🇹🇳",
    "egypt": "🇪🇬",
    "algeria": "🇩🇿",
    "nigeria": "🇳🇬",
    "ghana": "🇬🇭",
    "cameroon": "🇨🇲",
    "south africa": "🇿🇦",
    "ivory coast": "🇨🇮",
    "côte d'ivoire": "🇨🇮",
    # AFC
    "japan": "🇯🇵",
    "south korea": "🇰🇷",
    "korea republic": "🇰🇷",
    "iran": "🇮🇷",
    "australia": "🇦🇺",
    "saudi arabia": "🇸🇦",
    "qatar": "🇶🇦",
    "iraq": "🇮🇶",
    "uzbekistan": "🇺🇿",
    # OFC
    "new zealand": "🇳🇿",
}


def _flag(team_name: str) -> str:
    """Country-flag emoji for a team name, or a soccer-ball fallback."""
    if not team_name:
        return "⚽"
    return _FLAGS.get(team_name.strip().lower(), "⚽")


def _already_tweeted(match_id: str) -> bool:
    """Idempotency guard — has this match already produced a recap row?"""
    try:
        rows = execute_query(
            "SELECT match_id FROM wc_match_tweets WHERE match_id = %s LIMIT 1",
            (match_id,),
        )
        return bool(rows)
    except Exception as e:
        # Lock check failed (table missing? DB down?) — fail closed:
        # don't post, don't crash settlement.
        log.warning("wc_match_recap: lock check failed for %s: %s", match_id, e)
        return True


def _load_match_with_prediction(match_id: str) -> Optional[dict]:
    """Fetch the settled WC fixture + the model's home/draw/away probs.

    Returns None when:
      - match doesn't exist
      - match isn't on the WC league (api_football_id=1)
      - match isn't finished / has no scores
      - no prediction row exists (model never ran)
    """
    try:
        rows = execute_query(
            """
            SELECT
                m.id::text          AS match_id,
                m.date::date        AS kickoff_date,
                m.score_home, m.score_away, m.result,
                ht.name             AS home_team,
                ta.name             AS away_team,
                MAX(CASE WHEN p.market = '1x2_home' THEN p.model_probability END)
                                    AS home_prob,
                MAX(CASE WHEN p.market = '1x2_draw' THEN p.model_probability END)
                                    AS draw_prob,
                MAX(CASE WHEN p.market = '1x2_away' THEN p.model_probability END)
                                    AS away_prob
            FROM matches m
            JOIN leagues l   ON l.id = m.league_id
            JOIN teams   ht  ON ht.id = m.home_team_id
            JOIN teams   ta  ON ta.id = m.away_team_id
            LEFT JOIN predictions p ON p.match_id = m.id
                AND p.market IN ('1x2_home', '1x2_draw', '1x2_away')
            WHERE m.id = %s
              AND l.api_football_id = 1
              AND m.status = 'finished'
              AND m.score_home IS NOT NULL
              AND m.score_away IS NOT NULL
            GROUP BY m.id, m.date, m.score_home, m.score_away, m.result,
                     ht.name, ta.name
            LIMIT 1
            """,
            (match_id,),
        )
    except Exception as e:
        log.warning("wc_match_recap: fetch failed for %s: %s", match_id, e)
        return None
    return rows[0] if rows else None


def _pick_label_and_pct(
    row: dict,
) -> Optional[tuple[str, int]]:
    """Return ("Brazil", 55) for the model's max-prob outcome, or None.

    None when the model has no 1X2 prediction for this fixture — caller
    should skip posting (we don't want a tweet that says "predicted at
    N/A").
    """
    home_p = row.get("home_prob")
    draw_p = row.get("draw_prob")
    away_p = row.get("away_prob")
    if home_p is None or draw_p is None or away_p is None:
        return None

    try:
        hp, dp, ap = float(home_p), float(draw_p), float(away_p)
    except (TypeError, ValueError):
        return None

    candidates = [
        (hp, row.get("home_team") or "home"),
        (dp, "draw"),
        (ap, row.get("away_team") or "away"),
    ]
    pct, label = max(candidates, key=lambda t: t[0])
    return label, int(round(pct * 100))


def _was_pick_correct(pick_label: str, row: dict) -> bool:
    """Did the model's max-prob pick match the actual result?"""
    home = (row.get("home_team") or "").strip()
    away = (row.get("away_team") or "").strip()
    result = row.get("result")  # 'home' | 'draw' | 'away'
    if pick_label == "draw":
        return result == "draw"
    if pick_label == home:
        return result == "home"
    if pick_label == away:
        return result == "away"
    return False


def _compose_tweet(row: dict) -> Optional[str]:
    """Compose the recap. Returns None when we have nothing to say."""
    pick = _pick_label_and_pct(row)
    if pick is None:
        return None
    pick_label, pick_pct = pick

    home = row.get("home_team") or "Home"
    away = row.get("away_team") or "Away"
    score_h = int(row["score_home"])
    score_a = int(row["score_away"])
    stage = _stage_label(row.get("kickoff_date"))
    url = f"{SITE_BASE}/matches/{row['match_id']}"
    tick = "✓" if _was_pick_correct(pick_label, row) else "✗"

    # URL counts as 23 — see Twitter docs. Reserve room for URL + 1 space.
    # 280 - 23 - 1 = 256 chars available for the prose portion.
    prose_budget = TWEET_MAX - TWITTER_URL_LENGTH - 1

    full_prose = (
        f"{_flag(home)} {home} {score_h}-{score_a} {_flag(away)} {away} "
        f"({stage}). OddsIntel predicted {pick_label} at {pick_pct}%. {tick}"
    )

    if len(full_prose) > prose_budget:
        # Drop the stage parenthetical first.
        full_prose = (
            f"{_flag(home)} {home} {score_h}-{score_a} {_flag(away)} {away}. "
            f"OddsIntel predicted {pick_label} at {pick_pct}%. {tick}"
        )

    if len(full_prose) > prose_budget:
        # Last-ditch: truncate the prose hard. The tick + percentage are
        # the load-bearing bits, so chop from the start (team names).
        full_prose = full_prose[: prose_budget - 1] + "…"

    return f"{full_prose} {url}"


def _record_tweet(match_id: str, tweet_id: str, text: str) -> None:
    """Persist the (match_id → tweet_id) audit row. Best-effort."""
    try:
        execute_write(
            """
            INSERT INTO wc_match_tweets (match_id, tweet_id, tweet_text)
            VALUES (%s, %s, %s)
            ON CONFLICT (match_id) DO NOTHING
            """,
            (match_id, tweet_id, text),
        )
    except Exception as e:
        log.warning(
            "wc_match_recap: audit insert failed for %s (tweet %s): %s",
            match_id, tweet_id, e,
        )


def post_wc_match_recap(match_id: str) -> bool:
    """Compose + post a recap tweet for one finished WC fixture.

    Returns True iff a tweet was actually posted (and the audit row
    written). Returns False on any skip path (already tweeted, missing
    prediction, missing creds, HTTP failure). NEVER raises — settlement
    must be able to call this in a tight loop without try/except.
    """
    if not match_id:
        return False

    if _already_tweeted(match_id):
        return False

    row = _load_match_with_prediction(match_id)
    if row is None:
        # Not a WC match, or not finished, or no prediction — silent skip.
        return False

    text = _compose_tweet(row)
    if text is None:
        log.info("wc_match_recap: no prediction for %s, skipping tweet",
                 match_id)
        return False

    tweet_id = post_tweet(text)
    if not tweet_id:
        # Either creds missing (silent, expected in dev) or HTTP error
        # (already logged inside post_tweet). Either way: no audit row,
        # so a future settlement run can re-attempt once creds land.
        return False

    _record_tweet(match_id, tweet_id, text)
    log.info("wc_match_recap: posted tweet %s for match %s",
             tweet_id, match_id)
    return True


__all__ = ["post_wc_match_recap"]
