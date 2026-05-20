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
    COOLBET_ODDS_TOLERANCE  — max odds slippage fraction before skipping (default: 0.05)
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rapidfuzz import fuzz, process as rfprocess
from workers.api_clients.db import execute_query
from workers.api_clients.supabase_client import store_coolbet_odds_snapshot, store_real_bet
from workers.automation.coolbet_session import CoolbetSession

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
        SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
            sb.id             AS simulated_bet_id,
            sb.match_id,
            sb.market,
            sb.selection,
            sb.odds_at_pick   AS model_odds,
            sb.edge_percent,
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


# ── Coolbet event fetcher ─────────────────────────────────────────────────────

def fetch_coolbet_events(session: CoolbetSession) -> list[dict]:
    """
    Fetch all pre-match football events from fo-category.
    Returns flat list of {id, home, away, start, bet_offers}.

    bet_offers: [{id, criterion_label, outcomes: [{id, label, odds_decimal}]}]
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


def search_coolbet_event(
    session: CoolbetSession, home: str, away: str
) -> dict | None:
    """
    Use GET /s/sbgate/sports/search/v2?search=<query> to find the Coolbet event
    for a specific match.  Searches for the home team name (first word of it),
    then fuzzy-matches all returned events against home+away pair.

    Returns a parsed event dict (same format as _parse_event) or None.
    Much faster than loading the full fo-category tree.
    """
    # Use the first "word" of the home team name as the search query —
    # enough to narrow results without risking zero hits on short names.
    query = home.split()[0] if home.split() else home

    resp = session.get(_SEARCH_URL, params={
        "search":   query,
        "country":  "EE",
        "language": "en",
        "layout":   "EUROPEAN",
    })
    if resp.status_code != 200:
        log.info("Search HTTP %d for query '%s' ('%s vs %s') — will try fo-category",
                 resp.status_code, query, home, away)
        return None

    data = resp.json()
    raw_events = (
        data if isinstance(data, list)
        else data.get("events") or data.get("results") or []
    )

    candidates = [e for e in (_parse_event(ev) for ev in raw_events) if e]
    if not candidates:
        log.info("Search for '%s' returned 0 events ('%s vs %s') — will try fo-category",
                 query, home, away)
        return None

    match = fuzzy_match_event(home, away, candidates)
    if match:
        log.info("Search matched '%s vs %s' → Coolbet '%s vs %s' (id=%s)",
                 home, away, match["home"], match["away"], match["id"])
    else:
        best = f"{candidates[0]['home']} vs {candidates[0]['away']}" if candidates else "—"
        log.info("Search found %d events for '%s' but none matched '%s vs %s' "
                 "(best candidate: '%s') — will try fo-category",
                 len(candidates), query, home, away, best)
    return match


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

def fuzzy_match_event(
    home: str, away: str, events: list[dict]
) -> dict | None:
    """Find the Coolbet event whose home+away names best match ours."""
    event_keys = [f"{e['home']} {e['away']}" for e in events]
    result = rfprocess.extractOne(
        f"{home} {away}", event_keys, scorer=fuzz.token_sort_ratio
    )
    if result is None:
        return None
    _, score, idx = result
    if score < _FUZZY_THRESHOLD:
        log.info("Fuzzy match FAILED for '%s vs %s' — best was '%s' (score %d < threshold %d)",
                 home, away, event_keys[idx], score, _FUZZY_THRESHOLD)
        return None
    log.info("Fuzzy matched '%s vs %s' → Coolbet '%s' (score %d)", home, away, event_keys[idx], score)
    return events[idx]


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
) -> str:
    """
    POST /s/bets/bets — place a single bet.
    Returns the ticket_id (UUID string) on success. Raises on failure.

    Payload structure confirmed from captured network request:
      ticketType: "single"
      bets[].oddsIdByOutcomeId: {outcomeId: oddsId_uuid}
      deviceId: uuid cookie value
    """
    device_id = session._http.cookies.get("uuid", "")

    payload = {
        "ticketType": "single",
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "copiedFrom": None,
        "foTranslationsByOutcomeId": {
            str(outcome_id): {
                "matchName":   match_name,
                "marketName":  market_name,
                "outcomeName": "",
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

    resp = session.post(_BET_URL, json=payload)
    log.debug("POST /s/bets/bets → %d  %s", resp.status_code, resp.text[:300])

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Bet placement failed ({resp.status_code}): {resp.text[:400]}"
        )

    data = resp.json()
    ticket_id = (
        data.get("ticketId")
        or data.get("ticket_id")
        or data.get("id")
        or str(data)[:40]
    )
    return ticket_id


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

    # COOLBET-SAFETY-GUARDRAILS: default guard = no limits (preserves
    # behavior for callers that don't pass one).
    if guard is None:
        guard = PlacementGuard()

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
    for bet in pending:
        home       = bet["home_team"]
        away       = bet["away_team"]
        mkt        = bet["market"]
        sel        = bet["selection"]
        model_odds = float(bet["model_odds"])
        # edge_percent stored as decimal fraction (0.09 = 9%); ×100 for display.
        edge_pct   = float(bet["edge_percent"]) * 100
        label      = f"{home} vs {away} | {mkt} {sel} @ {model_odds:.3f} (edge {edge_pct:+.2f}%)"

        # 1. Find Coolbet event
        ev = search_coolbet_event(session, home, away)
        if ev is None:
            if _category_events is None:
                try:
                    log.info("Search miss — loading full fo-category tree")
                    _category_events = fetch_coolbet_events(session)
                except Exception as e:
                    log.warning("fo-category unavailable (%s) — search-only", e)
                    _category_events = []
            ev = fuzzy_match_event(home, away, _category_events) if _category_events else None
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
            results.append({**bet, "outcome": "no_market"})
            continue
        bo_id, oc_id, odds_uuid, ev_odds = target

        # ── Snapshot Coolbet odds (all modes, including dry-run) ─────────────
        if ev_odds:
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
                    str(bet["match_id"]), mkt, sel, ev_odds, mins_to_ko
                )
                log.debug("Snapshot stored: %s %s %s %.3f (%s min to KO)",
                          home, mkt, sel, ev_odds, mins_to_ko)
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

        # Odds-drop check: refuse placement if Coolbet price fell more than
        # _ODDS_TOLERANCE below model_odds. (We already have current odds from
        # the resolve step — no second fetch needed.)
        drop = (model_odds - ev_odds) / model_odds if model_odds > 0 else 0
        odds_ok = drop <= _ODDS_TOLERANCE
        live_odds = ev_odds
        if not odds_ok:
            log.info("Odds dropped %.1f%% > %.1f%% tolerance — recording only, no execute",
                     drop * 100, _ODDS_TOLERANCE * 100)

        ticket_id = None
        if execute and odds_ok and odds_uuid:
            # --require-confirm prompts y/n in the TTY (skipped if non-interactive)
            if not guard.prompt_confirm(bet, stake, live_odds):
                log.info("Skip %s — confirm declined / no TTY for --require-confirm", label)
                results.append({**bet, "outcome": "confirm_declined"})
                continue
            match_name = f"{ev['home']} - {ev['away']}"
            try:
                ticket_id = _place_bet_api(
                    session, oc_id, odds_uuid, stake, match_name, f"{mkt} {sel}"
                )
                log.info("✓ Coolbet ticket placed: %s", ticket_id)
            except Exception as e:
                log.error("Coolbet placement failed for %s: %s — recording only", label, e)
        elif execute and not odds_uuid:
            log.warning("No oddsId UUID for %s — cannot place at Coolbet, recording only", label)

        # Write to real_bets (always in record mode)
        real_bet_id = store_real_bet(
            match_id=str(bet["match_id"]),
            market=mkt,
            selection=sel,
            bookmaker="Coolbet",
            captured_odds=ev_odds,
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
        results.append({**bet, "outcome": "placed",
                         "ticket_id": ticket_id, "real_bet_id": real_bet_id,
                         "live_odds": live_odds, "stake": stake})

    return results
