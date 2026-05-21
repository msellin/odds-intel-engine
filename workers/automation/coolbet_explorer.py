"""Coolbet read-only odds explorer.

Companion to `coolbet_placer.py`. Shares the authenticated CoolbetSession + the
fo-category / search / sidebets helpers but never POSTs a bet — purely
explore-and-ingest. Built to answer "what does Coolbet actually price for the
matches in our DB?", which is the data we need to evaluate COOLBET-OR-PIN-REQUIRED
as a replacement for the current Pinnacle-only OU quality gate.

Two modes:
  --match-id <uuid>            One-shot: print all Coolbet markets for one match.
  (default)                    Bulk: snapshot all matches in DB kicking off
                               within --days days, store in odds_snapshots.

Behaviour notes:
  • Matches our matches → Coolbet events via the same search-then-fo-category
    fallback the placer uses (and the same fuzzy threshold). Skips matches with
    no Coolbet fixture.
  • Stores via store_coolbet_odds_snapshot — one row per (market, selection).
  • Sleeps 0.25s between sidebets calls (one per match) to be polite.
  • --dry-run prints what would be stored without writing.
  • --no-store with one-shot mode is implied (the one-shot view always prints).

Markets parsed: 1X2, OU 0.5/1.5/2.5/3.5/4.5, BTTS, double_chance,
asian_handicap (with line). Anything else is dropped on the floor with a debug
log; extend MARKET_PARSERS below if a missing market matters.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.supabase_client import (
    execute_query,
    store_coolbet_odds_snapshot,
)
from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    _parse_event,
    _FO_MATCH_URL,
    _ODDS_URL,
    _SIDEBETS_URL,
    fetch_coolbet_events,
    fuzzy_match_event,
    search_coolbet_event,
)

# Odds for line markets (OU, AH, handicap) live at a different endpoint than
# simple markets (1X2, BTTS, DC). Discovered 2026-05-20 via DevTools capture.
_ODDS_LINE_URL = "https://www.coolbet.com/s/sb-odds/odds/current/fo-line/"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("coolbet_explorer")
console = Console()


# ── Coolbet API: markets + odds (new schema, 2026-05-20) ─────────────────────
#
# Coolbet now serves markets and odds from separate endpoints:
#   POST /s/sbgate/sports/fo-match               → matches[].markets[] (no odds)
#   GET  /s/sbgate/sports/fo-market/sidebets     → markets[].markets[] (no odds)
#   POST /s/sb-odds/odds/current/fo              → simple-market odds (line=0)
#   POST /s/sb-odds/odds/current/fo-line/        → line-market odds (OU, AH)
#
# Both odds endpoints return: { "<outcome_id>": {value: <decimal>, status, ...} }
# Markets carry `market_type_id` (stable, locale-independent) and outcomes carry
# `result_key` ("[Home]"/"Draw"/"[Away]"/"Over"/"Under"/"Yes"/"No"/"1X"/...).
# Mapping by these instead of English labels is way more robust than the old
# Kambi-style criterion-label substring matching.


def fetch_match_markets(
    session: CoolbetSession, match_id: int, live: bool = False,
) -> list[dict]:
    """Combine fo-match + sidebets into one flat list of markets for a match.
    Each market: {id, name, line, market_type_id, outcomes:[{id, name, result_key}]}.
    Odds are NOT included — call fetch_odds_for_markets to fill those in.

    `live=True` switches sidebets to `matchStatus=LIVE` (vs default OPEN) and
    uses `limit=13` (matches the browser's live-page request). Used by the
    inplay snapshot capture flow. Per browser curl 2026-05-20, only the
    sidebets endpoint's matchStatus param needs flipping; fo-match returns
    the same shape for live + prematch matches."""
    flat: list[dict] = []

    # fo-match: main markets (1X2 + headline OU/BTTS for the league)
    r = session.post(_FO_MATCH_URL, json={
        "language": "en", "country": "EE", "layout": "EUROPEAN",
        "locale": "en", "matchIds": [str(match_id)],
    })
    if r.status_code == 200:
        for m in (r.json().get("matches") or []):
            flat.extend(m.get("markets") or [])
    else:
        log.warning("fo-match %s returned %d", match_id, r.status_code)

    # sidebets: side markets. Response groups individual line-markets under
    # `markets[].markets[]` (group → line variants). Flatten.
    r = session.get(_SIDEBETS_URL, params={
        "matchId": match_id, "country": "EE", "language": "en",
        "layout": "EUROPEAN", "limit": 13,
        "matchStatus": "LIVE" if live else "OPEN",
    })
    if r.status_code == 200:
        for group in (r.json().get("markets") or []):
            mtid = group.get("market_type_id")
            for sub in (group.get("markets") or []):
                if mtid and "market_type_id" not in sub:
                    sub["market_type_id"] = mtid
                flat.append(sub)
    else:
        log.warning("sidebets %s (live=%s) returned %d", match_id, live, r.status_code)
    return flat


def fetch_odds_for_markets(
    session: CoolbetSession, markets: list[dict],
) -> dict[int, dict]:
    """Resolve odds for every outcome across the given markets. Splits market_ids
    by simple (line==0) vs line (line!=0) and POSTs to the matching endpoint.
    Returns {outcome_id: {value, odds_id, market_id, status}} — full odds row,
    not just the decimal. Placer needs the odds_id UUID for the bet payload.

    Backwards-compat for explorer: callers that just want the price do
    `odds_map[oid]['value']`."""
    simple_ids: list[int] = []
    line_ids:   list[int] = []
    for mkt in markets:
        mid = mkt.get("id")
        if not mid:
            continue
        if _is_simple(mkt):
            simple_ids.append(int(mid))
        else:
            line_ids.append(int(mid))

    out: dict[int, dict] = {}
    if simple_ids:
        r = session.post(_ODDS_URL, json={
            "where": {"market_id": {"in": simple_ids}},
        })
        if r.status_code == 200:
            _harvest_odds(r.json(), out)
    if line_ids:
        # /fo-line/ takes a nested array (groups of related lines). Single group
        # of everything works in practice and avoids guessing the grouping.
        r = session.post(_ODDS_LINE_URL, json={"marketIds": [line_ids]})
        if r.status_code == 200:
            _harvest_odds(r.json(), out)
    return out


def _is_simple(mkt: dict) -> bool:
    line = mkt.get("line")
    if line is None:
        return True
    try:
        return float(line) == 0.0
    except (TypeError, ValueError):
        return str(line).strip() in ("", "0", "0.0")


def _harvest_odds(payload, into: dict[int, dict]) -> None:
    """Flatten Coolbet's {outcome_id_str: {value, odds_id, ...}} odds responses
    into {int(outcome_id): {value: float, odds_id: str, market_id: int, status: str}}."""
    if not isinstance(payload, dict):
        return
    for k, v in payload.items():
        try:
            oid = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        val = v.get("value")
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        into[oid] = {
            "value":      fval,
            "odds_id":    v.get("odds_id") or v.get("oddsId") or "",
            "market_id":  v.get("market_id") or v.get("marketId"),
            "status":     v.get("status") or "",
        }


# ── Market → our schema mapping ───────────────────────────────────────────────
#
# Map (market_type_id, result_key, line) → our (market_name, selection,
# handicap_line). Fallbacks on market.name when market_type_id is unknown so
# new markets degrade gracefully instead of silently dropping.
#
# Known market_type_ids (confirmed from probe responses):
#   81  → Match Result (1X2)
#   818 → Total Goals Over / Under
# More will be added as we observe them.

_MTID_1X2  = {81}
_MTID_OU   = {818}
_MTID_BTTS = set()          # populate when observed
_MTID_DC   = set()
_MTID_AH   = set()


def _ou_market_for_line(line: float) -> str | None:
    """OU .5 lines we ingest: 0.5, 1.5, 2.5, 3.5, 4.5."""
    if line is None:
        return None
    if abs(line * 10 - round(line * 10)) > 1e-6:
        return None
    cents = round(line * 10)
    if cents not in {5, 15, 25, 35, 45}:
        return None
    return f"over_under_{cents:02d}"


def parse_market(mkt: dict, odds_map: dict[int, dict]) -> list[tuple[str, str, float, float | None]]:
    """Return list of (market_name, selection, odds, handicap_line) rows
    for one Coolbet market dict, looking up odds by outcome_id.
    odds_map values are dicts ({value, odds_id, ...}) from fetch_odds_for_markets."""
    rows: list[tuple[str, str, float, float | None]] = []
    mtid = mkt.get("market_type_id")
    name = (mkt.get("name") or "").lower()
    line_raw = mkt.get("line")
    try:
        line_val = float(line_raw) if line_raw not in (None, "") else None
    except (TypeError, ValueError):
        line_val = None

    def _add(market: str, selection: str, oid, hline: float | None = None) -> None:
        try:
            entry = odds_map.get(int(oid))
        except (TypeError, ValueError):
            return
        if not entry:
            return
        # Backwards-compat: accept either {value: float} (new) or raw float (old).
        odds = entry.get("value") if isinstance(entry, dict) else entry
        if odds and odds > 1.0:
            rows.append((market, selection, float(odds), hline))

    is_1x2  = mtid in _MTID_1X2  or "match result" in name or "1x2" in name
    is_ou   = mtid in _MTID_OU   or "total goals over" in name or "over / under" in name
    is_btts = mtid in _MTID_BTTS or "both teams to score" in name or "btts" in name
    is_dc   = mtid in _MTID_DC   or "double chance" in name
    is_ah   = mtid in _MTID_AH   or "asian handicap" in name

    if is_1x2:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]")
            if rk == "Home":
                _add("1x2", "Home", oc.get("id"))
            elif rk == "Draw":
                _add("1x2", "Draw", oc.get("id"))
            elif rk == "Away":
                _add("1x2", "Away", oc.get("id"))
        return rows

    if is_ou:
        market = _ou_market_for_line(line_val) if line_val is not None else None
        if market is None:
            return rows
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            if rk == "over":
                _add(market, "over", oc.get("id"))
            elif rk == "under":
                _add(market, "under", oc.get("id"))
        return rows

    if is_btts:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            if rk == "yes":
                _add("btts", "yes", oc.get("id"))
            elif rk == "no":
                _add("btts", "no", oc.get("id"))
        return rows

    if is_dc:
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip()
            if rk in {"1X", "X2", "12"}:
                _add("double_chance", rk, oc.get("id"))
        return rows

    if is_ah:
        # AH: market.line is the home-perspective handicap. Outcomes are Home/Away.
        # Both rows share the same handicap_line (already home-perspective).
        if line_val is None:
            return rows
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]")
            if rk == "Home":
                _add("asian_handicap", "home", oc.get("id"), line_val)
            elif rk == "Away":
                _add("asian_handicap", "away", oc.get("id"), line_val)
        return rows

    return rows  # Unknown — degrade silently


def resolve_placement_target(
    markets: list[dict],
    odds_map: dict[int, dict],
    our_market: str,
    our_selection: str,
) -> tuple[int, int, str, float] | None:
    """For a bot's (market, selection) bet, find the Coolbet
    (market_id, outcome_id, odds_id, current_decimal_odds).

    our_market:    "1X2" | "O/U" | "BTTS" | "double_chance" | "asian_handicap"
    our_selection: "Home" | "Over 1.5" | "Yes" | "1X" | "Home -1.25" | ...

    Returns None if no matching market+outcome was found OR the outcome has
    no odds entry (suspended / dropped).

    Used by coolbet_placer.place_all_bets to resolve a paper bet into the
    actual Coolbet IDs needed for placement.
    """
    target_market, target_sel, target_line = _normalise_our_target(our_market, our_selection)
    if target_market is None:
        return None
    for mkt in markets:
        for parsed_market, parsed_sel, _odds, parsed_line in parse_market(mkt, odds_map):
            if parsed_market != target_market:
                continue
            if parsed_sel != target_sel:
                continue
            if target_line is not None and not _lines_equal(parsed_line, target_line):
                continue
            # Find the outcome_id whose result_key matches parsed_sel.
            oid = _outcome_id_for_selection(mkt, parsed_market, parsed_sel)
            if oid is None:
                continue
            entry = odds_map.get(int(oid))
            if not entry:
                continue
            return (
                int(mkt.get("id") or 0),
                int(oid),
                str(entry.get("odds_id") or ""),
                float(entry.get("value") or 0),
            )
    return None


def _lines_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-6


def _normalise_our_target(
    our_market: str, our_selection: str,
) -> tuple[str | None, str | None, float | None]:
    """Map our (market, selection) into the (parsed_market, parsed_sel, line)
    tuple shape that parse_market emits. Returns (None, None, None) if the
    market type isn't supported."""
    m = our_market.strip()
    s = our_selection.strip()

    if m in ("1X2", "1x2"):
        if s.lower() in ("home", "draw", "away"):
            return ("1x2", s.title(), None)
        return (None, None, None)

    if m in ("O/U", "OU", "ou"):
        # selection like "Over 1.5" / "Under 2.5"
        parts = s.split()
        if len(parts) != 2:
            return (None, None, None)
        side, line_str = parts[0].lower(), parts[1]
        try:
            line = float(line_str)
        except ValueError:
            return (None, None, None)
        market = _ou_market_for_line(line)
        if market is None or side not in ("over", "under"):
            return (None, None, None)
        return (market, side, None)

    if m == "BTTS":
        if s.lower() in ("yes", "no"):
            return ("btts", s.lower(), None)
        return (None, None, None)

    if m == "double_chance":
        if s in ("1X", "X2", "12"):
            return ("double_chance", s, None)
        return (None, None, None)

    if m == "asian_handicap":
        # selection like "Home -1.25" / "Away +0.5" — line is home-perspective
        parts = s.split()
        if len(parts) != 2:
            return (None, None, None)
        side = parts[0].lower()
        try:
            line = float(parts[1])
        except ValueError:
            return (None, None, None)
        if side not in ("home", "away"):
            return (None, None, None)
        return ("asian_handicap", side, line)

    return (None, None, None)


def _outcome_id_for_selection(mkt: dict, parsed_market: str, parsed_sel: str) -> int | None:
    """Given a Coolbet market dict and our (parsed_market, parsed_sel), find
    the outcome_id whose result_key matches. Mirrors the parsing in
    parse_market but returns the outcome_id instead of the (market, sel,
    odds, line) tuple."""
    sel_to_key = {
        ("1x2",          "Home"):    "Home",
        ("1x2",          "Draw"):    "Draw",
        ("1x2",          "Away"):    "Away",
        ("btts",         "yes"):     "yes",
        ("btts",         "no"):      "no",
        ("double_chance","1X"):      "1x",
        ("double_chance","X2"):      "x2",
        ("double_chance","12"):      "12",
        ("asian_handicap","home"):   "Home",
        ("asian_handicap","away"):   "Away",
    }
    # OU selections are dynamic (over/under across multiple lines)
    if parsed_market.startswith("over_under_"):
        target_key = parsed_sel.lower()  # "over" or "under"
    else:
        target_key = sel_to_key.get((parsed_market, parsed_sel))
        if target_key is None:
            return None
    for oc in mkt.get("outcomes") or []:
        rk = (oc.get("result_key") or "").strip("[]").lower()
        if rk == target_key.lower():
            return oc.get("id")
    return None


# ── DB layer ──────────────────────────────────────────────────────────────────


def load_matches_in_window(days: int) -> list[dict]:
    """Pull pre-KO matches from our DB kicking off within `days` days.

    TODAY-ONLY-SHORTCUT (2026-05-20): when `days` <= 1 we use a strict
    "today UTC" filter (`DATE(m.date) = CURRENT_DATE`) instead of a rolling
    24-hour window. Tomorrow's matches don't help today's betting and
    iterating them just burns API budget. For multi-day windows the old
    rolling-interval behaviour is preserved."""
    if int(days) <= 1:
        return execute_query(
            """
            SELECT m.id::text AS id, m.date AS date,
                   ht.name AS home, at2.name AS away,
                   l.name AS league
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at2 ON at2.id = m.away_team_id
            JOIN leagues l ON l.id = m.league_id
            WHERE m.date > NOW()
              AND DATE(m.date AT TIME ZONE 'UTC') = CURRENT_DATE
              AND m.status = 'scheduled'
            ORDER BY m.date
            """,
            (),
        )
    return execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.date > NOW()
          AND m.date < NOW() + INTERVAL '%s days'
          AND m.status = 'scheduled'
        ORDER BY m.date
        """ % int(days),
        (),
    )


def load_today_active_leagues() -> list[dict]:
    """LEAGUE-MAPPED ingest mode: only fetch Coolbet leagues where AF has
    today's matches WITH odds. Skips leagues with no activity today —
    much smaller API footprint than iterating all 132 mapped leagues.

    Returns rows: {af_league_id, cb_league_id, cb_full_slug, cb_name,
                   match_count, with_odds_count}
    """
    import json
    mapping_path = Path(__file__).parent / "coolbet_league_mapping.json"
    if not mapping_path.exists():
        log.warning("coolbet_league_mapping.json not found — no leagues to fetch")
        return []
    mapping = json.loads(mapping_path.read_text())
    # Build af_league_id → list of cb mappings (some AF leagues map to multiple CB)
    by_af: dict[str, list[dict]] = {}
    for m in mapping:
        by_af.setdefault(m["db_league_id"], []).append(m)

    # AF leagues with today's matches that have any odds in odds_snapshots
    rows = execute_query(
        """
        SELECT m.league_id::text AS af_league_id,
               COUNT(DISTINCT m.id)                                AS match_count,
               COUNT(DISTINCT m.id) FILTER (
                 WHERE EXISTS (SELECT 1 FROM odds_snapshots os
                               WHERE os.match_id = m.id)
               )                                                    AS with_odds_count
        FROM matches m
        WHERE m.date > NOW()
          AND DATE(m.date AT TIME ZONE 'UTC') = CURRENT_DATE
          AND m.status = 'scheduled'
        GROUP BY m.league_id
        HAVING COUNT(DISTINCT m.id) FILTER (
                 WHERE EXISTS (SELECT 1 FROM odds_snapshots os
                               WHERE os.match_id = m.id)
               ) > 0
        ORDER BY COUNT(DISTINCT m.id) DESC
        """,
        (),
    )
    out: list[dict] = []
    for r in rows:
        af_id = r["af_league_id"]
        cb_mappings = by_af.get(af_id, [])
        for cb in cb_mappings:
            out.append({
                "af_league_id":   af_id,
                "cb_league_id":   cb["cb_league_id"],
                "cb_full_slug":   cb["cb_full_slug"],
                "cb_name":        cb["cb_league_name"],
                "confidence":     cb.get("confidence", "high"),
                "match_count":    int(r["match_count"] or 0),
                "with_odds_count":int(r["with_odds_count"] or 0),
            })
    return out


def load_today_matches_for_league(af_league_id: str) -> list[dict]:
    """Today's AF matches in a specific league."""
    return execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        WHERE m.league_id = %s::uuid
          AND m.date > NOW()
          AND DATE(m.date AT TIME ZONE 'UTC') = CURRENT_DATE
          AND m.status = 'scheduled'
        ORDER BY m.date
        """,
        (af_league_id,),
    )


def load_value_bet_matches(days: int) -> list[dict]:
    """Pull matches that currently have at least one pending value bet from any
    active bot, kicking off within `days` days. This is the actionable set —
    matches we'd actually place real money on. Far smaller (and far better-
    targeted) than `load_matches_in_window`."""
    return execute_query(
        """
        SELECT DISTINCT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league,
               COUNT(*) OVER (PARTITION BY m.id) AS bet_count
        FROM simulated_bets sb
        JOIN bots b   ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        JOIN teams ht  ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE sb.result = 'pending'
          AND m.date > NOW()
          AND m.date < NOW() + INTERVAL '%s days'
          AND b.is_active = true
          AND b.retired_at IS NULL
        ORDER BY m.date
        """ % int(days),
        (),
    )


def store_coolbet_snapshots_for_match(
    match_id: str,
    coolbet_markets: list[dict],
    odds_map: dict[int, float],
    *,
    dry_run: bool,
    kickoff_iso: str = "",
) -> tuple[int, int, dict[str, int]]:
    """Parse + store all markets for one match. Returns (parsed, stored, by_market)."""
    parsed = 0
    stored = 0
    by_market: dict[str, int] = {}
    minutes_to_ko = _minutes_to_kickoff(kickoff_iso)

    for mkt in coolbet_markets:
        for market, selection, odds, line in parse_market(mkt, odds_map):
            parsed += 1
            by_market[market] = by_market.get(market, 0) + 1
            if dry_run:
                continue
            try:
                if market == "asian_handicap":
                    _store_with_handicap(match_id, market, selection, odds, line, minutes_to_ko)
                else:
                    store_coolbet_odds_snapshot(match_id, market, selection, odds, minutes_to_ko)
                stored += 1
            except Exception as e:
                log.warning("Store failed for %s %s: %s", market, selection, e)
    return parsed, stored, by_market


def _store_with_handicap(
    match_id: str, market: str, selection: str, odds: float,
    line: float | None, minutes_to_ko: int | None,
) -> None:
    """AH rows need handicap_line populated. store_coolbet_odds_snapshot doesn't
    expose handicap_line, so insert directly."""
    from workers.api_clients.db import get_conn
    now = datetime.now(timezone.utc).isoformat()
    is_closing = minutes_to_ko is not None and abs(minutes_to_ko) <= 5
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO odds_snapshots
                   (match_id, bookmaker, market, selection, odds, timestamp,
                    is_closing, minutes_to_kickoff, handicap_line)
                   VALUES (%s, 'Coolbet', %s, %s, %s, %s, %s, %s, %s)""",
                (match_id, market, selection, odds, now,
                 is_closing, minutes_to_ko, line),
            )
            conn.commit()


def _minutes_to_kickoff(iso: str) -> int | None:
    if not iso:
        return None
    try:
        ko = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = ko - datetime.now(timezone.utc)
    return int(delta.total_seconds() // 60)


# ── League-sweep (LEAGUE-MAPPED 2026-05-20) ───────────────────────────────────


def run_league_sweep(
    *,
    dry_run: bool = False,
    sleep_s: float = 1.5,
    require_pinnacle: bool = False,
    session: "CoolbetSession | None" = None,
) -> None:
    """Today's AF matches → group by league → look up mapped Coolbet league →
    fetch its events in one call → match events within-league by team names →
    pull markets+odds for matched pairs → store.

    Far fewer API calls than per-match search (one fo-category call per active
    league vs one search call per match). Within-league matching is reliable
    because both AF and Coolbet ship clean team names — fuzzy threshold can be
    low (65) without false positives.

    require_pinnacle: if True, only consider AF matches that have Pinnacle
    odds. Default False (any-odds is broader and matches bot universe).
    """
    from workers.automation.coolbet_session import CoolbetSession
    from workers.automation.coolbet_placer import fetch_events_for_league
    from rapidfuzz import fuzz

    active = load_today_active_leagues()
    if require_pinnacle:
        # Filter to leagues where at least one of today's matches has Pinnacle
        from workers.api_clients.supabase_client import execute_query as _q
        pin_ok = {r["af_league_id"] for r in _q(
            """
            SELECT DISTINCT m.league_id::text AS af_league_id
            FROM matches m
            JOIN odds_snapshots os ON os.match_id = m.id
            WHERE m.date > NOW()
              AND DATE(m.date AT TIME ZONE 'UTC') = CURRENT_DATE
              AND m.status = 'scheduled'
              AND os.bookmaker = 'Pinnacle'
            """, (),
        )}
        before = len(active)
        active = [r for r in active if r["af_league_id"] in pin_ok]
        log.info("Pinnacle filter: %d → %d leagues", before, len(active))

    if not active:
        console.print("[yellow]No active leagues today.[/yellow]")
        return

    log.info("League sweep — %d Coolbet leagues to fetch (today's AF matches)", len(active))
    if session is None:
        session = CoolbetSession()

    matched_total = 0
    parsed_total = 0
    stored_total = 0
    by_market: dict[str, int] = {}

    seen_cb_ids: set[int] = set()  # dedup if same CB league mapped from multiple AF leagues

    for i, league in enumerate(active, 1):
        cb_id = league["cb_league_id"]
        cb_slug = league["cb_full_slug"]
        if cb_id in seen_cb_ids:
            continue
        seen_cb_ids.add(cb_id)

        af_matches = load_today_matches_for_league(league["af_league_id"])
        if not af_matches:
            continue

        cb_events = fetch_events_for_league(session, cb_id, league_slug=cb_slug)
        log.info("[%d/%d] %s (cb=%d) → AF matches=%d, CB events=%d",
                 i, len(active), league["cb_name"], cb_id, len(af_matches), len(cb_events))
        if not cb_events:
            continue

        # Within-league fuzzy match (high precision because same league)
        for af_m in af_matches:
            af_key = f"{af_m['home']} vs {af_m['away']}".lower()
            best, best_score = None, 0
            for cb_e in cb_events:
                cb_key = f"{cb_e['home']} vs {cb_e['away']}".lower()
                sc = fuzz.token_sort_ratio(af_key, cb_key)
                if sc > best_score:
                    best, best_score = cb_e, sc
            if not best or best_score < 65:
                continue

            matched_total += 1
            # Pull markets+odds, store
            markets = fetch_match_markets(session, int(best["id"]))
            odds_map = fetch_odds_for_markets(session, markets)
            parsed, stored, mkt_counts = store_coolbet_snapshots_for_match(
                af_m["id"], markets, odds_map,
                dry_run=dry_run, kickoff_iso=best.get("start") or "",
            )
            parsed_total += parsed
            stored_total += stored
            for k, v in mkt_counts.items():
                by_market[k] = by_market.get(k, 0) + v
        time.sleep(sleep_s)

    t = Table(show_header=True, title=f"League-sweep summary {'[DRY-RUN]' if dry_run else ''}")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    t.add_row("Active leagues iterated", str(len(seen_cb_ids)))
    t.add_row("AF↔CB match pairs",       str(matched_total))
    t.add_row("Odds rows parsed",        str(parsed_total))
    t.add_row("Odds rows stored",        "0 (dry-run)" if dry_run else str(stored_total))
    console.print(t)

    if by_market:
        t2 = Table(show_header=True, title="By market")
        t2.add_column("Market"); t2.add_column("Rows", justify="right")
        for k, v in sorted(by_market.items(), key=lambda kv: -kv[1]):
            t2.add_row(k, str(v))
        console.print(t2)


# ── Bulk + one-shot drivers ───────────────────────────────────────────────────


def run_bulk(
    days: int, dry_run: bool, sleep_s: float, limit: int | None,
    *, bets_only: bool = False,
    long_pause_every: int = 15, long_pause_s: float = 20.0,
    session: "CoolbetSession | None" = None,
) -> None:
    matches = load_value_bet_matches(days) if bets_only else load_matches_in_window(days)
    if limit:
        matches = matches[:limit]
    if not matches:
        msg = "No pending value-bet matches in window." if bets_only else "No upcoming matches in DB window."
        console.print(f"[yellow]{msg}[/yellow]")
        return
    label = "value-bet matches" if bets_only else "matches from DB"
    console.print(f"[cyan]Loaded {len(matches)} {label} (window={days}d){' [DRY-RUN]' if dry_run else ''}[/cyan]")

    if session is None:
        session = CoolbetSession()
    category_cache: list[dict] | None = None

    matched = 0
    parsed_total = 0
    stored_total = 0
    by_market: dict[str, int] = {}
    missed_leagues: dict[str, int] = {}
    matched_leagues: dict[str, int] = {}

    for i, m in enumerate(matches, 1):
        home, away = m["home"], m["away"]
        league = m.get("league") or "—"
        ev = search_coolbet_event(session, home, away)
        if ev is None:
            if category_cache is None:
                console.print("[dim]Search miss — loading full fo-category once[/dim]")
                try:
                    category_cache = fetch_coolbet_events(session)
                except Exception as e:
                    # fo-category endpoint has 404'd in production at least once
                    # (Coolbet seems to have moved or retired it). Degrade to
                    # search-only — matches the search misses are skipped.
                    log.warning("fo-category unavailable (%s) — falling back to search-only", e)
                    category_cache = []
            ev = fuzzy_match_event(home, away, category_cache) if category_cache else None
        if ev is None:
            missed_leagues[league] = missed_leagues.get(league, 0) + 1
            log.info("[%d/%d] no Coolbet event: %s vs %s (%s)", i, len(matches), home, away, league)
        else:
            matched_leagues[league] = matched_leagues.get(league, 0) + 1

            # New flow: fetch markets (fo-match + sidebets), then fetch odds
            # (split by simple vs line endpoint), then stitch.
            markets = fetch_match_markets(session, int(ev["id"]))
            odds_map = fetch_odds_for_markets(session, markets)
            parsed, stored, mkt_counts = store_coolbet_snapshots_for_match(
                m["id"], markets, odds_map,
                dry_run=dry_run, kickoff_iso=ev.get("start") or "",
            )
            matched += 1
            parsed_total += parsed
            stored_total += stored
            for k, v in mkt_counts.items():
                by_market[k] = by_market.get(k, 0) + v
            log.info("[%d/%d] %s vs %s → %d markets, %d odds, %d stored",
                     i, len(matches), home, away, len(markets), parsed, stored)

        # Sleep after every match (hit or miss) so misses don't fire search
        # queries back-to-back without any gap. Jitter breaks the fixed-period
        # pattern that Imperva's bot-detection looks for.
        time.sleep(sleep_s + random.uniform(0, sleep_s * 0.5))

        # Breathing pause every N matches — simulates a user scrolling around
        # between bouts of checking matches. Keeps the hourly request rate
        # well below scraper-flagging thresholds for long sweeps.
        if long_pause_every > 0 and i % long_pause_every == 0 and i < len(matches):
            pause = long_pause_s + random.uniform(0, long_pause_s * 0.3)
            log.info("breathing pause %.0fs after %d matches", pause, i)
            time.sleep(pause)

    console.print()
    t = Table(show_header=True, title="Coolbet ingest summary")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    t.add_row("Matches in DB window", str(len(matches)))
    t.add_row("Matched on Coolbet", str(matched))
    t.add_row("Rows parsed (all markets)", str(parsed_total))
    t.add_row("Rows stored", "0 (dry-run)" if dry_run else str(stored_total))
    console.print(t)

    if by_market:
        t2 = Table(show_header=True, title="By market")
        t2.add_column("Market")
        t2.add_column("Rows", justify="right")
        for k, v in sorted(by_market.items(), key=lambda kv: -kv[1]):
            t2.add_row(k, str(v))
        console.print(t2)

    # League-level match/miss split — most actionable view for "what does
    # Coolbet actually cover for us".
    all_leagues = sorted(set(matched_leagues) | set(missed_leagues))
    if all_leagues:
        t3 = Table(show_header=True, title="By league (matched / missed)")
        t3.add_column("League")
        t3.add_column("Matched", justify="right")
        t3.add_column("Missed", justify="right")
        t3.add_column("Match %", justify="right")
        for lg in all_leagues:
            mt = matched_leagues.get(lg, 0)
            ms = missed_leagues.get(lg, 0)
            pct = 100.0 * mt / (mt + ms) if (mt + ms) else 0
            t3.add_row(lg, str(mt), str(ms), f"{pct:.0f}%")
        console.print(t3)


def run_one_shot(match_id: str) -> None:
    rows = execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.id = %s
        """,
        (match_id,),
    )
    if not rows:
        console.print(f"[red]No match found with id={match_id}[/red]")
        return
    m = rows[0]
    console.print(f"[cyan]{m['home']} vs {m['away']} — {m['league']} — {m['date']}[/cyan]")

    session = CoolbetSession()
    ev = search_coolbet_event(session, m["home"], m["away"])
    if ev is None:
        console.print("[dim]Search miss — loading full fo-category[/dim]")
        try:
            ev = fuzzy_match_event(m["home"], m["away"], fetch_coolbet_events(session))
        except Exception as e:
            console.print(f"[yellow]fo-category unavailable ({e}). Search-only mode.[/yellow]")
            ev = None
    if ev is None:
        console.print("[yellow]No matching Coolbet event.[/yellow]")
        return

    markets = fetch_match_markets(session, int(ev["id"]))
    odds_map = fetch_odds_for_markets(session, markets)

    # --raw: dump every market's name + type_id before parsing, so unknown
    # markets (AH, DC, etc.) can be identified and _MTID_* sets updated.
    if getattr(args, "raw", False):
        tr = Table(show_header=True, title=f"RAW markets — event #{ev['id']}")
        tr.add_column("market_type_id", justify="right")
        tr.add_column("name")
        tr.add_column("line", justify="right")
        tr.add_column("outcomes")
        for mkt in markets:
            ocs = ", ".join(
                f"{o.get('result_key','?')}(id={o.get('id','?')})"
                for o in (mkt.get("outcomes") or [])[:4]
            )
            tr.add_row(
                str(mkt.get("market_type_id") or "—"),
                mkt.get("name") or "—",
                str(mkt.get("line") or "—"),
                ocs,
            )
        console.print(tr)
        console.print(f"[dim]{len(odds_map)} odds entries fetched[/dim]")
        return

    t = Table(show_header=True, title=f"Coolbet markets for event #{ev['id']}")
    t.add_column("Market")
    t.add_column("Selection")
    t.add_column("Line", justify="right")
    t.add_column("Odds", justify="right")
    seen = 0
    for mkt in markets:
        for market, selection, odds, line in parse_market(mkt, odds_map):
            t.add_row(market, selection, f"{line:+.2f}" if line is not None else "—", f"{odds:.3f}")
            seen += 1
    if seen == 0:
        console.print(f"[yellow]Event found ({len(markets)} markets / {len(odds_map)} odds) "
                      f"but parser recognised none. Probable cause: market_type_id mappings "
                      f"need extension. Run with --raw to see all market names + type_ids.[/yellow]")
        return
    console.print(t)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--match-id", help="Inspect a single match by our matches.id")
    ap.add_argument("--days", type=int, default=2, help="Bulk window in days (default 2)")
    ap.add_argument("--limit", type=int, help="Cap bulk to first N matches (testing)")
    ap.add_argument("--sleep", type=float, default=0.25, help="Seconds between sidebets calls")
    ap.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    ap.add_argument("--raw", action="store_true",
                    help="With --match-id: dump all raw market names + type_ids before parsing. "
                         "Use this to discover unknown market_type_ids (AH, DC, etc.) so "
                         "_MTID_* sets can be updated.")
    ap.add_argument("--bets-only", action="store_true",
                    help="Bulk only over matches that have a pending value bet (active bots)")
    args = ap.parse_args()

    if args.match_id:
        run_one_shot(args.match_id)
    else:
        run_bulk(args.days, args.dry_run, args.sleep, args.limit, bets_only=args.bets_only)


if __name__ == "__main__":
    main()
