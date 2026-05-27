"""
Coolbet automated bet placer.

Flow:
  1. Query DB for today's simulated_bets with edge > threshold and no real_bet yet.
  2. Find each match on Coolbet: search by home team name (v2 search endpoint),
     fuzzy-match results to confirm home+away pair.  Falls back to loading the
     full fo-category tree if search returns nothing.
  3. Get market details via fo-market/sidebets → betOfferId + outcomeId.
  4. Verify current odds via sb-odds/current/fo → also gets oddsId UUID needed for bet.
  5. Place bet via POST /s/bets/bets.
  6. Write to real_bets table.

Required .env:
    COOLBET_USER, COOLBET_PASS, COOLBET_IMPERVA_COOKIES   (see coolbet_session.py)
    COOLBET_STAKE           — stake per bet in EUR (default: 10.0)
    COOLBET_MIN_EDGE        — minimum edge% to place (default: 0.03)
    COOLBET_MIN_REMAINING_EDGE — minimum edge at placement price; rows below this are
                                 not written to real_bets (default: 0.0 = skip only -EV)
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rapidfuzz import fuzz, process as rfprocess
from workers.api_clients.db import execute_query
from workers.api_clients.supabase_client import store_coolbet_odds_snapshot, store_real_bet
from workers.automation.coolbet_session import CoolbetSession
from workers.notify.telegram import send_telegram

log = logging.getLogger(__name__)

_SEARCH_URL    = "https://www.coolbet.com/s/sbgate/sports/search/v2"
_CATEGORY_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_SIDEBETS_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-market/sidebets"
_ODDS_URL      = "https://www.coolbet.com/s/sb-odds/odds/current/fo"
_BET_URL       = "https://www.coolbet.com/s/bets/bets"

_FOOTBALL_CATEGORY_ID = 62
_DEFAULT_STAKE   = float(os.getenv("COOLBET_STAKE",        "10.0"))
_MIN_EDGE        = float(os.getenv("COOLBET_MIN_EDGE",      "0.03"))
_ODDS_TOLERANCE  = float(os.getenv("COOLBET_ODDS_TOLERANCE","0.05"))
# REAL-BETS-EDGE-FORMULA-FIX (2026-05-24): supersedes the old fixed-%
# `_ODDS_TOLERANCE` slippage gate in the main placement path. We now gate
# on edge at the placement price (additive, same convention as the bot).
# Default 0.0 = skip only strictly -EV bets; raise to e.g. 0.02 to also
# skip near-zero edge. `_ODDS_TOLERANCE` kept for `get_live_odds_and_id`
# (unused but live in legacy flow).
_MIN_REMAINING_EDGE = float(os.getenv("COOLBET_MIN_REMAINING_EDGE", "0.0"))
_FUZZY_THRESHOLD = 70


# COOLBET-SAFETY-GUARDRAILS (2026-05-20): instances live for one invocation of
# place_all_bets and track state across the per-bet loop (rate limit + total
# stake). Daemon constructs from CLI flags and passes via place_all_bets kwargs.
# All limits are optional (None = disabled) so existing callers keep working.
class PlacementGuard:
    def __init__(
        self,
        *,
        use_kelly_stake: bool = False,
        fixed_stake: float | None = None,         # overrides _DEFAULT_STAKE when not using Kelly
        max_stake_per_bet: float | None = None,   # absolute cap (clamps Kelly or fixed)
        max_bets_per_hour: int | None = None,     # rolling 60-min rate limit
        max_total_stake: float | None = None,     # cumulative session stake cap
        max_edge_pct: float | None = None,        # refuse absurd-edge bets (model bug guard)
        require_confirm: bool = False,            # y/n per bet (execute mode only)
        bot_filter: list[str] | None = None,      # only place bets from these bots
    ):
        self.use_kelly_stake     = use_kelly_stake
        self.fixed_stake         = fixed_stake
        self.max_stake_per_bet   = max_stake_per_bet
        self.max_bets_per_hour   = max_bets_per_hour
        self.max_total_stake     = max_total_stake
        self.max_edge_pct        = max_edge_pct
        self.require_confirm     = require_confirm
        self.bot_filter          = set(bot_filter) if bot_filter else None
        # Tracking
        self._placement_times: list[float] = []   # for rate limit
        self._total_stake: float = 0.0            # for session-stake cap

    def stake_for(self, bet: dict) -> float:
        """Return stake to use for `bet`, clamped to max_stake_per_bet."""
        if self.use_kelly_stake:
            base = float(bet.get("model_stake") or 0)
            if base <= 0:
                base = _DEFAULT_STAKE  # Kelly was zero/missing — fall back to fixed
        else:
            base = self.fixed_stake if self.fixed_stake is not None else _DEFAULT_STAKE
        if self.max_stake_per_bet is not None:
            base = min(base, self.max_stake_per_bet)
        return round(max(base, 1.0), 2)  # never go below €1

    def can_place(self, bet: dict, stake: float) -> tuple[bool, str]:
        """Pre-flight check before each placement attempt. Returns (allowed, reason)."""
        bot = bet.get("bot_name") or ""
        if self.bot_filter and bot not in self.bot_filter:
            return False, f"bot {bot!r} not in --bot-filter"

        # edge_percent stored as decimal fraction (0.09 = 9%); max_edge_pct
        # is user-supplied percentage (e.g. 25). Compare in percentage units.
        edge_pct = float(bet.get("edge_percent") or 0) * 100
        if self.max_edge_pct is not None and edge_pct > self.max_edge_pct:
            return False, f"edge {edge_pct:.1f}% > --max-edge-pct {self.max_edge_pct} (likely model bug)"

        if self.max_total_stake is not None:
            if (self._total_stake + stake) > self.max_total_stake:
                return False, (
                    f"placing this bet would exceed --max-total-stake "
                    f"(would be €{self._total_stake + stake:.2f} vs cap €{self.max_total_stake:.2f})"
                )

        if self.max_bets_per_hour is not None:
            cutoff = time.time() - 3600
            self._placement_times = [t for t in self._placement_times if t >= cutoff]
            if len(self._placement_times) >= self.max_bets_per_hour:
                return False, (
                    f"--max-bets-per-hour {self.max_bets_per_hour} hit "
                    f"(placed {len(self._placement_times)} in last 60 min)"
                )

        return True, ""

    def record_placement(self, stake: float) -> None:
        """Call after a successful placement (or even a recorded one) so the
        rate / total-stake counters track reality."""
        self._placement_times.append(time.time())
        self._total_stake += stake

    def prompt_confirm(self, bet: dict, stake: float, odds: float) -> bool:
        """If --require-confirm, prompt y/n in the terminal. Returns True if
        the user accepts. Non-interactive sessions (e.g. tmux send-keys with no
        TTY) return False to avoid placing without confirmation."""
        if not self.require_confirm:
            return True
        if not sys.stdin.isatty():
            log.warning("--require-confirm set but stdin is not a TTY — skipping placement to be safe")
            return False
        msg = (
            f"\n  CONFIRM: place €{stake:.2f} on "
            f"{bet['home_team']} vs {bet['away_team']} | "
            f"{bet['market']} {bet['selection']} @ {odds:.3f}? [y/N] "
        )
        try:
            ans = input(msg).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes")


# ── Market / selection mapping ────────────────────────────────────────────────
#
# Our market label → patterns to match Kambi criterion labels (case-insensitive
# substring match against the criterion's englishLabel).

_MARKET_CRITERION: dict[str, list[str]] = {
    "1X2":           ["full time", "match result", "match"],
    "O/U":           ["over/under", "total goals", "goal line"],
    "BTTS":          ["both teams", "goal or no goal", "btts"],
    "double_chance": ["double chance"],
    "asian_handicap":["asian handicap"],
    "draw_no_bet":   ["draw no bet", "dnb"],
}

# Our selection label → Coolbet outcome label (for non-contextual markets)
_SELECTION_OUTCOME: dict[str, str] = {
    "Home": "1",  "Draw": "X",  "Away": "2",
    "Yes":  "Yes","No":   "No",
    "1X":   "1X", "X2":   "X2", "12":   "12",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def load_qualified_bets() -> list[dict]:
    """Return today's simulated_bets qualifying for automated placement.

    COOLBET-EDGE-UNITS-FIX (2026-05-20): `simulated_bets.edge_percent` is
    stored as a DECIMAL FRACTION (0.09 = 9%), not a percentage value (despite
    the column name). The original SQL used `>= %s * 100` which inadvertently
    compared decimal to percentage units — so e.g. _MIN_EDGE=0.03 → filter
    `>= 3.0` → real values like 0.05/0.09/0.20 were ALL rejected → placer
    silently never placed any auto-bet since shipping. Fixed to compare in
    decimal units throughout."""

    # ── Diagnostic: show what each filter removes ─────────────────────────
    diag = execute_query(
        """
        SELECT
            COUNT(*)                                                        AS total_pending_today,
            COUNT(*) FILTER (WHERE m.date > NOW())                          AS not_kicked_off,
            COUNT(*) FILTER (WHERE m.date > NOW()
                               AND sb.edge_percent >= %s)                   AS pass_edge,
            COUNT(*) FILTER (WHERE m.date > NOW()
                               AND sb.edge_percent >= %s
                               AND NOT EXISTS (
                                   SELECT 1 FROM real_bets rb
                                   WHERE rb.match_id  = sb.match_id
                                     AND rb.market    = sb.market
                                     AND rb.selection = sb.selection
                                     AND DATE(rb.placed_at) = CURRENT_DATE
                               ))                                           AS pass_no_real_bet
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result    = 'pending'
          AND DATE(m.date) = CURRENT_DATE
        """,
        (_MIN_EDGE, _MIN_EDGE),
    )
    if diag:
        d = diag[0]
        log.info(
            "Today's simulated_bets: %s pending | %s not kicked off | "
            "%s pass edge≥%.0f%% | %s pass no-real-bet → qualifying",
            d["total_pending_today"], d["not_kicked_off"],
            d["pass_edge"], _MIN_EDGE * 100,
            d["pass_no_real_bet"],
        )

    # Show edge values of the bets that failed the edge filter
    if diag and int(diag[0]["not_kicked_off"] or 0) > 0 and int(diag[0]["pass_edge"] or 0) == 0:
        below_edge = execute_query(
            """
            SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                   ht.name AS home_team, at2.name AS away_team,
                   sb.market, sb.selection,
                   ROUND(sb.edge_percent::numeric, 2) AS edge_pct,
                   sb.odds_at_pick
            FROM simulated_bets sb
            JOIN matches m   ON m.id   = sb.match_id
            JOIN teams   ht  ON ht.id  = m.home_team_id
            JOIN teams   at2 ON at2.id = m.away_team_id
            WHERE sb.result    = 'pending'
              AND DATE(m.date) = CURRENT_DATE
              AND m.date       > NOW()
            ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
            """,
            (),
        )
        if below_edge:
            log.info("Bets below edge threshold (top edges shown):")
            for r in below_edge:
                # edge_pct stored as decimal — display × 100 for the %-suffixed log line.
                log.info("  %s vs %s | %s %s  edge=%.2f%%  @ %s",
                         r["home_team"], r["away_team"],
                         r["market"], r["selection"],
                         float(r["edge_pct"]) * 100, r["odds_at_pick"])

    # ── Blocked by real_bets: show which ones were already placed ─────────
    already = execute_query(
        """
        SELECT
            ht.name AS home_team, at2.name AS away_team,
            sb.market, sb.selection,
            ROUND(sb.edge_percent::numeric, 4) AS edge_pct,
            rb.actual_odds, rb.placed_at::time AS placed_time
        FROM simulated_bets sb
        JOIN matches m   ON m.id   = sb.match_id
        JOIN teams   ht  ON ht.id  = m.home_team_id
        JOIN teams   at2 ON at2.id = m.away_team_id
        JOIN real_bets rb ON rb.match_id  = sb.match_id
                          AND rb.market    = sb.market
                          AND rb.selection = sb.selection
                          AND DATE(rb.placed_at) = CURRENT_DATE
        WHERE sb.result    = 'pending'
          AND DATE(m.date) = CURRENT_DATE
          AND m.date       > NOW()
          AND sb.edge_percent >= %s
        ORDER BY sb.edge_percent DESC
        """,
        (_MIN_EDGE,),
    )
    if already:
        log.info("Skipped — real bet already placed today:")
        for r in already:
            log.info("  ✓ already placed  %s vs %s | %s %s  edge=%.2f%%  @ %s  (%s)",
                     r["home_team"], r["away_team"], r["market"], r["selection"],
                     float(r["edge_pct"]) * 100, r["actual_odds"], r["placed_time"])

    rows = execute_query(
        """
        SELECT * FROM (
          SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
              sb.id             AS simulated_bet_id,
              sb.match_id,
              sb.market,
              sb.selection,
              sb.odds_at_pick   AS model_odds,
              sb.edge_percent,
              sb.calibrated_prob,
              sb.model_probability,
              sb.kelly_fraction,
              sb.stake          AS model_stake,
              sb.bot_id,
              b.name            AS bot_name,
              ht.name           AS home_team,
              at2.name          AS away_team,
              m.date            AS match_date,
              COUNT(*) OVER (PARTITION BY sb.match_id, sb.market, sb.selection) AS bot_count
          FROM simulated_bets sb
          JOIN bots          b   ON b.id   = sb.bot_id
          JOIN matches       m   ON m.id   = sb.match_id
          JOIN teams         ht  ON ht.id  = m.home_team_id
          JOIN teams         at2 ON at2.id = m.away_team_id
          WHERE sb.result          = 'pending'
            AND sb.combo_legs IS NULL                    -- COMBO-SINGLES-SEPARATION (2026-05-23): combos handled by load_qualified_combo_bets()
            AND DATE(m.date)       = CURRENT_DATE
            AND m.date             > NOW()
            AND sb.edge_percent    >= %s
            AND NOT EXISTS (
                SELECT 1 FROM real_bets rb
                WHERE rb.match_id  = sb.match_id
                  AND rb.market    = sb.market
                  AND rb.selection = sb.selection
                  AND DATE(rb.placed_at) = CURRENT_DATE
            )
          ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
        ) q
        ORDER BY q.match_date ASC, q.edge_percent DESC
        """,
        (_MIN_EDGE,),
    )
    results = [dict(r) for r in rows]
    for r in results:
        if int(r.get("bot_count", 1)) > 1:
            log.info("  %s vs %s | %s %s — %s bots agree, using highest-edge row",
                     r["home_team"], r["away_team"], r["market"], r["selection"],
                     r["bot_count"])
    return results


def load_qualified_combo_bets() -> list[dict]:
    """COMBO-PLACER (2026-05-23): qualifying combo simulated_bets.

    Returns combo bets whose:
      - simulated_bet.result = 'pending'
      - combo_legs IS NOT NULL
      - edge_percent >= _MIN_EDGE
      - ALL leg matches have a future kickoff (no leg has started yet)
      - No existing real_bet uses this simulated_bet_id today

    Each row carries combo_legs (already JSONB), system_type, combo_size,
    bot info, stake, and edge_percent so the placer loop can resolve each
    leg's Coolbet outcome and write a single multi-leg real_bet.
    """
    rows = execute_query(
        """
        SELECT sb.id          AS simulated_bet_id,
               sb.match_id    AS placeholder_match_id,
               sb.combo_legs,
               sb.combo_size,
               sb.system_type,
               sb.odds_at_pick AS combined_model_odds,
               sb.edge_percent,
               sb.stake        AS model_stake,
               sb.bot_id,
               b.name          AS bot_name
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.result = 'pending'
          AND sb.combo_legs IS NOT NULL
          AND sb.edge_percent >= %s
          AND NOT EXISTS (
              SELECT 1 FROM real_bets rb
              WHERE rb.simulated_bet_id = sb.id
                AND DATE(rb.placed_at) = CURRENT_DATE
          )
          AND NOT EXISTS (
              -- any leg already kicked off → drop the combo, can't place it
              SELECT 1
              FROM jsonb_array_elements(sb.combo_legs) AS leg
              JOIN matches m ON m.id = (leg->>'match_id')::uuid
              WHERE m.date <= NOW()
                 OR DATE(m.date) <> CURRENT_DATE
          )
        ORDER BY sb.edge_percent DESC
        """,
        (_MIN_EDGE,),
    )
    return [dict(r) for r in rows]


# ── Coolbet event fetcher ─────────────────────────────────────────────────────

def fetch_coolbet_events(session: CoolbetSession) -> list[dict]:
    """
    Fetch all pre-match football events from fo-category.
    Returns flat list of {id, home, away, start, bet_offers}.

    bet_offers: [{id, criterion_label, outcomes: [{id, label, odds_decimal}]}]

    NB: legacy single-call sweep at categoryId=62 (football root) — Coolbet
    deprecated this in early 2026 and now returns 404. Kept as a fallback
    for old call sites; new code should use fetch_coolbet_leagues +
    fetch_events_for_league instead.
    """
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": _FOOTBALL_CATEGORY_ID,
        "lang": "en",
        "layout": "EUROPEAN",
    })
    if resp.status_code != 200:
        raise RuntimeError(f"fo-category {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    events: list[dict] = []
    _walk_group(data.get("group") or data, events)
    log.info("Fetched %d Coolbet football events from fo-category", len(events))
    return events


# ── League-based ingest (COOLBET-LEAGUE-INGEST 2026-05-20) ────────────────────
#
# Single category=62 sweep is dead; per-match search has false negatives.
# Two-endpoint architecture replaces both:
#
#   1. POST /s/sports/category/order/explicit/category-page-leagues
#        body: {sportCategoryId: 62, country: "EE", locale: "et"}
#        returns: list of {id, name, fullSlug, ...} — ~136 leagues
#
#   2. GET /s/sbgate/sports/fo-category/?categoryId=<league_id>
#        returns: [{matches: [{id, name, home_team_name, away_team_name, start}]}]
#        — all current/upcoming events in that league, no fuzzy matching needed

_LEAGUES_URL = "https://www.coolbet.com/s/sports/category/order/explicit/category-page-leagues"
_LEAGUES_CACHE_PATH = Path(__file__).parent / "coolbet_leagues_cache.json"


def _load_leagues_cache() -> list[dict]:
    """Fallback: read the static leagues list cached in the repo."""
    try:
        import json as _json
        with open(_LEAGUES_CACHE_PATH, "r") as f:
            data = _json.load(f)
        return [
            {"id": int(e["id"]),
             "name": e.get("name") or "",
             "fullSlug": e.get("fullSlug") or "",
             "sportCategoryId": e.get("sportCategoryId")}
            for e in data if e.get("id")
        ]
    except Exception as e:
        log.warning("Could not load leagues cache from %s: %s", _LEAGUES_CACHE_PATH, e)
        return []


def fetch_coolbet_leagues(session: CoolbetSession) -> list[dict]:
    """Return the full list of Coolbet football leagues exposed to the EE locale.
    Each entry: {id, name, fullSlug, sportCategoryId}. ~132 leagues.

    Tries the live POST endpoint first; falls back to the static cache at
    `coolbet_leagues_cache.json` if Imperva 403's (which it does for our
    Python session as of 2026-05-20 — endpoint has stricter TLS fingerprinting
    than other Coolbet API calls). Cache is curated from the user's browser
    curl and refreshed manually when leagues change (rarely)."""
    resp = session.post(_LEAGUES_URL, json={
        "sportCategoryId": _FOOTBALL_CATEGORY_ID,
        "country":         "EE",
        "locale":          "et",
    })
    if resp.status_code == 200:
        payload = resp.json()
        if isinstance(payload, list) and payload:
            log.info("fetch_coolbet_leagues — live endpoint returned %d leagues", len(payload))
            return [
                {"id": int(e["id"]),
                 "name": e.get("name") or "",
                 "fullSlug": e.get("fullSlug") or "",
                 "sportCategoryId": e.get("sportCategoryId")}
                for e in payload if e.get("id")
            ]
    # Live endpoint failed (403 Imperva, network, schema change) — fall back.
    log.info("fetch_coolbet_leagues — live endpoint returned %d, falling back to cache",
             resp.status_code)
    return _load_leagues_cache()


def fetch_events_for_league(
    session: CoolbetSession, league_id: int, league_slug: str | None = None,
) -> list[dict]:
    """Return all matches in one Coolbet league.

    Each match dict has at minimum: {id, name, home_team_name, away_team_name,
    match_start, status}. Status='OPEN' = pre-match or live; others (closed,
    etc.) are skipped by caller.

    LEAGUE-EVENTS-PARAMS-FIX (2026-05-20): replicates the browser's exact
    fo-category request shape — `language=et`, `isMobile=0`, `limit=6`, and
    a league-specific referer header. Without these Imperva 403's; with them
    the endpoint works. `league_slug` is optional but improves the referer.
    """
    extra_headers: dict[str, str] = {}
    if league_slug:
        extra_headers["referer"] = f"https://www.coolbet.com/et/sport/{league_slug}"
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": league_id,
        "country":    "EE",
        "isMobile":   0,
        "language":   "et",  # Estonian locale to match browser fingerprint
        "layout":     "EUROPEAN",
        "limit":      6,
    }, headers=extra_headers or None)
    if resp.status_code != 200:
        log.debug("fo-category(league=%d) returned %d", league_id, resp.status_code)
        return []
    data = resp.json()
    matches: list[dict] = []
    # Response is a list of category objects, each with a matches[] array.
    for cat in (data if isinstance(data, list) else [data]):
        for m in (cat.get("matches") or []):
            if not m.get("id"):
                continue
            matches.append({
                "id":         int(m["id"]),
                "home":       (m.get("home_team_name") or "").strip(),
                "away":       (m.get("away_team_name") or "").strip(),
                "start":      m.get("match_start") or m.get("start"),
                "status":     m.get("status"),
                "name":       m.get("name"),
            })
    return matches


def _walk_group(group: dict, out: list) -> None:
    for ev in group.get("events") or []:
        parsed = _parse_event(ev)
        if parsed:
            out.append(parsed)
    for sub in group.get("groups") or []:
        _walk_group(sub, out)


def _parse_event(ev: dict) -> dict | None:
    ev_id = ev.get("id")
    if not ev_id:
        return None

    name = ev.get("name") or ""
    participants = ev.get("participants") or []
    if len(participants) >= 2:
        home = participants[0].get("name", "")
        away = participants[1].get("name", "")
    elif " - " in name:
        home, away = name.split(" - ", 1)
    else:
        return None

    bet_offers = []
    for bo in ev.get("betOffers") or []:
        if bo.get("suspended"):
            continue
        criterion = bo.get("criterion") or {}
        label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()
        outcomes = []
        for oc in bo.get("outcomes") or []:
            raw = oc.get("odds") or 0
            dec = raw / 1000.0 if isinstance(raw, int) and raw > 100 else float(raw or 0)
            outcomes.append({
                "id":           oc.get("id"),
                "label":        (oc.get("englishLabel") or oc.get("label") or "").strip(),
                "odds_decimal": dec,
            })
        if outcomes:
            bet_offers.append({
                "id":              bo.get("id"),
                "criterion_label": label,
                "outcomes":        outcomes,
            })

    return {
        "id":         ev_id,
        "home":       home.strip(),
        "away":       away.strip(),
        "start":      ev.get("start"),
        "bet_offers": bet_offers,
    }


class CoolbetSearchBlocked(Exception):
    """Coolbet /search/v2 refused the request (non-200).

    Why: a dead `cbauth` JWT or Incapsula bot challenge returns 4xx/5xx —
    previously swallowed at DEBUG, making 18 doomed searches look like 18
    genuine no-coverage misses (silent-failure trap, 2026-05-26).
    Raising forces the placer to stop after the first failure with a clear
    "refresh COOLBET_MANUAL_JWT" signal.
    """


def _do_search(session: CoolbetSession, query: str) -> list[dict]:
    """Single search call. Returns parsed event candidates (possibly empty).

    Raises CoolbetSearchBlocked on non-200 so a dead session cannot
    masquerade as a string of "no event" misses.
    """
    resp = session.get(_SEARCH_URL, params={
        "search":   query,
        "country":  "EE",
        "language": "en",
        "layout":   "EUROPEAN",
    })
    if resp.status_code != 200:
        body_snip = (resp.text or "")[:200].replace("\n", " ")
        log.warning("Search HTTP %d for query %r — body: %s",
                    resp.status_code, query, body_snip or "<empty>")
        raise CoolbetSearchBlocked(
            f"search/v2 returned HTTP {resp.status_code} for query {query!r}. "
            f"Likely Incapsula challenge or dead cbauth JWT — re-Smart-ID in "
            f"browser and refresh COOLBET_MANUAL_JWT, then re-run."
        )
    data = resp.json()
    raw_events = (
        data if isinstance(data, list)
        else data.get("events") or data.get("results") or []
    )
    return [e for e in (_parse_event(ev) for ev in raw_events) if e]


def search_coolbet_event(
    session: CoolbetSession, home: str, away: str,
    match_date: datetime | None = None,
) -> dict | None:
    """
    Multi-pass team-name search against /s/sbgate/sports/search/v2.

    SEARCH-MULTIPASS (2026-05-20): single-word search was missing real
    European top-flight matches (Ajax-Groningen, Wolfsburg-Paderborn,
    Hammarby etc.) that Coolbet does cover. Now widens progressively
    until a fuzzy match passes the threshold:

        1. home first 3 letters    (short prefix, broad result set)
        2. home first 4 letters
        3. home first 5 letters
        4. away first 3 letters    (fallback — useful when home name is generic)
        5. away first 4 letters
        6. away first 5 letters
        7. full home name (whitespace-stripped)

    Aggregates all unique candidates across passes and fuzzy-matches once.
    Stops early as soon as a match clears _FUZZY_THRESHOLD.
    """
    def _prefix(name: str, n: int) -> str | None:
        # Strip whitespace + take first n alphabetic chars (skip "FC ", "SC ", etc.)
        clean = "".join(c for c in (name or "") if c.isalpha())
        return clean[:n] if len(clean) >= n else None

    queries: list[str] = []
    seen: set[str] = set()
    for q in (
        _prefix(home, 3), _prefix(home, 4), _prefix(home, 5),
        _prefix(away, 3), _prefix(away, 4), _prefix(away, 5),
        home.strip() if home else None,
        away.strip() if away else None,
    ):
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)

    if not queries:
        return None

    # Run progressively. Stop as soon as fuzzy match clears threshold.
    aggregate: dict[int, dict] = {}  # de-dup by event id
    for q in queries:
        cands = _do_search(session, q)
        for ev in cands:
            try:
                aggregate[int(ev["id"])] = ev
            except (TypeError, ValueError, KeyError):
                continue
        match = fuzzy_match_event(home, away, list(aggregate.values()), match_date) if aggregate else None
        if match:
            log.info("Search matched '%s vs %s' → Coolbet '%s vs %s' (id=%s) "
                     "via query=%r (queries tried=%d, candidates=%d)",
                     home, away, match["home"], match["away"], match["id"],
                     q, queries.index(q) + 1, len(aggregate))
            return match

    # All queries exhausted without a fuzzy hit
    best = "—"
    if aggregate:
        sample = next(iter(aggregate.values()))
        best = f"{sample['home']} vs {sample['away']}"
    log.info("Search exhausted %d queries for '%s vs %s' — best candidate '%s' "
             "(%d unique events scanned)",
             len(queries), home, away, best, len(aggregate))
    return None


_FO_MATCH_URL = "https://www.coolbet.com/s/sbgate/sports/fo-match"


def fetch_sidebets(session: CoolbetSession, match_id: int) -> list[dict]:
    """
    GET /s/sbgate/sports/fo-market/sidebets?matchId=... for a single match.
    Returns bet_offers in same format as _parse_event.

    SIDEBETS-PARAMS-FIX (2026-05-20): live capture from Coolbet DevTools shows
    the site sends `marketTypeGroupId=15` + `matchStatus=OPEN` — without them
    the endpoint silently returns empty `betOffers`. Group 15 appears to be
    "all open side markets" (OU/BTTS/AH/etc.). The main 1X2 market comes
    from fetch_main_markets() (POST /fo-match), not from here.
    """
    resp = session.get(_SIDEBETS_URL, params={
        "matchId":            match_id,
        "country":            "EE",
        "language":           "en",
        "layout":             "EUROPEAN",
        "marketTypeGroupId":  15,
        "matchStatus":        "OPEN",
    })
    if resp.status_code != 200:
        log.warning("sidebets %d returned %d", match_id, resp.status_code)
        return []
    return _parse_bet_offers_payload(resp.json().get("betOffers") or [])


def fetch_main_markets(session: CoolbetSession, match_ids: list[int]) -> dict[int, list[dict]]:
    """
    POST /s/sbgate/sports/fo-match — main markets (1X2, OU 2.5, etc.) per match.
    Replacement for the now-404 fo-category endpoint discovered 2026-05-20.
    Body: {language, country, layout, locale, matchIds: [...]}
    Returns: {match_id: [bet_offer, ...]} in same shape as fetch_sidebets.

    Batches well — caller can pass many matchIds at once.
    """
    if not match_ids:
        return {}
    body = {
        "language": "en",
        "country":  "EE",
        "layout":   "EUROPEAN",
        "locale":   "en",
        "matchIds": [str(mid) for mid in match_ids],
    }
    resp = session.post(_FO_MATCH_URL, json=body)
    if resp.status_code != 200:
        log.warning("fo-match POST %s returned %d: %s",
                    match_ids[:3], resp.status_code, resp.text[:200])
        return {}
    payload = resp.json()
    # Response shape varies. Either a list of {matchId, betOffers} OR a dict
    # keyed by matchId OR a single bag of betOffers. Handle all three.
    out: dict[int, list[dict]] = {}
    if isinstance(payload, list):
        for entry in payload:
            mid = int(entry.get("matchId") or entry.get("id") or 0)
            if mid:
                out[mid] = _parse_bet_offers_payload(entry.get("betOffers") or [])
    elif isinstance(payload, dict):
        if "betOffers" in payload and len(match_ids) == 1:
            out[int(match_ids[0])] = _parse_bet_offers_payload(payload["betOffers"])
        else:
            for k, v in payload.items():
                try:
                    mid = int(k)
                except (TypeError, ValueError):
                    continue
                bo = v.get("betOffers") if isinstance(v, dict) else (v if isinstance(v, list) else [])
                out[mid] = _parse_bet_offers_payload(bo or [])
    return out


def _parse_bet_offers_payload(raw_offers: list[dict]) -> list[dict]:
    """Shared parser — turns raw Coolbet betOffers list into the standard
    {id, criterion_label, outcomes:[...]} shape used by both sidebets and
    fo-match callers."""
    bet_offers = []
    for bo in raw_offers:
        if bo.get("suspended"):
            continue
        criterion = bo.get("criterion") or {}
        label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()
        outcomes = []
        for oc in bo.get("outcomes") or []:
            raw = oc.get("odds") or 0
            dec = raw / 1000.0 if isinstance(raw, int) and raw > 100 else float(raw or 0)
            outcomes.append({
                "id":           oc.get("id"),
                "label":        (oc.get("englishLabel") or oc.get("label") or "").strip(),
                "odds_decimal": dec,
            })
        if outcomes:
            bet_offers.append({
                "id":              bo.get("id"),
                "criterion_label": label,
                "outcomes":        outcomes,
            })
    return bet_offers


# ── Matching ──────────────────────────────────────────────────────────────────

_UNICODE_MAP = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'å': 'a', 'Å': 'A',
    'ö': 'o', 'Ö': 'O', 'ü': 'u', 'Ü': 'U',
    'ä': 'a', 'Ä': 'A', 'é': 'e', 'è': 'e',
    'ê': 'e', 'ë': 'e', 'à': 'a', 'â': 'a',
    'î': 'i', 'ï': 'i', 'ô': 'o', 'ù': 'u',
    'û': 'u', 'ç': 'c', 'ñ': 'n', 'æ': 'ae', 'Æ': 'Ae',
})


def _ascii(s: str) -> str:
    """Map European chars to ASCII equivalents for fuzzy matching (ø→o, å→a, etc.)."""
    return s.translate(_UNICODE_MAP)


# COOLBET-FUZZY-DATE-GUARD (2026-05-26): match team names AND kickoff date.
# Same-team double-headers (Reserve vs first team, women vs men, multiple
# legs on different days) were resolving to the wrong event because the
# fuzzy matcher only scored names. Reject any candidate whose kickoff is
# more than this many hours away from our DB match date.
_FUZZY_DATE_TOLERANCE_HOURS = 6


def _parse_iso_start(start: str | None) -> datetime | None:
    if not start:
        return None
    try:
        # Coolbet `start` is ISO-8601 with trailing Z; fromisoformat handles "+00:00".
        s = start.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def fuzzy_match_event(
    home: str, away: str, events: list[dict],
    match_date: datetime | None = None,
) -> dict | None:
    """Find the Coolbet event whose home+away names best match ours.

    COOLBET-SEARCH-LAVAL (2026-05-24): the previous whole-string
    token_set_ratio failed when our short club names ("Laval", "Rouen")
    matched against Coolbet's full names ("Stade Lavallois", "FC Rouen") —
    score 62 < threshold 70 because "Laval" and "Lavallois" don't share
    tokens. Per-team partial_ratio handles prefix/substring overlap
    correctly: partial_ratio("Laval", "Stade Lavallois") = 100 (Laval is
    inside Lavallois). We score each event by min(home_score, away_score)
    so a great home match can't paper over a bad away match.

    COOLBET-FUZZY-DATE-GUARD (2026-05-26): when `match_date` is supplied,
    only candidate events whose `start` is within ±6h are considered. Stops
    same-team-different-day false matches (e.g. Coolbet has Racing Club's
    first-team fixture tomorrow but not today's reserves fixture — names
    score 100 but the date is wrong).
    """
    if not events:
        return None
    query_home = _ascii(home)
    query_away = _ascii(away)
    if match_date is not None and match_date.tzinfo is None:
        match_date = match_date.replace(tzinfo=timezone.utc)
    tol_seconds = _FUZZY_DATE_TOLERANCE_HOURS * 3600

    best_event = None
    best_score = -1
    skipped_date = 0
    for ev in events:
        if match_date is not None:
            ev_start = _parse_iso_start(ev.get("start"))
            if ev_start is not None:
                if abs((ev_start - match_date).total_seconds()) > tol_seconds:
                    skipped_date += 1
                    continue
        ev_home = _ascii(ev.get("home") or "")
        ev_away = _ascii(ev.get("away") or "")
        # Each side can match either Coolbet's home or away (handles flipped fixtures).
        home_score = max(
            fuzz.partial_ratio(query_home, ev_home),
            fuzz.partial_ratio(query_home, ev_away),
        )
        away_score = max(
            fuzz.partial_ratio(query_away, ev_home),
            fuzz.partial_ratio(query_away, ev_away),
        )
        score = min(home_score, away_score)
        if score > best_score:
            best_score = score
            best_event = ev

    if best_event is None or best_score < _FUZZY_THRESHOLD:
        best_label = f"{best_event['home']} {best_event['away']}" if best_event else "—"
        log.info(
            "Fuzzy match FAILED for '%s vs %s' — best was '%s' (score %d < threshold %d, %d candidates rejected on date)",
            home, away, best_label, best_score, _FUZZY_THRESHOLD, skipped_date,
        )
        return None
    log.info(
        "Fuzzy matched '%s vs %s' → Coolbet '%s vs %s' (score %d, %d date-mismatched candidates skipped)",
        home, away, best_event["home"], best_event["away"], best_score, skipped_date,
    )
    return best_event


def _write_presence_marker_snapshot(
    match_id: str,
    markets: list[dict],
    odds_map: dict[int, dict],
    match_date,
) -> None:
    """Write one canonical odds_snapshot proving the event exists on Coolbet.

    Used in the no_market path so the frontend can distinguish "event present
    but doesn't offer this market" from "event not at Coolbet at all".
    Prefers 1X2 Home; falls back to the first parseable market+selection.
    Errors are swallowed — this is a UX hint, never a correctness path.
    """
    from workers.automation.coolbet_explorer import parse_market

    mins_to_ko: int | None = None
    if match_date is not None:
        if match_date.tzinfo is None:
            match_date = match_date.replace(tzinfo=timezone.utc)
        import math as _math
        mins_to_ko = -int(_math.ceil(
            (match_date - datetime.now(timezone.utc)).total_seconds() / 60
        ))

    chosen: tuple[str, str, float] | None = None  # (market, selection, odds)
    for mkt in markets:
        rows = parse_market(mkt, odds_map)
        for market, selection, odds, _hline in rows:
            if market == "1x2" and selection == "Home":
                chosen = (market, selection, odds)
                break
            if chosen is None:
                chosen = (market, selection, odds)
        if chosen and chosen[0] == "1x2":
            break

    if chosen is None:
        return
    try:
        from workers.api_clients.supabase_client import store_coolbet_odds_snapshot
        store_coolbet_odds_snapshot(match_id, chosen[0], chosen[1], chosen[2], mins_to_ko)
        log.debug("Presence marker snapshot written: %s %s %s %.3f",
                  match_id, chosen[0], chosen[1], chosen[2])
    except Exception as e:
        log.warning("Presence marker snapshot failed for %s: %s", match_id, e)


def find_market_outcome(
    bet_offers: list[dict], our_market: str, our_selection: str
) -> tuple[int | None, int | None, float | None]:
    """
    Return (bet_offer_id, outcome_id, odds_decimal) for our market+selection.

    our_market:    "1X2" | "O/U" | "BTTS" | "double_chance" | "asian_handicap" | "draw_no_bet"
    our_selection: e.g. "Home", "Over 2.5", "Home -1.25", "Yes", "1X"
    """
    patterns = _MARKET_CRITERION.get(our_market, [our_market.lower()])

    for bo in bet_offers:
        cl = bo["criterion_label"]
        if not any(pat in cl for pat in patterns):
            continue

        if our_market == "O/U":
            line = _extract_ou_line(our_selection)
            if line and str(line) not in cl and f"{line:.1f}" not in cl:
                continue
            side = "over" if our_selection.lower().startswith("over") else "under"
            for oc in bo["outcomes"]:
                if side in oc["label"].lower():
                    return bo["id"], oc["id"], oc["odds_decimal"]

        elif our_market == "asian_handicap":
            parts = our_selection.split()
            if len(parts) < 2:
                continue
            side = parts[0].lower()
            try:
                line = float(parts[1])
            except ValueError:
                continue
            if str(abs(line)) not in cl and f"{abs(line):.2f}" not in cl:
                continue
            for oc in bo["outcomes"]:
                if side in oc["label"].lower():
                    return bo["id"], oc["id"], oc["odds_decimal"]

        else:
            target = _SELECTION_OUTCOME.get(our_selection, our_selection)
            for oc in bo["outcomes"]:
                if oc["label"].lower() == target.lower():
                    return bo["id"], oc["id"], oc["odds_decimal"]
            for oc in bo["outcomes"]:
                if target.lower() in oc["label"].lower():
                    return bo["id"], oc["id"], oc["odds_decimal"]

    return None, None, None


def _extract_ou_line(selection: str) -> float | None:
    m = re.search(r"(\d+\.?\d*)", selection)
    return float(m.group(1)) if m else None


# ── Odds verification (also fetches oddsId) ───────────────────────────────────

def get_live_odds_and_id(
    session: CoolbetSession,
    market_id: int,
    outcome_id: int,
    model_odds: float,
) -> tuple[float | None, str | None]:
    """
    POST /s/sb-odds/odds/current/fo → return (current_decimal_odds, odds_uuid).
    Returns (None, None) if odds have dropped beyond tolerance or market not found.

    The odds_uuid (oddsId) is required in the bet placement payload.
    """
    resp = session.post(_ODDS_URL, json={
        "where": {"market_id": {"in": [market_id]}}
    })
    if resp.status_code != 200:
        log.warning("sb-odds/current/fo %d for market %s", resp.status_code, market_id)
        return None, None

    data = resp.json()
    # Response may be a list of rows or wrapped in a key
    rows = data if isinstance(data, list) else (
        data.get("data") or data.get("rows") or data.get("outcomes") or []
    )

    for row in rows:
        row_market = row.get("market_id") or row.get("marketId")
        if row_market and str(row_market) != str(market_id):
            continue
        row_outcome = row.get("outcome_id") or row.get("outcomeId") or row.get("id")
        if row_outcome and str(row_outcome) != str(outcome_id):
            continue

        raw = row.get("odds") or row.get("oddsDecimal") or 0
        live_odds = raw / 1000.0 if isinstance(raw, int) and raw > 100 else float(raw or 0)
        odds_uuid = row.get("odds_id") or row.get("oddsId") or row.get("id")

        if live_odds <= 1.0:
            log.warning("Live odds %.3f invalid for outcome %s", live_odds, outcome_id)
            return None, None

        drop = (model_odds - live_odds) / model_odds if model_odds > 0 else 0
        if drop > _ODDS_TOLERANCE:
            log.info(
                "Odds dropped: model=%.3f live=%.3f (%.1f%% > %.1f%% tolerance) — skipping",
                model_odds, live_odds, drop * 100, _ODDS_TOLERANCE * 100,
            )
            return None, None

        return live_odds, str(odds_uuid) if odds_uuid else None

    log.warning("Outcome %s not found in odds response for market %s", outcome_id, market_id)
    return None, None


# ── Bet placement ─────────────────────────────────────────────────────────────

def _place_bet_api(
    session: CoolbetSession,
    outcome_id: int,
    odds_uuid: str,
    stake: float,
    match_name: str,
    market_name: str,
    outcome_name: str = "",
) -> str:
    """
    POST /s/bets/bets — place a single bet.
    Returns the ticket_id (UUID string) on success. Raises on failure.

    Payload structure matches a captured browser bet (2026-05-20):
      ticketType: "single"
      foTranslationsByOutcomeId: {outcomeId: {matchName, marketName, outcomeName}}
        — outcomeName CAN be empty; browser sends "" and it works.
      language: "en"  (NOT "et" — Estonia site, but bet API still wants en)
      No `currency` / `acceptOddsChanges` keys — Coolbet's schema strict-rejects
        unknown fields with GenericBadRequestError("Invalid request") and no
        further detail. Send EXACTLY the keys the browser sends.
    """
    device_id = session._http.cookies.get("uuid", "")

    payload = {
        "ticketType": "single",
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "copiedFrom": None,
        "foTranslationsByOutcomeId": {
            str(outcome_id): {
                "matchName":   match_name,
                "marketName":  market_name,
                "outcomeName": outcome_name,
            }
        },
        "oddsFormat":       "DECIMAL",
        "layout":           "EUROPEAN",
        "isForceDuplicate": False,
        "deviceId":         device_id,
        "language":         "en",
        "bets": [{
            "stake": stake,
            "oddsIdByOutcomeId": {str(outcome_id): odds_uuid},
            "betbuilderMarkets": [],
        }],
    }

    headers = {
        "accept-language":    "en-GB,en-US;q=0.9,en;q=0.8,da;q=0.7",
        "priority":           "u=1, i",
        "referer":            "https://www.coolbet.com/en/sports/football",
    }
    resp = session.post(_BET_URL, json=payload, headers=headers)
    log.info("POST /s/bets/bets → %d  %s", resp.status_code, resp.text[:300])

    # Always dump payload + response so we can audit what Coolbet returned.
    # Critical for real-money calls: even on "success" the response shape may
    # not match our parser — if the bet landed but we crashed on parse,
    # the dump tells us the bet exists at Coolbet's side.
    try:
        import json as _json
        from pathlib import Path as _Path
        dump_dir = _Path.home() / ".coolbet-daemon"
        dump_dir.mkdir(exist_ok=True)
        suffix = "failed" if resp.status_code not in (200, 201) else "success"
        (dump_dir / f"last-placement-{suffix}.json").write_text(_json.dumps({
            "request": payload,
            "response_status": resp.status_code,
            "response_body": resp.text,
        }, indent=2))
    except Exception:
        pass

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Bet placement failed ({resp.status_code}): {resp.text}"
        )

    # Response shape is whatever Coolbet returns — could be a dict, list,
    # bare string (ticket UUID), or wrapped in {ticket: ...}. Walk through
    # likely shapes; if none match, return the whole body so we can see it.
    try:
        data = resp.json()
    except Exception:
        # Plain-text response — return as-is
        return resp.text.strip().strip('"')[:60]

    def _extract_ticket_id(obj) -> str | None:
        if isinstance(obj, str):
            return obj
        if isinstance(obj, list) and obj:
            return _extract_ticket_id(obj[0])
        if isinstance(obj, dict):
            for k in ("ticketId", "ticket_id", "id", "uuid"):
                v = obj.get(k)
                if isinstance(v, (str, int)):
                    return str(v)
            # Common nested shapes
            for k in ("ticket", "data", "result"):
                v = obj.get(k)
                if v is not None:
                    nested = _extract_ticket_id(v)
                    if nested:
                        return nested
        return None

    ticket_id = _extract_ticket_id(data)
    if ticket_id:
        return ticket_id
    # Last resort — return a JSON snippet so caller sees something useful
    # AND the bet is still recorded in our DB (since the POST succeeded).
    import json as _json
    return _json.dumps(data)[:120]


# ── Main ──────────────────────────────────────────────────────────────────────

def place_all_bets(
    record: bool = False,
    execute: bool = False,
    min_edge: float | None = None,
    *,
    guard: PlacementGuard | None = None,
) -> list[dict]:
    """
    Run the placement cycle in one of three modes:

        record=False, execute=False  →  dry-run: print candidates, no DB writes
        record=True,  execute=False  →  write real_bets only (replaces manual admin work)
        record=True,  execute=True   →  write real_bets + place bet at Coolbet API

    All modes are idempotent: already-recorded bets are filtered out by the
    NOT EXISTS check in load_qualified_bets().

    Args:
        record:   Write matching records to real_bets table.
        execute:  Also call the Coolbet API to place the bet (implies record=True).
        min_edge: Override COOLBET_MIN_EDGE for this run (fraction, e.g. 0.03).
    """
    if execute:
        record = True

    global _MIN_EDGE
    if min_edge is not None:
        _MIN_EDGE = min_edge

    session = CoolbetSession()
    pending = load_qualified_bets()

    if not pending:
        log.info("No qualifying bets found for today.")
        return []

    # COOLBET-SAFETY-GUARDRAILS: default guard uses Kelly stakes so manual
    # calls without an explicit guard still size correctly.
    if guard is None:
        guard = PlacementGuard(use_kelly_stake=True)

    log.info("Found %d qualifying simulated_bets to evaluate", len(pending))

    # fo-category fallback: loaded lazily if search misses
    _category_events: list[dict] | None = None

    # COOLBET-PLACER-NEW-SCHEMA (2026-05-20): Coolbet split markets and odds
    # into separate endpoints and dropped the old `criterion_label` field.
    # Per-bet flow now uses coolbet_explorer.fetch_match_markets +
    # fetch_odds_for_markets + resolve_placement_target, which return the new
    # markets shape with market_type_id/result_key/line + odds with odds_id UUID.
    from workers.automation.coolbet_explorer import (
        fetch_match_markets,
        fetch_odds_for_markets,
        resolve_placement_target,
    )

    results = []
    search_blocked = False
    for idx, bet in enumerate(pending):
        home       = bet["home_team"]
        away       = bet["away_team"]
        mkt        = bet["market"]
        sel        = bet["selection"]
        model_odds = float(bet["model_odds"])
        # edge_percent stored as decimal fraction (0.09 = 9%); ×100 for display.
        edge_pct   = float(bet["edge_percent"]) * 100
        label      = f"{home} vs {away} | {mkt} {sel} @ {model_odds:.3f} (edge {edge_pct:+.2f}%)"

        # 1. Find Coolbet event (date-guarded — see COOLBET-FUZZY-DATE-GUARD)
        match_date = bet.get("match_date")
        try:
            ev = search_coolbet_event(session, home, away, match_date)
        except CoolbetSearchBlocked as e:
            log.error("Coolbet search blocked — aborting singles loop after "
                      "%d/%d bets evaluated. %s", idx, len(pending), e)
            for rb in pending[idx:]:
                results.append({**rb, "outcome": "search_blocked",
                                "reason": "coolbet search HTTP-refused "
                                          "(dead JWT / Incapsula)"})
            search_blocked = True
            break
        if ev is None:
            if _category_events is None:
                try:
                    log.info("Search miss — loading full fo-category tree")
                    _category_events = fetch_coolbet_events(session)
                except Exception as e:
                    log.warning("fo-category unavailable (%s) — search-only", e)
                    _category_events = []
            ev = fuzzy_match_event(home, away, _category_events, match_date) if _category_events else None
        if ev is None:
            log.info("No Coolbet event for %s — skipping", label)
            results.append({**bet, "outcome": "no_event"})
            continue

        coolbet_match_id = int(ev["id"])

        # 2. Fetch markets + odds (new schema)
        markets  = fetch_match_markets(session, coolbet_match_id)
        odds_map = fetch_odds_for_markets(session, markets)

        # 3. Resolve our (market, selection) to (market_id, outcome_id, odds_id, odds)
        target = resolve_placement_target(markets, odds_map, mkt, sel)
        if target is None:
            available = [(m.get("name"), m.get("line")) for m in markets[:8]]
            log.info("Market %s/%s not found for %s (matchId=%s) — "
                     "available markets: %s",
                     mkt, sel, label, coolbet_match_id, available or "none")
            # COOLBET-NO-MARKET-PRESENCE (2026-05-25): even though this specific
            # market+selection failed, the event itself exists on Coolbet. Write
            # one canonical snapshot (1X2 home if available) so the frontend's
            # `matchIdsWithCoolbetEvent` set can correctly chip this as
            # "no_market" instead of the misleading "no_event" / "⚠ no match".
            _write_presence_marker_snapshot(str(bet["match_id"]), markets, odds_map,
                                            bet.get("match_date"))
            results.append({**bet, "outcome": "no_market"})
            continue
        bo_id, oc_id, odds_uuid, ev_odds = target

        # ── Snapshot Coolbet odds (all modes, including dry-run) ─────────────
        # Normalise market/selection to the canonical odds_snapshots format so
        # the frontend's bot-book-odds lookup (which normalises "o/u"/"under 3.5"
        # → "over_under_35"/"under") can find the row. The explorer's scheduled
        # snapshot job already stores in this format; the placer must match it.
        if ev_odds:
            from workers.automation.coolbet_explorer import _normalise_our_target
            norm = _normalise_our_target(mkt, sel)
            snap_market = norm[0] if norm[0] else mkt
            snap_sel    = norm[1] if norm[1] else sel
            match_date = bet.get("match_date")
            mins_to_ko = None
            if match_date:
                import math
                if match_date.tzinfo is None:
                    match_date = match_date.replace(tzinfo=timezone.utc)
                delta_mins = (match_date - datetime.now(timezone.utc)).total_seconds() / 60
                mins_to_ko = -int(math.ceil(delta_mins))  # negative = pre-match
            try:
                store_coolbet_odds_snapshot(
                    str(bet["match_id"]), snap_market, snap_sel, ev_odds, mins_to_ko
                )
                log.debug("Snapshot stored: %s %s %s %.3f (%s min to KO)",
                          home, snap_market, snap_sel, ev_odds, mins_to_ko)
            except Exception as e:
                log.warning("Failed to store Coolbet odds snapshot: %s", e)

        # COOLBET-SAFETY-GUARDRAILS: stake comes from the guard
        # (Kelly-derived or fixed, capped by --max-stake-per-bet).
        stake = guard.stake_for(bet)

        # ── DRY-RUN ──────────────────────────────────────────────────────────
        if not record:
            coolbet_odds_str = f"{ev_odds:.3f}" if ev_odds else "?"
            print(f"  [DRY-RUN] {label}  →  match={coolbet_match_id} "
                  f"market_id={bo_id} outcome_id={oc_id} odds={coolbet_odds_str} "
                  f"stake=€{stake:.2f}")
            results.append({**bet, "outcome": "dry_run",
                             "coolbet_match_id": coolbet_match_id,
                             "market_id": bo_id, "outcome_id": oc_id,
                             "ev_odds": ev_odds, "stake": stake})
            continue

        # ── RECORD / EXECUTE ─────────────────────────────────────────────────
        # SAFETY-GUARDRAILS pre-flight: bot filter, rate limit, session-stake
        # cap, absurd-edge guard.
        allowed, reason = guard.can_place(bet, stake)
        if not allowed:
            log.info("Skip %s — %s", label, reason)
            results.append({**bet, "outcome": "guard_skip", "reason": reason})
            continue

        # REAL-BETS-EDGE-FORMULA-FIX (2026-05-24): gate on edge at the
        # placement price, not slippage. Compute additive edge using the
        # bot's calibrated_prob (same formula as daily_pipeline_v2.py:2384
        # and store_real_bet). Skip the row entirely if the bet is no
        # longer above `_MIN_REMAINING_EDGE` — that mirrors what would
        # happen in execute mode (no Coolbet placement = no real_bets row)
        # and stops paper-tracking from being polluted with bets we never
        # would have taken.
        cal_prob = float(bet.get("calibrated_prob") or 0)
        if not cal_prob:
            cal_prob = float(bet.get("model_probability") or 0)
        live_odds = ev_odds
        live_edge = (cal_prob - 1.0 / ev_odds) if (cal_prob > 0 and ev_odds > 1.0) else None
        if live_edge is not None and live_edge < _MIN_REMAINING_EDGE:
            log.info(
                "Skip %s — edge at placement %.2f%% < %.2f%% min "
                "(pick %.3f → live %.3f)",
                label, live_edge * 100, _MIN_REMAINING_EDGE * 100,
                model_odds, ev_odds,
            )
            results.append({**bet, "outcome": "edge_eroded",
                             "live_odds": ev_odds, "live_edge": live_edge})
            continue

        ticket_id = None
        if execute and odds_uuid:
            # --require-confirm prompts y/n in the TTY (skipped if non-interactive)
            if not guard.prompt_confirm(bet, stake, live_odds):
                log.info("Skip %s — confirm declined / no TTY for --require-confirm", label)
                results.append({**bet, "outcome": "confirm_declined"})
                continue
            match_name = f"{ev['home']} - {ev['away']}"
            # Look up Coolbet's real marketName from markets data.
            # outcomeName is sent as "" to match a captured browser bet.
            cb_market_name = ""
            for _m in markets:
                if int(_m.get("id") or 0) != bo_id: continue
                cb_market_name = _m.get("name") or ""
                break
            try:
                ticket_id = _place_bet_api(
                    session, oc_id, odds_uuid, stake, match_name,
                    cb_market_name, "",
                )
                log.info("✓ Coolbet ticket placed: %s", ticket_id)
            except Exception as e:
                log.error("Coolbet placement failed for %s: %s — recording only", label, e)
        elif execute and not odds_uuid:
            log.warning("No oddsId UUID for %s — cannot place at Coolbet, leaving for manual placement", label)

        # DUPE-FIX-2 (scoped to --execute): if we tried to place at Coolbet and
        # got no ticket (no odds_uuid / odds dropped / placement error), skip
        # the real_bets write so we don't pollute the dataset with phantom
        # rows + block the same bet from manual placement via /admin/place.
        # --record mode never gets a ticket by design (it's the paper-trade
        # tracking workflow), so we MUST write the row in that mode.
        if execute and ticket_id is None:
            log.info("Skip real_bets write for %s — execute mode but no Coolbet ticket (manual placement still possible)", label)
            results.append({**bet, "outcome": "not_placed",
                             "reason": "no_ticket",
                             "live_odds": live_odds, "stake": stake})
            continue

        real_bet_id = store_real_bet(
            match_id=str(bet["match_id"]),
            market=mkt,
            selection=sel,
            bookmaker="Coolbet",
            captured_odds=float(bet.get("model_odds") or ev_odds),
            actual_odds=live_odds,
            stake=stake,
            bot_id=str(bet["bot_id"]),
            simulated_bet_id=str(bet["simulated_bet_id"]),
            notes=f"auto ticket={ticket_id} edge={edge_pct:+.2f}%",
        )
        # Track the placement against rate-limit + total-stake counters
        guard.record_placement(stake)
        log.info("✓ Placed %s  stake=€%.2f  ticket=%s real_bet=%s",
                 label, stake, ticket_id, real_bet_id)
        icon = "💸" if execute else "📝"
        mode_label = "<b>REAL MONEY</b> @ Coolbet" if execute else "paper (record-only)"
        send_telegram(
            f"{icon} {mode_label}\n"
            f"  <b>{bet.get('home_team','?')} vs {bet.get('away_team','?')}</b>\n"
            f"  {mkt} {sel} @ {live_odds:.3f}\n"
            f"  €{stake:.2f}  ·  edge {edge_pct:+.1f}%  ·  bot {bet.get('bot_name','?')}\n"
            f"  ticket {ticket_id or '(record only)'}"
        )
        results.append({**bet, "outcome": "placed",
                         "ticket_id": ticket_id, "real_bet_id": real_bet_id,
                         "live_odds": live_odds, "stake": stake})

    # ── Combo bets (COMBO-PLACER, 2026-05-23) ─────────────────────────────────
    # Singles loop done — now process qualifying combo simulated_bets the
    # same way: resolve every leg's Coolbet outcome, then either write a
    # paper bet (--record) or warn that the Coolbet combo POST schema is
    # still pending (--execute, follow-up task).
    if search_blocked:
        log.warning("Skipping combo phase — Coolbet search is blocked; "
                    "refresh JWT and re-run.")
    else:
        combo_results = _place_combo_bets(
            session, guard, fetch_match_markets, fetch_odds_for_markets,
            resolve_placement_target, record=record, execute=execute,
        )
        results.extend(combo_results)

    return results


def _place_combo_bets(
    session: "CoolbetSession",
    guard: PlacementGuard,
    fetch_match_markets,
    fetch_odds_for_markets,
    resolve_placement_target,
    *,
    record: bool,
    execute: bool,
) -> list[dict]:
    """COMBO-PLACER (2026-05-23): resolve qualifying combo simulated_bets
    against Coolbet and write a single multi-leg real_bet per combo.

    Per-leg Coolbet resolution reuses the same path as singles
    (search → fetch_match_markets → resolve_placement_target). A combo
    requires ALL legs to resolve cleanly; if any leg can't be priced on
    Coolbet, the whole combo is skipped.

    --execute is not yet wired: the Coolbet combo POST payload shape is
    captured-browser-bet territory and we have a single's shape only. For
    now --execute on a combo falls back to record-only and emits a warning.
    Tracked as COMBO-EXECUTE-COOLBET-API follow-up.
    """
    combos = load_qualified_combo_bets()
    if not combos:
        log.info("No qualifying combo bets to evaluate")
        return []
    log.info("Found %d qualifying combo simulated_bet(s) to evaluate", len(combos))

    results: list[dict] = []
    for cidx, combo in enumerate(combos):
        legs = combo["combo_legs"]
        if isinstance(legs, str):
            import json as _json
            legs = _json.loads(legs)
        if not legs:
            continue

        sim_id      = str(combo["simulated_bet_id"])
        bot_id      = str(combo["bot_id"])
        bot_name    = combo.get("bot_name") or ""
        system_type = combo.get("system_type") or "straight"
        combined_model_odds = float(combo.get("combined_model_odds") or 0)
        edge_pct    = float(combo["edge_percent"]) * 100
        stake       = guard.stake_for({"model_stake": combo.get("model_stake")})
        label_head  = f"COMBO[{system_type}] {bot_name} edge {edge_pct:+.2f}% ({len(legs)} legs)"

        # Resolve every leg against Coolbet
        resolved_legs: list[dict] = []
        skip_reason = None
        for i, leg in enumerate(legs, 1):
            leg_match_id = str(leg["match_id"])
            leg_market   = leg["market"]
            leg_sel      = leg["selection"]
            # Look up team names + kickoff
            team_rows = execute_query(
                """SELECT ht.name AS home, at2.name AS away, m.date AS kick
                   FROM matches m
                   JOIN teams ht  ON ht.id  = m.home_team_id
                   JOIN teams at2 ON at2.id = m.away_team_id
                   WHERE m.id = %s""",
                (leg_match_id,),
            )
            if not team_rows:
                skip_reason = f"leg {i}: match {leg_match_id} not in DB"
                break
            home, away = team_rows[0]["home"], team_rows[0]["away"]

            try:
                ev = search_coolbet_event(session, home, away)
            except CoolbetSearchBlocked as e:
                log.error("Coolbet search blocked during combo leg %d — "
                          "aborting combo phase after %d/%d combos. %s",
                          i, cidx, len(combos), e)
                for rc in combos[cidx:]:
                    results.append({**rc, "outcome": "search_blocked",
                                     "reason": "coolbet search HTTP-refused "
                                               "(dead JWT / Incapsula)"})
                return results
            if ev is None:
                skip_reason = f"leg {i}: no Coolbet event for {home} vs {away}"
                break
            cb_match_id = int(ev["id"])
            markets  = fetch_match_markets(session, cb_match_id)
            odds_map = fetch_odds_for_markets(session, markets)
            target = resolve_placement_target(markets, odds_map, leg_market, leg_sel)
            if target is None:
                skip_reason = f"leg {i}: market {leg_market}/{leg_sel} not on Coolbet for {home} vs {away}"
                break
            bo_id, oc_id, odds_uuid, ev_odds = target
            resolved_legs.append({
                "match_id": leg_match_id,
                "market":   leg_market,
                "selection": leg_sel,
                "odds":     float(leg.get("odds") or 0),
                "prob":     float(leg.get("prob") or 0),
                "bot_source": leg.get("bot_source") or "",
                "coolbet_match_id": cb_match_id,
                "coolbet_market_id": bo_id,
                "coolbet_outcome_id": oc_id,
                "coolbet_odds_id": odds_uuid,
                "coolbet_odds":   ev_odds,
            })

        if skip_reason:
            log.info("%s — skipping: %s", label_head, skip_reason)
            results.append({**combo, "outcome": "no_market",
                             "reason": skip_reason})
            continue

        # Combined live odds (straight accumulator product).
        live_combined = 1.0
        for rl in resolved_legs:
            live_combined *= float(rl["coolbet_odds"] or 1.0)

        allowed, reason = guard.can_place(combo, stake)
        if not allowed:
            log.info("Skip %s — %s", label_head, reason)
            results.append({**combo, "outcome": "guard_skip", "reason": reason})
            continue

        if not record:
            log.info("[DRY-RUN] %s  combined live=%.3f stake=€%.2f",
                     label_head, live_combined, stake)
            results.append({**combo, "outcome": "dry_run",
                             "live_combined_odds": live_combined, "stake": stake})
            continue

        ticket_id = None
        if execute:
            # COMBO-EXECUTE-COOLBET-API (follow-up): the bet API's POST
            # payload shape for combo tickets isn't captured yet. Recording
            # only — manual placement still possible at coolbet.com.
            log.warning(
                "%s — --execute requested but Coolbet combo POST schema "
                "not implemented; recording only (manual placement still "
                "possible at coolbet.com).",
                label_head,
            )

        real_bet_id = store_real_bet(
            match_id=str(combo["placeholder_match_id"]),
            market="combo",
            selection=system_type,
            bookmaker="Coolbet",
            captured_odds=combined_model_odds if combined_model_odds > 0 else None,
            actual_odds=live_combined,
            stake=stake,
            bot_id=bot_id,
            simulated_bet_id=sim_id,
            notes=f"auto-combo ticket={ticket_id} edge={edge_pct:+.2f}% legs={len(resolved_legs)}",
            combo_legs=resolved_legs,
            system_type=system_type,
        )
        guard.record_placement(stake)
        log.info("✓ Recorded %s  combined live=%.3f  stake=€%.2f  real_bet=%s",
                 label_head, live_combined, stake, real_bet_id)
        results.append({**combo, "outcome": "placed",
                         "ticket_id": ticket_id, "real_bet_id": real_bet_id,
                         "live_combined_odds": live_combined, "stake": stake})

    return results
