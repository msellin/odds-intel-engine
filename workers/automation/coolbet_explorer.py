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
import json
import logging
import os
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
    # COOLBET-CORNERS-NOT-FLOWING-2026-09-05: `limit` caps how many market GROUPS
    # the sidebets endpoint returns. It was hardcoded to 13, copied from the
    # browser's LIVE-page request (see the docstring above) — and a live page
    # shows far fewer groups than a pre-match one. Corner and card groups sit
    # beyond the first 13 on pre-match fixtures, so they were never returned and
    # the parser never saw them. Symptom: after the corners parser shipped,
    # Coolbet wrote 9 market families and ZERO corners for three hours, while
    # Unibet-Kambi wrote corners on 183 fixtures and Pinnacle on 301.
    #
    # LIVE keeps 13 — that request shape is copied from a real browser call and
    # the live page genuinely offers fewer groups; widening it there would change
    # in-play behaviour for no benefit, and in-play is retired anyway.
    r = session.get(_SIDEBETS_URL, params={
        "matchId": match_id, "country": "EE", "language": "en",
        "layout": "EUROPEAN",
        "limit": 13 if live else _SIDEBETS_PREMATCH_LIMIT,
        "matchStatus": "LIVE" if live else "OPEN",
    })
    if r.status_code == 200:
        groups = r.json().get("markets") or []
        for group in groups:
            mtid = group.get("market_type_id")
            for sub in (group.get("markets") or []):
                if mtid and "market_type_id" not in sub:
                    sub["market_type_id"] = mtid
                flat.append(sub)
        # ── COOLBET-SIDEBETS-LIMIT-IS-NOT-A-CAP-2026-09-06 ────────────────
        # There is deliberately no truncation detector here, and the reason is
        # worth writing down because the obvious one does not work.
        #
        # `limit` does NOT behave as "return up to N groups". Measured on
        # Birmingham v Wolves (event 6073447), asking the endpoint directly:
        #
        #     limit=60    ->  29 groups,  84 markets
        #     limit=300   ->  52 groups, 180 markets
        #     limit=1200  ->  52 groups, 180 markets
        #
        # At 60 it returns 29 groups — FEWER than the limit — and is still
        # truncating. So `len(groups) >= limit` can never fire, and no
        # group-count test can distinguish truncated from complete.
        #
        # The fix is therefore not a better detector or a better guess: it is a
        # limit high enough that it cannot bind. 300 and 1200 return identical
        # payloads, so a large value costs nothing — the response stops growing
        # once the board is exhausted. See _SIDEBETS_PREMATCH_LIMIT.
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

# COOLBET-CORNERS-NOT-FLOWING-2026-09-05: how many sidebet GROUPS to request for
# a PRE-MATCH fixture. Env-tunable because the right number is a property of
# Coolbet's board, not of our code — if they add market groups this may need to
# rise again, and a silent truncation looks exactly like "the book does not offer
# that market".
# COOLBET-CORNERS-LIMIT-STILL-TOO-LOW-2026-09-06: raised 60 -> 300. The 13 -> 60
# change had the right diagnosis and too small a number, and its comment claimed
# the problem was fixed without checking — Coolbet wrote ZERO corners rows in the
# entire time it was live.
#
# Measured on Birmingham v Wolves (Championship, coolbet_event_id 6073447), the
# deepest fixture available, all three in one sitting:
#
#     COOLBET_SIDEBETS_LIMIT=13    47 markets total,  0 corner/card
#     COOLBET_SIDEBETS_LIMIT=60    99 markets total,  0 corner/card   <- prod
#     COOLBET_SIDEBETS_LIMIT=300  195 markets total, 65 corner/card
#
# So corners sit beyond position 60 on a deep board. The parser was never the
# problem — feeding those 65 markets through `parse_market` yields 72 correctly
# namespaced rows (`cards_ou_15`, `corners_ou_*`, …) on the first try.
#
# Why it looked like "Coolbet does not offer corners": a thin fixture genuinely
# has none, and on 2026-09-06 (an international break) the sweep's deepest
# fixtures were 23-46 markets. Sampling those alone reproduces a confident and
# completely wrong conclusion — which is exactly what happened before this
# measurement.
#
# 300 is chosen to sit clear of the ~195 a full board currently returns, so the
# next market family Coolbet adds does not silently truncate again. Cost is one
# request per fixture either way (it is a query parameter); what grows is the
# odds fetch, roughly 99 -> 195 market ids on deep fixtures, and that is already
# chunked.
# Set FAR above any observed board rather than tuned to one, because `limit` is
# not a simple cap (see the note in fetch_match_markets) and because guessing it
# has now failed twice — 13, then 60 — each time producing zero rows, no error,
# and a symptom identical to "Coolbet does not offer that market".
#
# Measured: 300 and 1200 return byte-identical payloads (52 groups / 180
# markets), so the response stops growing once the board is exhausted and a
# large value costs nothing. 1000 is chosen to be unreachable in practice.
_SIDEBETS_PREMATCH_LIMIT = int(os.getenv("COOLBET_SIDEBETS_LIMIT", "1000"))

_MTID_1X2  = {81}
_MTID_OU   = {818}
_MTID_BTTS = {1377}         # "Both Teams To Score"
_MTID_DC   = {1484}         # "Double Chance"
_MTID_AH   = {1086}         # "Asian Handicap"

# ── COOLBET-MATCH-CORNERS-NAME-2026-09-06 ────────────────────────────────────
# Coolbet's MATCH-TOTAL corners market is called **"Match Corners"**, and that
# single fact is why Coolbet wrote per-team corners but never the match line
# the corners strategy actually trades.
#
# `is_corners` below requires the name to contain "corner" AND one of
# total/over/under. "[Home] Total Corners" satisfies it; "Match Corners" does
# not — so it matched no branch, hit the silent `return rows` at the bottom,
# and vanished. Cards were unaffected because Coolbet DOES call theirs "Total
# Cards", which is why `cards_ou_*` landed while `corners_ou_*` stayed at zero
# and made it look like a corners-specific outage.
#
# Found by the unmatched-market WARNING added earlier the same day, which is
# the only reason this was diagnosable at all: 248 warnings, naming
# 'match corners' (826) with lines 9.5-12.5 across the sweep. Worth noting the
# hypothesis was raised and then wrongly REFUTED hours earlier by sampling
# international-break fixtures that offered no corners whatsoever.
#
# Keyed by market_type_id, not name, following this file's own stated rule that
# mtids are locale-independent while names are not. The name test stays as a
# fallback for books/leagues that spell it differently.
#
# Only families with an EXISTING cross-book namespace are captured — a row in a
# namespace Pinnacle never writes has no de-vig anchor and is dead weight:
#     corners_ou_*      10 books today   <- the one the strategy needs
#     corners_1h_ou_*    8 books today
#     corners_handicap   Epicbet
# Deliberately NOT captured, for want of an anchor: 'most corners (3-way)'
# (451), 'first corner' (1753), 'last corner' (1754), 'half with most corners'
# (1807), '2nd half corners' (1755), and the cards handicap/most-cards family.
# Names already reported as unmatched this process — keeps the inventory log
# to one line per distinct (name, mtid) instead of one per fixture.
_UNMATCHED_SEEN: set = set()

_MTID_CORNERS_MATCH = {826}     # "Match Corners"        -> corners_ou_NN
_MTID_CORNERS_1H    = {1752}    # "1st Half Corners"     -> corners_1h_ou_NN
_MTID_CORNERS_AH    = {1724}    # "Corners Handicap (2 way)" -> corners_handicap

# CB-UB-1H-TT-COLUMNS-2026-09-11: the two families the shadow bots
# bot_1h_1x2_paper_shadow_v1 / bot_team_total_paper_shadow_v1 pick, and which
# Coolbet DOES offer — both were sitting in coolbet_market_inventory as
# unmatched (seen on every sweep), so the per-bot "Now CB" column was blank.
# Written in the exact vocabulary Epicbet/AF already use (`1x2_1h`,
# `team_total_{side}_{NN}` with a numeric handicap_line) so they join across
# books. mtid-keyed, with an EXACT-name fallback: "1st half result and 1st half
# both teams to score" (1549) and "1st half [home] goals" (844) must not match.
_MTID_1X2_1H       = {98}      # "1st half result"     -> 1x2_1h
_MTID_TEAM_TOTAL   = {1551: "home", 1547: "away"}  # "[home]/[away] total goals"
_NAME_TEAM_TOTAL   = {"[home] total goals": "home", "[away] total goals": "away"}


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


# COOLBET-HALF-MATCH-FILTER-2026-08-22: reject sub-period markets before they
# hit the market-type dispatch. Coolbet's API returns "Both Teams To Score
# 1st Half" and similar sub-period markets whose names still match the
# is_btts / is_ou name-fallback substrings ("both teams to score", "total
# goals") — they were being written into the full-match BTTS/OU slot and
# clobbering the real full-match rows. Confirmed 2026-08-22 on Olympic vs
# Tvååker BTTS: three separate BTTS pairs stored in one scrape, the last
# one (yes=2.41/no=1.46) had yes/no swapped vs full-match consensus (~1.46
# yes across every other book) because the "last" pair was actually the
# 2nd-half market. Only affects the name-fallback path — if `mtid` is
# already a known main-market ID, we trust it.
_HALF_MATCH_HINTS = (
    " 1st half", " 2nd half", " first half", " second half",
    " 1st period", " 2nd period",
    " halftime", " half time", " ht ", "(ht)",
    " extra time", " overtime",
    " team to score", " home to score", " away to score",  # single-team variants
)

# COOLBET-COMBINED-MARKET-FILTER: reject combo markets like "Both Teams To
# Score & Over 2.5 Goals" from the is_btts name-fallback. These markets have
# combined outcomes (yes+over, no+under) and a different market_type_id than
# 1377, so they only reach the fallback path. Their result_keys are NOT the
# plain "yes"/"no" that the BTTS branch expects — so if they slip through they
# either add garbage rows or clobber the real BTTS slot depending on key shape.
# " & " is Coolbet's standard connector for all combined-market names.
_COMBINED_MARKET_HINTS = (" & ", " and over", " and under", "+ over", "+ under")

# COOLBET-OU-LINE-MISLABEL-RCA (2026-08-24): the is_ou name-fallback accepted ANY
# market whose name contains "total goals" / "over/under". Coolbet ships a family
# of markets that match that substring but are NOT the full-match goals line, and
# every one of them carries its own `line` (often 0.5-3.5) that collides with the
# real goals lines:
#   • team totals      — "Total Goals Home Team", "Total Goals — Away Team"
#   • non-goal totals  — "Over / Under Corners", "Total Cards", "Total Bookings"
#   • parity markets   — "Total Goals Odd/Even"
# A team total U2.5 (one side under 2.5) prices nothing like a match U2.5, so when
# one overwrote the other the stored OU ladder stopped being monotone in the line —
# the exact symptom the 2026-08-22 guard detects. The guard drops the data; this
# list stops it being written in the first place.
#
# Half/period variants are already handled by _HALF_MATCH_HINTS. Only the
# name-fallback consults this — a market arriving with a trusted `mtid` in
# _MTID_OU is still believed, so leagues that ship non-standard mtids
# (COOLBET-MARKET-NAMES 2026-05-23) do not regress.
_NON_GOALS_TOTAL_HINTS = (
    # team-total qualifiers
    "home team", "away team", "by home", "by away", "home total", "away total",
    # COOLBET-OU-LINE-SHIFT-2026-08-26: Coolbet's actual name for a team total
    # is "[Home] Total Goals" / "[Away] Total Goals" — BRACKETED, matching none
    # of the qualifiers above. It carries market_type_id 1551, which is not in
    # _MTID_OU, so it fell through to the name-fallback, matched "total goals",
    # and was written into the FULL-MATCH OU slot at the same `line` as the real
    # ladder.
    #
    # A single team going over 4.5 is far rarer than the match doing so, so the
    # prices are wildly apart and the mislabelled row looks like enormous value:
    # Grêmio Anápolis v Goianésia stored "over 4.5" at 17.00 while Pinnacle's
    # match line was 4.19 (+306%). Fleet-wide this showed as OU 4.5 sitting
    # +32% above Pinnacle on average and OU 3.5 +12%, while OU 1.5 and 2.5 —
    # lines a team total rarely competes on — were normal.
    #
    # Same family of bug as COOLBET-COMBINED-MARKET-FILTER (BTTS & Over 2.5
    # clobbering pure BTTS) and COOLBET-HALF-MATCH-FILTER (1st-half markets
    # clobbering full-match): a market whose NAME contains ours, at a `line`
    # that collides with ours.
    "[home]", "[away]", "[home ", "[away ",
    # parity
    "odd/even", "odd / even", "odd or even",
    # non-goal totals
    "corner", "card", "booking", "yellow", "red card", "shot", "foul",
    "offside", "throw", "save", "penalt", "substitut",
)


def _looks_like_non_goals_total(name: str) -> bool:
    """True if an over/under-shaped market name is not the full-match GOALS line.

    Applied to the is_ou name-fallback only; trusted mtids bypass it.
    See _NON_GOALS_TOTAL_HINTS for why each family is excluded."""
    if not name:
        return False
    return any(hint in name for hint in _NON_GOALS_TOTAL_HINTS)


def _looks_like_sub_period(name: str) -> bool:
    """True if the market name suggests a sub-period (half/period/single-team)
    market rather than a full-match one. Used to short-circuit the name-fallback
    on is_ou/is_btts so sub-period markets don't clobber their full-match slots."""
    if not name:
        return False
    for hint in _HALF_MATCH_HINTS:
        if hint in name:
            return True
    return False


def _looks_like_combined_market(name: str) -> bool:
    """True if the market name combines two independent bet types (e.g. 'BTTS & Over 2.5').
    Applied to the name-fallback path only — trusted mtids bypass this check."""
    if not name:
        return False
    for hint in _COMBINED_MARKET_HINTS:
        if hint in name:
            return True
    return False


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
    # AH lines use a display string ("0 - 4") that can't be parsed; fall back
    # to raw_line which carries the numeric value (-4.0 = home -4 handicap).
    if line_val is None and mkt.get("raw_line") is not None:
        try:
            line_val = float(mkt["raw_line"])
        except (TypeError, ValueError):
            pass

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

    # COOLBET-MARKET-NAMES (2026-05-23): Coolbet labels the same market type
    # differently across leagues (e.g. "Total Goals" in Brasileirão vs
    # "Total Goals Over/Under" elsewhere; "Match Winner (3-way)" vs "Match
    # Result"). Keep mtid as the primary key and broaden the name-fallback
    # so leagues with unfamiliar mtids still resolve. Discovered when Gremio
    # vs Santos was found on Coolbet but every selection skipped as
    # "no_market" — Coolbet returned `Match Winner (3-way)` + `Total Goals`.
    # COOLBET-HALF-MATCH-FILTER-2026-08-22: reject sub-period markets on the
    # name-fallback path only. If mtid matches a known main-market ID we still
    # trust it (mtid=818 is full-match Total Goals, mtid=1377 is full-match
    # BTTS — these are locale-independent and unambiguous). But for markets
    # coming in via the name-substring fallback (e.g. "Both Teams To Score
    # 1st Half"), reject if the name looks sub-period. Same guard applied to
    # is_1x2 defensively — we haven't seen half-1X2 collide yet, but Coolbet
    # ships "Match Result 1st Half" in some leagues.
    sub_period = _looks_like_sub_period(name)
    combined   = _looks_like_combined_market(name)
    is_1x2  = (mtid in _MTID_1X2
               or (not sub_period and ("match result" in name or "1x2" in name
                                       or "match winner" in name)))
    non_goals_total = _looks_like_non_goals_total(name)

    # NEW-MARKETS-LINESHOP-2026-09-05: corners and cards are over/under-shaped
    # markets Coolbet DOES offer — `_NON_GOALS_TOTAL_HINTS` exists precisely
    # because they were reaching the goals slot and clobbering it (see the
    # COOLBET-OU-LINE-SHIFT note there: a team total stored `over 4.5` at 17.00
    # against Pinnacle's 4.19, +306%).
    #
    # They are captured here into their OWN namespaces. The filter above is left
    # completely untouched — the goals path must keep rejecting them. Anyone
    # tempted to "fix" this by relaxing _NON_GOALS_TOTAL_HINTS would reproduce
    # that phantom price on a real-money surface.
    #
    # Why bother: Pinnacle prices corners at a 5.97% margin vs 5.68% on goals,
    # so the sharp anchor is just as good there, while Kambi charges 8.95% on
    # corners against 10.14% on goals. The gap to the sharp price is therefore
    # NARROWER on corners (2.98pp) than on goals (4.46pp) — measured 2026-09-05.
    # COOLBET-MATCH-CORNERS-NAME-2026-09-06: mtid FIRST. Coolbet calls the
    # match-total market "Match Corners", which contains no total/over/under
    # and so failed the name test below — the reason corners_ou_* was empty
    # while corners_home_ou_* and cards_ou_* both worked. See the _MTID_CORNERS_*
    # block for the full vocabulary and what is deliberately left out.
    is_corners = (not combined
                  and (mtid in _MTID_CORNERS_MATCH
                       or mtid in _MTID_CORNERS_1H
                       or (any(h in name for h in ("corner",))
                           and ("total" in name or "over" in name
                                or "under" in name))))
    is_cards = (not combined
                and any(h in name for h in ("card", "booking"))
                and ("total" in name or "over" in name or "under" in name))

    is_ou   = (mtid in _MTID_OU
               or (not sub_period and not combined and not non_goals_total
                   and ("total goals" in name
                        or "over / under" in name or "over/under" in name)))
    is_btts = (mtid in _MTID_BTTS
               or (not sub_period and not combined
                   and ("both teams to score" in name or "btts" in name)))
    is_dc   = mtid in _MTID_DC   or (not sub_period and "double chance" in name)
    is_ah   = mtid in _MTID_AH   or (not sub_period and "asian handicap" in name)

    # CB-UB-1H-TT-COLUMNS-2026-09-11 — see _MTID_1X2_1H. Checked before is_1x2
    # so a 1st-half result can never fall into the full-match slot.
    if mtid in _MTID_1X2_1H or name.strip() == "1st half result":
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]").lower()
            if rk in ("home", "draw", "away"):
                _add("1x2_1h", rk, oc.get("id"))
        return rows

    tt_side = _MTID_TEAM_TOTAL.get(mtid) or _NAME_TEAM_TOTAL.get(name.strip())
    if tt_side:
        # .0/.5 lines only: the name encoding is lossy for quarters
        # (MARKET-LINE-ENCODING-LOSSY-2026-09-06), and Coolbet ships none.
        if line_val is None or abs(line_val * 2 - round(line_val * 2)) > 1e-9:
            return rows
        tag = f"team_total_{tt_side}_{round(line_val * 10):02d}"
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or oc.get("name") or "").strip().lower()
            if rk.startswith("over"):
                _add(tag, "over", oc.get("id"), line_val)
            elif rk.startswith("under"):
                _add(tag, "under", oc.get("id"), line_val)
        return rows

    if is_1x2:
        # COOLBET-SELECTION-CASE (2026-06-03): emit lowercase to match every
        # OTHER bookmaker in odds_snapshots (Bet365/Pinnacle/Betano/… all store
        # `home`/`draw`/`away`). The /admin/place lookup lowercases via
        # `_mapPaperToSnapshotKey` so anything stored capital here is invisible
        # to the frontend → bet shows `⚠ no market` even when Coolbet priced it.
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").strip("[]")
            if rk == "Home":
                _add("1x2", "home", oc.get("id"))
            elif rk == "Draw":
                _add("1x2", "draw", oc.get("id"))
            elif rk == "Away":
                _add("1x2", "away", oc.get("id"))
        return rows

    if (is_corners or is_cards) and line_val is not None:
        # ── EPICBET-COOLBET-CORNERS-NAMESPACE-CLASH-2026-09-06 ──────────────
        # This built `corners_ou` and appended the side, producing
        # `corners_ou_home`. API-Football writes `corners_home_ou` (see the
        # market map in api_football.py) and Epicbet followed AF, because AF is
        # the feed that carries Pinnacle and Pinnacle is the de-vig anchor.
        #
        # Two spellings means per-team corners CANNOT be joined across books,
        # which defeats the entire point of collecting them: the line shop needs
        # the same key on both sides. Match totals (`corners_ou_*`, `cards_ou_*`)
        # were always consistent; only the side-scoped families clashed.
        #
        # Fixing it now is free — Coolbet has written ZERO corners rows all-time
        # (COOLBET-CORNERS-NEVER-WRITTEN), so there is nothing to migrate or
        # relabel. Doing it after rows accumulate would need a backfill.
        family = "corners" if is_corners else "cards"
        side = None
        if "[home]" in name or "home " in name:
            side = "home"
        elif "[away]" in name or "away " in name:
            side = "away"
        # AF convention: the qualifier sits BEFORE `_ou`, never after —
        # `corners_home_ou_45`, `corners_1h_ou_45`. Appending "_1h" to the end
        # would produce `corners_ou_1h_45`, which is a namespace AF/Kambi/
        # Epicbet never write, so the rows would have no de-vig anchor and be
        # dead weight — the same clash the side qualifier had (see above), and
        # caught here before any row was written rather than after.
        #
        # A market cannot be both team-scoped and first-half in the vocabulary
        # we share with AF, so 1H wins and a "2nd half [home] cards" style
        # market is left unmatched deliberately: there is no cross-book
        # namespace for it.
        # Detect 1H by MTID first. `_HALF_MATCH_HINTS` entries all carry a
        # LEADING SPACE (" 1st half"), deliberately, so a name that STARTS with
        # the qualifier — which is exactly how Coolbet spells it, "1st Half
        # Corners" — does not match. That is load-bearing for the goals path
        # and must not be loosened here; the mtid is the reliable key anyway.
        qualifier = ("1h" if (mtid in _MTID_CORNERS_1H or _looks_like_sub_period(name))
                     else side)
        prefix = f"{family}_{qualifier}_ou" if qualifier else f"{family}_ou"
        tag = f"{prefix}_{str(line_val).replace('.', '').replace('-', 'm')}"
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or oc.get("name") or "").strip().lower()
            sel = "over" if rk.startswith("over") or rk == "o" else \
                  "under" if rk.startswith("under") or rk == "u" else None
            od = odds_map.get(oc.get("id"))
            if sel and od and od.get("value"):
                try:
                    # MARKET-LINE-ENCODING-LOSSY-2026-09-06: carry the line
                    # NUMERICALLY, not only inside the market name. The name
                    # encoding is not reversible — `str(line).replace('.','')`
                    # maps both 10.5 and 1.25 to "125", and 4 distinct live
                    # markets already share the token "125". That ambiguity
                    # produced 634 fabricated losing bets when a backtest read
                    # over_under_1h_125 as a 12.5-goal first-half line.
                    #
                    # `handicap_line` already exists and is 100% populated for
                    # asian_handicap, so this needs no migration; it was simply
                    # NULL for every totals family. Nothing keys on it being
                    # NULL (checked across both repos), and the pipeline's
                    # DISTINCT ON already includes it, where the line is 1:1
                    # with the market name — so dedup is unchanged.
                    rows.append((tag, sel, float(od["value"]), line_val))
                except (TypeError, ValueError):
                    pass
        return rows

    if is_ou:
        market = _ou_market_for_line(line_val) if line_val is not None else None
        if market is None:
            return rows
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            # OU-LINE-BACKFILL-2026-09-11: `_add` takes the numeric line as its
            # 4th argument and the side-totals branch above passes it; this one,
            # the main goals ladder, did not — so Coolbet wrote handicap_line on
            # 100 pct of its corners/cards totals and 0 pct of its over_under_*.
            # `line_val` is right here and already validated by
            # _ou_market_for_line. See MARKET-LINE-ENCODING-LOSSY-2026-09-06 for
            # why the name alone is not enough.
            if rk == "over":
                _add(market, "over", oc.get("id"), line_val)
            elif rk == "under":
                _add(market, "under", oc.get("id"), line_val)
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
        # Coolbet DC result_keys use team placeholders: "[Home]/Draw",
        # "[Away]/Draw", "[Home]/[Away]". Map to our canonical 1x/x2/12
        # lowercase — matches the snapshot convention every other bookmaker
        # uses (see COOLBET-SELECTION-CASE above for the 1X2 explanation).
        _dc_key_map = {
            "[home]/draw":   "1x",
            "[away]/draw":   "x2",
            "[home]/[away]": "12",
        }
        for oc in mkt.get("outcomes") or []:
            rk = (oc.get("result_key") or "").lower()
            label = _dc_key_map.get(rk)
            if label:
                _add("double_chance", label, oc.get("id"))
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

    # ── COOLBET-CORNERS-NEVER-WRITTEN-2026-09-06 ────────────────────────────
    # "Degrade silently" is how a shipped feature can produce nothing for days
    # without a single log line. Coolbet has written ZERO corners rows all-time
    # while writing 55,376 rows across 9 healthy families in 24h, and the
    # `limit: 13 -> 60` fix that was supposed to solve it is deployed and did
    # not. The market is reaching this function and matching no branch.
    #
    # Most likely: `is_corners` above requires the name to contain "corner"
    # AND one of total/over/under, while `_NON_GOALS_TOTAL_HINTS` (which
    # contains "corner") already excludes it from the goals path. A group named
    # plainly "Corners" is therefore excluded from goals, matches nothing else,
    # and lands here — dropped with no error and no row.
    #
    # FlareSolverr's coolbet_prod session refuses ad-hoc calls while serving the
    # scheduled job, so this cannot be probed by hand. Logging the unmatched
    # name turns the next 30-minute sweep into the diagnostic instead. Kept at
    # DEBUG for everything EXCEPT the families we are actively chasing, so this
    # does not spam the journal with every exotic Coolbet market.
    # COOLBET-MARKET-INVENTORY-2026-09-06. Owner: "we should fetch all that we
    # can from Coolbet, we have no idea today what markets we will eventually
    # bet on." Agreed — but we cannot capture what we have never inventoried,
    # and until now everything except corners/cards dropped at DEBUG, i.e.
    # invisibly.
    #
    # So log EVERY unmatched market name once per process, deduplicated. One
    # sweep then yields the complete menu of what Coolbet offers and we throw
    # away, which is the input to deciding what is worth storing. Deduplicated
    # because a full sweep touches ~1,200 fixtures and would otherwise emit
    # hundreds of thousands of identical lines.
    #
    # This is deliberately an inventory, not a capture: a market with no
    # Pinnacle counterpart cannot be de-vigged and so cannot be priced against
    # a sharp line today. The owner's point stands anyway — optionality is
    # worth having, and the cost of knowing is one log line per distinct name.
    _n = name or ""
    _key = (_n, mtid)
    if _key not in _UNMATCHED_SEEN:
        _UNMATCHED_SEEN.add(_key)
        log.warning(
            "coolbet: UNMATCHED market (first sighting) name=%r market_type_id=%r "
            "line=%r — inventory for COOLBET-MARKET-INVENTORY", _n, mtid, line_val,
        )
        # COOLBET-MARKET-INVENTORY-2026-09-06: also persist to a durable catalog
        # so the "what do we not capture" menu survives restarts and is
        # queryable. One upsert per distinct (mtid, name) per process (gated by
        # the same _UNMATCHED_SEEN dedup), best-effort — a catalog write must
        # never break the sweep.
        if mtid is not None:
            try:
                from workers.api_clients.db import execute_write
                execute_write(
                    """INSERT INTO coolbet_market_inventory
                           (market_type_id, name, sample_line, first_seen, last_seen, times_seen)
                       VALUES (%s, %s, %s, now(), now(), 1)
                       ON CONFLICT (market_type_id, name) DO UPDATE
                          SET last_seen = now(),
                              times_seen = coolbet_market_inventory.times_seen + 1,
                              sample_line = COALESCE(coolbet_market_inventory.sample_line, EXCLUDED.sample_line)""",
                    [mtid, _n, line_val],
                )
            except Exception:
                pass  # best-effort inventory; never break the sweep
    return rows


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
    # Normalise to lowercase so DB values ("o/u", "btts", "1x2") match
    # regardless of how they were stored (uppercase variants were the original
    # assumption but the pipeline writes lowercase).
    m = our_market.strip().lower()
    s = our_selection.strip()

    if m in ("1x2",):
        # COOLBET-SELECTION-CASE (2026-06-03): return lowercase to match
        # parse_market's lowercase emit + the frontend snapshot lookup.
        sl = s.lower()
        if sl in ("home", "draw", "away"):
            return ("1x2", sl, None)
        return (None, None, None)

    # COMBO-LEG-MARKETS (2026-05-23): combo bots write per-leg markets as
    # "ou15"/"ou25"/"ou35"/"ou45"/"ou05" with selection "over"/"under"
    # (no embedded line — line is encoded in the market suffix). Singles
    # use "o/u" + "Over 2.5"; map both shapes here.
    if m in ("ou05", "ou15", "ou25", "ou35", "ou45"):
        side = s.lower().strip()
        if side not in ("over", "under"):
            return (None, None, None)
        suffix = m[2:]
        try:
            line = float(suffix[0]) + (0.5 if suffix[1] == "5" else 0.0)
        except (IndexError, ValueError):
            return (None, None, None)
        market = _ou_market_for_line(line)
        if market is None:
            return (None, None, None)
        return (market, side, None)

    if m in ("o/u", "ou"):
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

    if m == "btts":
        if s.lower() in ("yes", "no"):
            return ("btts", s.lower(), None)
        return (None, None, None)

    if m == "double_chance":
        # COOLBET-SELECTION-CASE (2026-06-03): emit lowercase to match
        # parse_market + the snapshot convention every other bookmaker uses.
        # Previously returned uppercase ("1X"/"X2"/"12") which got written
        # into odds_snapshots and was invisible to the frontend's lowercase
        # lookup, so Coolbet DC bets all surfaced as `⚠ no market`.
        s_lo = s.lower()
        if s_lo in ("1x", "x2", "12"):
            return ("double_chance", s_lo, None)
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
        # COOLBET-SELECTION-CASE (2026-06-03): keyed on lowercase parsed_sel —
        # parse_market + _normalise_our_target both emit lowercase now.
        ("1x2",          "home"):    "Home",
        ("1x2",          "draw"):    "Draw",
        ("1x2",          "away"):    "Away",
        ("btts",         "yes"):     "yes",
        ("btts",         "no"):      "no",
        # DC-RESULTKEY-FIX (2026-05-24): Coolbet DC result_keys use bracketed
        # team placeholders like `[Home]/Draw`, `[Home]/[Away]`. The old
        # `.strip("[]")` only stripped *leading/trailing* brackets, so
        # `[Home]/Draw` → `Home]/Draw` (still has the `]`) and `[Home]/[Away]`
        # → `Home]/[Away]` — neither equalled the sel_to_key target. Result:
        # DC bets have been silently returning no_market against any Coolbet
        # match that did offer Double Chance (confirmed live on Gagra vs Dila).
        # Fix: store target_key without brackets and use `.replace` on the
        # outcome's result_key to remove *all* brackets, not just edges.
        ("double_chance","1x"):      "home/draw",
        ("double_chance","x2"):      "away/draw",
        ("double_chance","12"):      "home/away",
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
        rk = (oc.get("result_key") or "").replace("[", "").replace("]", "").lower()
        if rk == target_key.lower():
            return oc.get("id")
    return None


# ── DB layer ──────────────────────────────────────────────────────────────────


def load_matches_in_kickoff_band(lo_h: float, hi_h: float) -> list[dict]:
    """Pre-KO matches kicking off between `lo_h` and `hi_h` hours from now.

    KICKOFF-BAND-SWEEP (2026-09-11). The day-window sweep loads EVERY fixture
    inside `--days` and walks them serially: ~2,000 fixtures taking 1.5h+, fired
    every 30 min, so passes overlap and the sweep is effectively continuous from
    one residential IP. Two things follow, and both are bad:

      * Imperva escalates. The runbook's §2 challenge is "usually triggered by
        our own request volume from one IP" — and FlareSolverr's logs show a
        FRESH session passing ("Challenge not detected!") while a REUSED one is
        challenged and times out. We have been treating a self-inflicted load
        problem as a mysterious external block, which is why it recurs daily.
      * Prices go stale where it matters. A full pass takes so long that the
        median Coolbet quote on an upcoming fixture is ~197 min old (p90 235),
        which is what the placer's `drift` rejections actually are.

    Measured distribution 2026-09-11 (48h horizon, 2,026 fixtures):
        0-6h     64      <- the band we actually place on
        6-24h   373
        24-48h 1589      <- 78% of the load, for fixtures a DAY+ away whose
                            prices will have moved entirely before we bet them

    So sweeping by kickoff proximity cuts footprint AND improves freshness at
    the same time — they are not a trade-off here. A 0-6h pass is ~3% of the
    current load.

    Half-open [lo_h, hi_h) so adjacent bands tile without double-sweeping the
    boundary fixture.
    """
    return execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league,
               m.league_id::text AS league_id
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.date > NOW()
          AND m.date >= NOW() + (%s * interval '1 hour')
          AND m.date <  NOW() + (%s * interval '1 hour')
          AND m.status = 'scheduled'
        ORDER BY m.date
        """,
        (float(lo_h), float(hi_h)),
    )


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
                   l.name AS league,
                   m.league_id::text AS league_id
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
               l.name AS league,
               m.league_id::text AS league_id
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


def _ou_rows_monotone(ou_rows: list[tuple[str, str, float]]) -> bool:
    """COOLBET-OU-LINE-MISLABEL-2026-08-22: sanity-check OU snapshot.

    17% of matches with today's shadow picks (51/302) had Coolbet OU rows where
    Under-probability was NOT monotone-nondecreasing in the line — mathematically
    impossible for a real market. Example: Shanghai Port II U2.5=1.60 and U3.5=1.20
    price essentially the same under-probability, so one label is wrong.

    Root cause hypothesis: `is_ou` gate in parse_market accepts any market with
    "total goals" / "over/under" substring — likely a non-goals total (halftime
    goals, corners, cards) with matching line value writes to the goals-OU slot
    and clobbers it. Cheap defensive fix: monotonicity guard at the writer.

    Returns True if the (over_line, under_odds) pairs across all present lines
    are consistent (U-prob non-decreasing in line, 2pp tolerance for margin).
    Returns True on empty/single-line sets — nothing to compare."""
    by_line: dict[int, dict[str, float]] = {}
    for market, selection, odds in ou_rows:
        if not market.startswith("over_under_"):
            continue
        try:
            cents = int(market.split("_")[-1])
        except ValueError:
            continue
        by_line.setdefault(cents, {})[selection] = odds
    lines_with_under = [(c, d["under"]) for c, d in sorted(by_line.items())
                        if "under" in d and d["under"] > 1.0]
    if len(lines_with_under) < 2:
        return True
    prev_u_prob = 0.0
    for _cents, u_odds in lines_with_under:
        u_prob = 1.0 / u_odds
        # 2pp tolerance covers normal 2-8% overround edge cases.
        if u_prob < prev_u_prob - 0.02:
            return False
        prev_u_prob = max(prev_u_prob, u_prob)
    return True


_OU_DUMP_DIR = Path(__file__).resolve().parents[2] / "dev" / "active"
_OU_DUMP_LIMIT = int(os.getenv("COOLBET_OU_DUMP_LIMIT", "3"))
_ou_dumps_written = 0


def _dump_ou_mislabel_payload(
    match_id: str,
    coolbet_markets: list[dict],
    ou_buffer: list[tuple[str, str, float, float | None]],
) -> None:
    """COOLBET-OU-LINE-MISLABEL-RCA (2026-08-24): capture the raw payload the
    first few times the monotonicity guard fires.

    The 2026-08-22 guard proved the data was wrong but not which market was
    doing the clobbering, because we store parsed rows and never the source
    names. Without a dump the RCA can only be argued from Coolbet's market
    catalogue. This writes one JSON per offending match (capped, so a bad day
    can't fill the disk) containing every market's name/mtid/line and the OU
    rows we were about to store — enough to name the culprit in one look.

    Best-effort: a dump failure must never break odds ingestion.
    """
    global _ou_dumps_written
    if _ou_dumps_written >= _OU_DUMP_LIMIT:
        return
    try:
        _OU_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        path = _OU_DUMP_DIR / f"coolbet-raw-{match_id}.json"
        if path.exists():
            return
        payload = {
            "match_id": match_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "reason": "ou_monotonicity_guard_fired",
            "markets": [
                {
                    "name": m.get("name"),
                    "market_type_id": m.get("market_type_id"),
                    "line": m.get("line"),
                    "raw_line": m.get("raw_line"),
                    "outcomes": [
                        {"id": oc.get("id"), "name": oc.get("name"),
                         "result_key": oc.get("result_key")}
                        for oc in (m.get("outcomes") or [])
                    ],
                }
                for m in coolbet_markets
            ],
            "parsed_ou_rows": [
                {"market": m, "selection": sel, "odds": o, "line": ln}
                for m, sel, o, ln in ou_buffer
            ],
        }
        path.write_text(json.dumps(payload, indent=2, default=str))
        _ou_dumps_written += 1
        log.warning("coolbet-ou-monotonicity: raw payload dumped to %s", path)
    except Exception as exc:  # pragma: no cover — diagnostics must never break ingest
        log.warning("coolbet-ou-monotonicity: payload dump failed: %s", exc)


def store_coolbet_snapshots_for_match(
    match_id: str,
    coolbet_markets: list[dict],
    odds_map: dict[int, float],
    *,
    dry_run: bool,
    kickoff_iso: str = "",
) -> tuple[int, int, dict[str, int]]:
    """Parse + store all markets for one match. Returns (parsed, stored, by_market).

    COOLBET-OU-LINE-MISLABEL-2026-08-22: OU rows are buffered and dropped
    wholesale if U-probability fails monotonicity across lines — better zero
    goals-OU data than lying data. Non-OU markets (1x2, BTTS, DC, AH) are
    unaffected."""
    parsed = 0
    stored = 0
    by_market: dict[str, int] = {}
    minutes_to_ko = _minutes_to_kickoff(kickoff_iso)

    ou_buffer: list[tuple[str, str, float, float | None]] = []  # (market, sel, odds, line)
    non_ou_rows: list[tuple[str, str, float, float | None]] = []

    for mkt in coolbet_markets:
        for market, selection, odds, line in parse_market(mkt, odds_map):
            parsed += 1
            by_market[market] = by_market.get(market, 0) + 1
            if market.startswith("over_under_"):
                ou_buffer.append((market, selection, odds, line))
            else:
                non_ou_rows.append((market, selection, odds, line))

    ou_ok = _ou_rows_monotone([(m, s, o) for m, s, o, _ in ou_buffer])
    if not ou_ok:
        log.warning(
            "coolbet-ou-monotonicity: dropping %d OU rows for match %s "
            "(U-prob not monotone in line — likely mislabelled)",
            len(ou_buffer), match_id,
        )
        _dump_ou_mislabel_payload(match_id, coolbet_markets, ou_buffer)
        for m in {r[0] for r in ou_buffer}:
            by_market.pop(m, None)
        ou_buffer.clear()

    to_store = non_ou_rows + ou_buffer
    for market, selection, odds, line in to_store:
        if dry_run:
            continue
        try:
            store_coolbet_odds_snapshot(match_id, market, selection, odds,
                                        minutes_to_ko, handicap_line=line)
            stored += 1
        except Exception as e:
            log.warning("Store failed for %s %s: %s", market, selection, e)
    return parsed, stored, by_market



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
        # COOLBET-INGEST-ANON (2026-06-04): snapshot ingest is reads-only, no
        # JWT needed. Previously defaulted to authed CoolbetSession() which
        # blew the entire 30-min sweep when COOLBET_MANUAL_JWT expired — every
        # new fixture then showed ⚠ no match in /admin/place because no
        # Coolbet odds_snapshots ever got written.
        session = CoolbetSession(require_auth=False)

    matched_total = 0
    parsed_total = 0
    stored_total = 0
    by_market: dict[str, int] = {}

    seen_cb_ids: set[int] = set()  # dedup if same CB league mapped from multiple AF leagues
    league_fetch_failures = 0

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

        # Within-league fuzzy match (high precision because same league).
        # COOLBET-FUZZY-DATE-GUARD: also enforce kickoff within ±6h so a
        # two-leg cup tie or rescheduled fixture can't match the wrong leg.
        from datetime import datetime as _dt, timezone as _tz
        from workers.automation.coolbet_placer import (
            _parse_iso_start, _FUZZY_DATE_TOLERANCE_HOURS,
        )
        _tol_s = _FUZZY_DATE_TOLERANCE_HOURS * 3600
        for af_m in af_matches:
            af_key = f"{af_m['home']} vs {af_m['away']}".lower()
            af_date = af_m.get("date")
            if af_date is not None and getattr(af_date, "tzinfo", None) is None:
                af_date = af_date.replace(tzinfo=_tz.utc)
            best, best_score = None, 0
            for cb_e in cb_events:
                if af_date is not None:
                    cb_start = _parse_iso_start(cb_e.get("start"))
                    if cb_start is not None and abs((cb_start - af_date).total_seconds()) > _tol_s:
                        continue
                cb_key = f"{cb_e['home']} vs {cb_e['away']}".lower()
                sc = fuzz.token_sort_ratio(af_key, cb_key)
                if sc > best_score:
                    best, best_score = cb_e, sc
            if not best or best_score < 65:
                continue

            matched_total += 1
            # Pull markets+odds, store
            # Same guard as run_bulk — an unhandled ReadTimeout here would
            # abandon every remaining league in the sweep.
            try:
                markets = fetch_match_markets(session, int(best["id"]))
                odds_map = fetch_odds_for_markets(session, markets)
            except Exception as e:
                league_fetch_failures += 1
                log.warning("market fetch failed for %s vs %s (%s) — fixture "
                            "UNRESOLVED (%d in a row)",
                            af_m.get("home"), af_m.get("away"), e,
                            league_fetch_failures)
                if league_fetch_failures >= _MAX_CONSECUTIVE_FETCH_FAILURES:
                    log.error("Coolbet unreachable: %d consecutive market fetches "
                              "failed — aborting the league sweep.",
                              league_fetch_failures)
                    console.print("[red]Coolbet unreachable — league sweep aborted.[/red]")
                    return
                time.sleep(sleep_s)
                continue
            league_fetch_failures = 0
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


# ── Board sweep (COOLBET-INGEST-REWORK 2026-09-08) ────────────────────────────
#
# Walks COOLBET'S OWN category tree instead of AF's leagues, so it enumerates
# 100% of Coolbet's football board (~140 categories) in one pass and needs no
# hand-maintained AF→Coolbet league map for coverage. Each event is matched back
# to an AF fixture by the record-linkage matcher (country + kickoff slot + both
# team names, subset-safe) — cross-league false positives are impossible, and
# Coolbet's short names ('Stoke') match AF's full ones ('Stoke City').
#
# Two coverage limits, both on the AF side, not the sweep's (measured 2026-09-08):
#   • AF ingests a short fixture horizon; Coolbet prices weeks ahead. The
#     near-term filter drops Coolbet events beyond `horizon_hours` so we do not
#     waste calls on games AF has no fixture for yet.
#   • A handful of bottom-tier leagues (Finnish Nelonen/Kolmonen) exist on
#     Coolbet but never in AF, and carry no Pinnacle anchor, so they are
#     unbettable and correctly left unmatched.

_VIRTUAL_CATEGORY_HINTS = (
    "esoccer", "esports", "battle", "cyber", "efootball", "week #",
    "(2x", "min)", "legends", "valhalla", "valkyrie", "h2h gg",
)
_FO_TREE_URL = "https://www.coolbet.com/s/sbgate/category/fo-tree/et"


def _is_virtual_category(name: str | None) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _VIRTUAL_CATEGORY_HINTS)


def enumerate_coolbet_football_categories(session: "CoolbetSession") -> list[dict]:
    """Walk Coolbet's fo-tree and return every real (non-virtual) football leaf
    category as {id, name}. This is the whole board's index in ONE request —
    which makes it a single point of failure for the whole board sweep, so the
    fetch is RETRIED: an Imperva-fronted endpoint throws transient 30s read
    timeouts under load, and a single miss must not silently zero a whole pass."""
    tree = None
    for attempt in range(3):
        try:
            resp = session.get(_FO_TREE_URL, params={"country": "EE"})
            tree = resp.json()
            break
        except Exception as e:
            log.warning("fo-tree fetch failed (attempt %d/3): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    if tree is None:
        log.error("fo-tree unreachable after 3 attempts — board NOT enumerated this pass")
        return []

    def _find_football(node: dict):
        if node.get("name") == "Jalgpall":
            return node
        for c in node.get("children") or []:
            hit = _find_football(c)
            if hit:
                return hit
        return None

    roots = tree if isinstance(tree, list) else [tree]
    football = next((f for f in (_find_football(r) for r in roots) if f), None)
    if not football:
        log.warning("fo-tree carried no football subtree")
        return []

    leaves: list[dict] = []

    def _walk(node: dict):
        kids = node.get("children") or []
        if not kids and node.get("id"):
            if not _is_virtual_category(node.get("name")):
                leaves.append({"id": int(node["id"]), "name": node.get("name")})
        for c in kids:
            _walk(c)

    _walk(football)
    return leaves


def _load_af_candidates(horizon_hours: float) -> list[dict]:
    """AF fixtures from now-6h to now+horizon, with country — the candidate set
    the matcher blocks over. tz-aware kickoff for the slot comparison."""
    rows = execute_query(
        """
        SELECT m.id::text AS id, m.date AS ko, ht.name AS home, at2.name AS away,
               l.country AS country
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.date > NOW() - INTERVAL '6 hours'
          AND m.date < NOW() + (%s || ' hours')::interval
        """,
        (str(horizon_hours),),
    )
    for r in rows:
        if r["ko"] is not None and getattr(r["ko"], "tzinfo", None) is None:
            r["ko"] = r["ko"].replace(tzinfo=timezone.utc)
    return rows


# ── CATEGORY NEAR-TERM MEMO (BOARD-SWEEP-NEARTERM-SKIP, 2026-09-11) ──────────
#
# The board sweep fetched EVERY football category's event list on every pass,
# then discarded events beyond `--horizon-hours`. Measured on the live log:
# 802 events fetched, 215 near-term — we paid for 192 category requests every
# 30 minutes and threw away ~73% of the payload. `--horizon-hours` filtered
# AFTER the network cost, not before.
#
# That volume is very likely what keeps triggering the Imperva escalation: the
# runbook's §2 challenge is "usually triggered by our own request volume from
# one IP", and FlareSolverr's logs show a FRESH session passing while a REUSED
# one is challenged and times out at 60s. We were treating a self-inflicted
# load problem as an external block, which is why it recurred daily.
#
# So remember which categories had NOTHING near-term and stop paying for them
# every pass. Same shape as the league negative cache above, and the same two
# safeguards, for the same reason:
#
#   1. NEVER PERMANENT. A skipped category is re-probed every
#      `_CAT_PROBE_EVERY` passes. Without that the zero becomes true by
#      construction — we stop looking, so we never see Coolbet add fixtures,
#      so it stays zero forever.
#   2. FAIL OPEN. Any memo problem (missing, unreadable, corrupt) sweeps
#      EVERYTHING. A cache that fails closed would silently stop collecting
#      odds and look exactly like "Coolbet offers nothing" — the silent-failure
#      class this whole ingest epic exists to kill.
_CAT_MEMO_PATH = Path.home() / ".config" / "oddsintel" / "coolbet-category-nearterm.json"
_CAT_PROBE_EVERY = 6          # at :03/:33 this re-probes an empty category ~3-hourly


def _load_cat_memo() -> dict:
    """category_id -> consecutive passes with zero near-term events. Fail open."""
    try:
        import json as _json
        return _json.loads(_CAT_MEMO_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt memo must sweep everything
        return {}


def _save_cat_memo(memo: dict) -> None:
    try:
        import json as _json
        _CAT_MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CAT_MEMO_PATH.write_text(_json.dumps(memo, separators=(",", ":")))
    except Exception as e:  # noqa: BLE001 — never let bookkeeping break a sweep
        log.debug("category memo save failed (non-fatal): %s", e)


def _cat_should_skip(memo: dict, cat_id) -> bool:
    """True when this category has been empty for a while and this is not a
    probe pass. Probes on every _CAT_PROBE_EVERY-th consecutive empty streak."""
    streak = int(memo.get(str(cat_id), 0) or 0)
    return streak > 0 and (streak % _CAT_PROBE_EVERY) != 0


# A genuine Coolbet challenge answers in ~2s with a body. Anything that burns
# this long and returns nothing is our own stuck FS session (2026-09-11: every
# probe read exactly 60.4s / 0 bytes, which is a timeout, not a verdict).
_WEDGED_AFTER_S = 20.0


def probe_coolbet_reachable(*, session_name: str | None = None) -> dict:
    """ONE request. Is Coolbet answering this machine right now?

    COOLBET-PROBE (2026-09-11). Added because the only way to find out whether
    an Imperva escalation had decayed was to run a sweep — and running a sweep
    is precisely what sustains the escalation. That made the question
    unanswerable without making the answer worse.

    Deliberately minimal: a single `fo-tree` GET, the same call the sweep opens
    with, so a green probe means the sweep's first hop will work. It is cheap
    enough to poll occasionally while paused WITHOUT rebuilding the footprint
    we just removed.

    Distinguishes the three states that look alike from the outside:
      * `ok`        — answered with a usable body
      * `challenged`— Coolbet answered, but with a challenge page instead of a
                      board (small, unparseable body). This is §2/§6: still
                      flagged, keep waiting.
      * `wedged`    — the FS session itself is stuck: HTTP 500 after a long
                      fixed delay, with ZERO bytes back. Distinguished from
                      `challenged` on 2026-09-11 (see below) because the remedy
                      differs — waiting for a flag to decay does nothing for a
                      broken session.
      * `down`      — our own plumbing (FS not running, import failure). Ours
                      to fix, nothing to do with Coolbet.

    WHY `wedged` IS A SEPARATE STATE (2026-09-11). Every probe that day returned
    "challenged, 60.4s, 0 bytes". The identical duration was the tell: 60.4s is
    a fixed timeout, not a verdict Coolbet rendered. Probing on a FRESH FS
    session instead answered in 1.8s with an 881-byte challenge page — a real
    answer. So two different things were being reported under one name, and the
    long-lived `coolbet_prod` session was ALSO broken on top of the live
    challenge. Collapsing them hid one behind the other.

    `session_name` overrides the FS session for this probe only. It exists so
    the two states above can be told apart; it is NOT a way to cycle sessions
    past a challenge, and a fresh session showing `challenged` (as it did on
    2026-09-11) means the flag is live and the footprint must stay paused.

    Returns {"state", "detail", "elapsed_s", "bytes"}. Never raises.
    """
    import time as _t
    t0 = _t.time()
    try:
        sess = CoolbetSession(require_auth=False, fs_session_name=session_name)
    except Exception as e:  # noqa: BLE001
        return {"state": "down", "detail": f"session init failed: {e}",
                "elapsed_s": round(_t.time() - t0, 1), "bytes": 0}
    try:
        resp = sess.get(_FO_TREE_URL, params={"country": "EE"})
        body = getattr(resp, "text", "") or ""
        n = len(body)
        # A challenge page is small and is not JSON; a real board is large.
        try:
            resp.json()
            parsed = True
        except Exception:  # noqa: BLE001
            parsed = False
        if parsed and n > 500:
            return {"state": "ok", "detail": "fo-tree answered with a parseable board",
                    "elapsed_s": round(_t.time() - t0, 1), "bytes": n}
        return {"state": "challenged",
                "detail": f"answered but not a usable board ({n} bytes, "
                          f"parsed={parsed}) — treat as still flagged",
                "elapsed_s": round(_t.time() - t0, 1), "bytes": n}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        elapsed = round(_t.time() - t0, 1)
        looks_blocked = ("500" in msg or "timed out" in msg.lower()
                         or "timeout" in msg.lower())
        if not looks_blocked:
            return {"state": "down", "detail": msg[:200],
                    "elapsed_s": elapsed, "bytes": 0}
        # A long, zero-byte failure is the FS session being stuck, not Coolbet
        # rendering a verdict — Coolbet's own challenge comes back in ~2s with a
        # body. Re-probing a wedged session forever teaches nothing.
        if elapsed >= _WEDGED_AFTER_S:
            return {"state": "wedged",
                    "detail": f"{msg[:160]} — {elapsed}s with 0 bytes on FS "
                              f"session {getattr(sess, '_fs_session_name', '?')!r}; "
                              f"that is a stuck session, not a challenge verdict. "
                              f"Re-probe with --fresh-session to see what Coolbet "
                              f"itself says before concluding anything about the flag",
                    "elapsed_s": elapsed, "bytes": 0}
        return {"state": "challenged", "detail": msg[:200],
                "elapsed_s": elapsed, "bytes": 0}


def run_board_sweep(
    *,
    dry_run: bool = False,
    horizon_hours: float = 96.0,
    sleep_s: float = 0.4,
    session: "CoolbetSession | None" = None,
) -> dict:
    """Enumerate Coolbet's whole football board → match each near-term event to
    an AF fixture (country+date+names) → store its markets. Returns a counters
    dict. Never raises out of the scheduler wrapper. Aborts if Coolbet goes
    unreachable (consecutive market-fetch failures)."""
    from workers.automation.coolbet_session import CoolbetSession
    from workers.automation.coolbet_matching import match_event_to_af
    from workers.automation.coolbet_placer import fetch_events_for_league, _parse_iso_start

    c = {"categories": 0, "events_seen": 0, "near_term": 0, "matched": 0,
         "stored_rows": 0, "unmatched": 0, "fetch_fails": 0}
    if session is None:
        session = CoolbetSession(require_auth=False)

    cats = enumerate_coolbet_football_categories(session)
    if not cats:
        # Loud, not silent: an empty enumeration means the whole pass wrote
        # nothing, which starves the placement price feed. Surface it as an
        # ERROR so log/output monitoring catches it (the next :03/:33 run retries).
        log.error("Board sweep enumerated 0 categories — NO Coolbet odds written this pass "
                  "(fo-tree unreachable). Feed will be stale until the next pass succeeds.")
        console.print("[red]Board sweep: no categories enumerated — nothing written.[/red]")
        return c
    af = _load_af_candidates(horizon_hours)
    log.info("Board sweep — %d Coolbet categories, %d AF candidate fixtures (horizon %.0fh)",
             len(cats), len(af), horizon_hours)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)
    consecutive_fails = 0

    # BOARD-SWEEP-NEARTERM-SKIP (2026-09-11) — see _load_cat_memo above.
    cat_memo = _load_cat_memo()
    c["cats_skipped_empty"] = 0
    for idx, cat in enumerate(cats, 1):
        if _cat_should_skip(cat_memo, cat["id"]):
            # Still count the streak up so the probe fires on schedule.
            cat_memo[str(cat["id"])] = int(cat_memo.get(str(cat["id"]), 0) or 0) + 1
            c["cats_skipped_empty"] += 1
            continue
        try:
            events = fetch_events_for_league(session, cat["id"])
        except Exception as e:
            log.warning("category %s events fetch failed (%s)", cat["name"], e)
            continue
        cat_near_term = 0
        for ev in events:
            c["events_seen"] += 1
            if (ev.get("status") not in (None, "OPEN")) or not ev.get("home") or not ev.get("away"):
                continue
            cb_start = _parse_iso_start(ev.get("start"))
            # near-term filter: skip games AF cannot have a fixture for yet
            if cb_start is not None and cb_start > horizon:
                continue
            c["near_term"] += 1
            cat_near_term += 1
            af_row, score, _second = match_event_to_af(
                ev["home"], ev["away"], ev.get("iso"), cb_start, af,
            )
            if af_row is None:
                c["unmatched"] += 1  # genuinely AF-absent OR beyond confidence — leave it
                continue
            c["matched"] += 1
            try:
                markets = fetch_match_markets(session, int(ev["id"]))
                odds_map = fetch_odds_for_markets(session, markets)
            except Exception as e:
                consecutive_fails += 1
                c["fetch_fails"] += 1
                log.warning("market fetch failed for %s vs %s (%s) — %d in a row",
                            ev["home"], ev["away"], e, consecutive_fails)
                if consecutive_fails >= _MAX_CONSECUTIVE_FETCH_FAILURES:
                    log.error("Coolbet unreachable: %d consecutive fetch failures — aborting board sweep.",
                              consecutive_fails)
                    console.print("[red]Coolbet unreachable — board sweep aborted.[/red]")
                    return c
                time.sleep(sleep_s)
                continue
            consecutive_fails = 0
            _parsed, stored, _bm = store_coolbet_snapshots_for_match(
                af_row["id"], markets, odds_map,
                dry_run=dry_run, kickoff_iso=ev.get("start") or "",
            )
            c["stored_rows"] += stored
        # Empty streak: reset the moment anything near-term shows up, so a
        # category that starts carrying fixtures is swept again immediately.
        cat_memo[str(cat["id"])] = 0 if cat_near_term else \
            int(cat_memo.get(str(cat["id"]), 0) or 0) + 1
        if idx % 40 == 0:
            log.info("  …%d/%d categories, matched=%d stored=%d", idx, len(cats), c["matched"], c["stored_rows"])
        time.sleep(sleep_s)
    _save_cat_memo(cat_memo)
    if c["cats_skipped_empty"]:
        log.info("board sweep: skipped %d/%d categories with no near-term "
                 "fixtures (re-probed every %d passes)",
                 c["cats_skipped_empty"], len(cats), _CAT_PROBE_EVERY)

    console.print(
        f"[cyan]Board sweep {'[DRY-RUN] ' if dry_run else ''}— {c['categories'] or len(cats)} categories, "
        f"{c['events_seen']} events ({c['near_term']} near-term), matched {c['matched']}, "
        f"unmatched {c['unmatched']}, stored {c['stored_rows']} rows[/cyan]"
    )
    c["categories"] = len(cats)

    # COOLBET-SWEEP-ROWS-WRITTEN-ALARM (2026-09-10): output-based health check for
    # the silent-failure class this epic exists to kill. A sweep that WALKED the
    # board (events_seen > 0) but wrote NOTHING (stored_rows == 0) looks identical
    # to "Coolbet genuinely offers nothing" from the outside — but it almost always
    # means a matcher or parser regression, not a real coverage gap. Fire a deduped
    # Telegram alert (3h window) so it can't spam. Never raises. Only the hard-zero
    # case; near-zero is deliberately out of scope. Skipped in dry_run (writes 0 by
    # design) — the `not dry_run` guard is load-bearing.
    try:
        if not dry_run and c["events_seen"] > 0 and c["stored_rows"] == 0:
            from workers.notify.telegram import send_telegram
            send_telegram(
                f"🟠 Coolbet board sweep ran but stored 0 rows — likely a matcher/parse regression, "
                f"not a coverage gap. events_seen={c['events_seen']}, "
                f"near_term={c['near_term']}, matched={c['matched']}, "
                f"stored_rows={c['stored_rows']}. The placement price feed is starving "
                f"until a later pass writes rows.",
                dedup_key="coolbet-sweep-zero-rows", dedup_window_s=10800)
    except Exception as e:  # noqa: BLE001
        log.debug("coolbet zero-rows alarm failed (non-fatal): %s", e)

    # ODDS-ARRIVAL HOOK (2026-09-11). Fresh Coolbet prices have just landed, so
    # regenerate the real-money candidates NOW instead of waiting for the
    # :10/:40 poll. Coolbet sweeps at :03/:33 and the generators ran at
    # :10/:40, so a qualifying price could sit unused for ~30 minutes — for a
    # match kicking off in 40 that was the entire window. It is also what let
    # the generator gate Nancy on a 14.8h-old quote of 3.10 while the live
    # price was 3.25 and clearing.
    #
    # `on_odds_written` never raises: collecting odds is this function's job and
    # must survive a pick-generation failure.
    if c.get("stored_rows"):
        from workers.automation.pick_generator import on_odds_written
        on_odds_written("Coolbet")

    return c


# ── Bulk + one-shot drivers ───────────────────────────────────────────────────


# How many market fetches may fail back-to-back before we conclude Coolbet is
# gone. Each failure costs a ~30s read timeout, so grinding a 650-fixture sweep
# through a dead endpoint burns 5+ hours to learn nothing.
_MAX_CONSECUTIVE_FETCH_FAILURES = 5


# ── COOLBET-NEGATIVE-CACHE: league prior ──────────────────────────────────────
#
# Measured 2026-09-06 over 60 days: of 626 leagues with fixtures, **201 have
# never had a single Coolbet price**, and 115 of those carry >=10 fixtures --
# 3,719 fixtures, **15.6% of everything the sweep walks**. They are exactly what
# you would guess of an Estonian book: Calcutta Premier Division, Scottish
# Lowland League, Dutch Derde Divisie, Polish III Liga, Zimbabwe PSL. Coolbet
# was never going to price them, and every sweep pays ~30s per fixture to
# rediscover that.
#
# Two guards, because a naive "skip what has never worked" is self-fulfilling:
#
#  1. EVIDENCE FLOOR. A league needs >=`_NEG_MIN_FIXTURES` fixtures in the
#     window before its zero means anything. One fixture that missed is not a
#     pattern, and skipping on it would silently shrink coverage.
#  2. PROBE RATE. Even a skipped league lets ~1 in `_NEG_PROBE_EVERY` fixtures
#     through. Without this the zero becomes permanent by construction: we stop
#     looking, so we never see Coolbet add the league, so the cache says zero
#     forever. The probe keeps the evidence alive at ~5% of the saved cost.
#
# The source is `odds_snapshots` (did a price ever land), NOT the sweep's own
# miss counters -- those are polluted by outages, as 2026-09-06 demonstrated
# when a dead network wrote 236 false "no Coolbet event" results in one run.
_NEG_MIN_FIXTURES = 10
_NEG_PROBE_EVERY = 20
_NEG_WINDOW_DAYS = 60


def never_coolbet_league_ids() -> set[str]:
    """League ids with >= _NEG_MIN_FIXTURES fixtures and ZERO Coolbet prices."""
    try:
        # PERFORMANCE, and it matters: the obvious form of this query --
        # a correlated `EXISTS (SELECT 1 FROM odds_snapshots ...)` evaluated per
        # match -- takes **367 seconds** against a 53M-row odds_snapshots. That
        # is worse than the problem: the sweep would spend six minutes computing
        # which fixtures to skip in order to save thirty seconds each.
        #
        # Narrowing to the window FIRST, then collecting Coolbet's match_ids in
        # one pass and left-joining, returns the identical 115 leagues in
        # **0.9 seconds**. Measured 2026-09-07.
        rows = execute_query(
            """
            WITH win AS (
              SELECT m.id, m.league_id
                FROM matches m
               WHERE m.date >= now() - make_interval(days => %s)
                 AND m.date <  now()
            ),
            cb AS (
              SELECT DISTINCT o.match_id
                FROM odds_snapshots o
                JOIN win w ON w.id = o.match_id
               WHERE o.bookmaker = 'Coolbet'
            )
            SELECT w.league_id::text AS id
              FROM win w
              LEFT JOIN cb ON cb.match_id = w.id
             GROUP BY w.league_id
            HAVING COUNT(*) >= %s AND COUNT(cb.match_id) = 0
            """,
            (_NEG_WINDOW_DAYS, _NEG_MIN_FIXTURES),
        )
        return {r["id"] for r in rows}
    except Exception as exc:            # never let the optimisation break the sweep
        log.warning("negative-cache league prior unavailable (%s) — sweeping everything", exc)
        return set()


def apply_league_prior(matches: list[dict]) -> tuple[list[dict], int]:
    """Drop fixtures in never-Coolbet leagues, keeping ~1 in N as a probe."""
    never = never_coolbet_league_ids()
    if not never:
        return matches, 0
    kept, skipped = [], 0
    for i, m in enumerate(matches):
        if str(m.get("league_id") or "") in never and (i % _NEG_PROBE_EVERY) != 0:
            skipped += 1
            continue
        kept.append(m)
    return kept, skipped


def run_bulk(
    days: int, dry_run: bool, sleep_s: float, limit: int | None,
    *, bets_only: bool = False,
    long_pause_every: int = 15, long_pause_s: float = 20.0,
    session: "CoolbetSession | None" = None,
    kickoff_band: tuple[float, float] | None = None,
) -> None:
    if kickoff_band is not None:
        lo_h, hi_h = kickoff_band
        matches = load_matches_in_kickoff_band(lo_h, hi_h)
    elif bets_only:
        matches = load_value_bet_matches(days)
    else:
        matches = load_matches_in_window(days)
    if limit:
        matches = matches[:limit]
    if not matches:
        msg = "No pending value-bet matches in window." if bets_only else "No upcoming matches in DB window."
        console.print(f"[yellow]{msg}[/yellow]")
        return
    # COOLBET-NEGATIVE-CACHE league prior — see never_coolbet_league_ids().
    matches, _skipped_prior = apply_league_prior(matches)
    if _skipped_prior:
        console.print(f"[dim]league prior: skipped {_skipped_prior} fixtures in "
                      f"never-Coolbet leagues (~1 in {_NEG_PROBE_EVERY} kept as a probe)[/dim]")
        log.info("negative-cache league prior skipped %d fixtures", _skipped_prior)

    if kickoff_band is not None:
        label = "matches from DB"
        window = f"kickoff {kickoff_band[0]:g}-{kickoff_band[1]:g}h"
    else:
        label = "value-bet matches" if bets_only else "matches from DB"
        window = f"window={days}d"
    console.print(f"[cyan]Loaded {len(matches)} {label} ({window}){' [DRY-RUN]' if dry_run else ''}[/cyan]")

    if session is None:
        # COOLBET-INGEST-ANON: see run_league_sweep — reads-only path.
        session = CoolbetSession(require_auth=False)
    category_cache: list[dict] | None = None
    # COOLBET-BULK-LISTING-FIRST: latch so a blocked search logs once, not
    # ~1,200 times, and never aborts the sweep.
    search_blocked = False

    matched = 0
    parsed_total = 0
    stored_total = 0
    by_market: dict[str, int] = {}
    missed_leagues: dict[str, int] = {}
    # Fixtures we could not resolve because Coolbet was unreachable — kept
    # strictly apart from genuine "Coolbet does not list this game" misses.
    unresolved_leagues: dict[str, int] = {}
    matched_leagues: dict[str, int] = {}
    consecutive_fetch_failures = 0

    for i, m in enumerate(matches, 1):
        home, away = m["home"], m["away"]
        league = m.get("league") or "—"
        # COOLBET-FUZZY-DATE-GUARD: pass DB kickoff so the matcher can reject
        # same-team different-day candidates (reserves vs first team, multi-leg
        # ties, women vs men).
        match_date = m.get("date")
        # ── COOLBET-BULK-LISTING-FIRST-2026-09-06 ──────────────────────────
        # This used to call `search_coolbet_event` FIRST, for EVERY match, and
        # only load the bulk fo-category listing on a miss. With a 2-day window
        # that is ~1,200 search requests per sweep, every 30 minutes, against
        # one endpoint — versus ONE request for the whole listing.
        #
        # Two problems with search-first, both observed in production today:
        #   1. `_do_search` raises CoolbetSearchBlocked on a 403, which is NOT
        #      caught here — so a single blocked search killed the entire sweep
        #      and lost all ~1,200 matches. That is exactly how the feed died
        #      this morning ("search/v2 returned HTTP 403 for query 'Vik'").
        #   2. Hammering one endpoint ~1,200 times per sweep is the behaviour
        #      most likely to attract rate-based blocking in the first place.
        #
        # Bulk-first fixes both and is strictly less load on Coolbet. The
        # fuzzy matcher used against the listing is the same one that was
        # already trusted as the fallback path, so match quality is unchanged;
        # search is now only consulted for the residue the listing misses.
        #
        # ⚠️ CORRECTION 2026-09-06, same day: `fo-category` currently returns
        # **404, not 403** — with valid cookies, so the request reaches Coolbet
        # and the path simply does not exist. The endpoint is retired, exactly
        # as the `except` below always suspected. So the 1,200-requests-to-1
        # saving is NOT being realised today; the listing fetch costs one failed
        # request per sweep and every match still goes through search.
        #
        # The ordering is kept deliberately rather than reverted, because the
        # half that DOES pay off is the resilience below — a blocked search no
        # longer discards the whole sweep — and because if Coolbet restores a
        # bulk listing (or one is found at a new path) this becomes correct for
        # free. Finding the current bulk endpoint is filed as a follow-up.
        if category_cache is None:
            try:
                category_cache = fetch_coolbet_events(session)
                console.print(f"[dim]fo-category listing: {len(category_cache)} events "
                              f"(1 request, replaces ~{len(matches)} searches)[/dim]")
            except Exception as e:
                # fo-category has 404'd in production at least once (Coolbet
                # seems to have moved or retired it). Degrade to search-only.
                log.warning("fo-category unavailable (%s) — falling back to search-only", e)
                category_cache = []

        ev = fuzzy_match_event(home, away, category_cache, match_date) if category_cache else None
        unreachable = False
        if ev is None and not search_blocked:
            # Residue only. A block here must NOT kill the sweep — everything
            # matched from the listing is still worth storing.
            try:
                ev = search_coolbet_event(session, home, away, match_date)
            except Exception as e:
                log.warning("Coolbet search unavailable (%s) — no further searches "
                            "this sweep; remaining fixtures are UNRESOLVED, not absent", e)
                search_blocked = True
                ev = None
        if ev is None and search_blocked and not category_cache:
            # No bulk listing AND no search: there is no path by which any
            # remaining fixture could ever match. Grinding on would spend hours
            # manufacturing false "no Coolbet event" rows. Stop loudly.
            log.error(
                "Coolbet unreachable: bulk listing empty and search blocked at "
                "fixture %d/%d. Aborting the sweep — every remaining fixture "
                "would be recorded as absent when it is merely unresolved.",
                i, len(matches),
            )
            console.print("[red]Coolbet unreachable — sweep aborted "
                          f"at {i}/{len(matches)} (no listing, no search).[/red]")
            break
        if ev is None and search_blocked:
            # COOLBET-SEARCH-BLOCKED-FALSE-NEGATIVES (2026-09-06): the latch was
            # set here but never read, so every remaining fixture still called
            # search — each one timing out at ~31s — while `if not
            # search_blocked` suppressed the warning. The 2026-09-06 17:03 sweep
            # spent 125 minutes producing 236 of 236 "no Coolbet event" results,
            # Valencia vs Barcelona among them. A dead network was silently
            # recorded as "Coolbet does not have this game".
            unreachable = True
        if ev is None:
            if unreachable:
                # NOT a miss. Counting it as one would poison `missed_leagues`,
                # which is the statistic the league-coverage prior is built on —
                # an outage would teach us that La Liga has no Coolbet coverage.
                unresolved_leagues[league] = unresolved_leagues.get(league, 0) + 1
                log.info("[%d/%d] UNRESOLVED (search blocked): %s vs %s (%s)",
                         i, len(matches), home, away, league)
            else:
                missed_leagues[league] = missed_leagues.get(league, 0) + 1
                log.info("[%d/%d] no Coolbet event: %s vs %s (%s)", i, len(matches), home, away, league)
        else:
            matched_leagues[league] = matched_leagues.get(league, 0) + 1

            # New flow: fetch markets (fo-match + sidebets), then fetch odds
            # (split by simple vs line endpoint), then stitch.
            # COOLBET-MARKET-FETCH-UNGUARDED (2026-09-06): these two calls had
            # no exception handling, so a single transient ReadTimeout raised
            # straight out of run_bulk and killed the whole sweep — which is
            # exactly what happened at 16:52 local, abandoning 650 fixtures
            # mid-run. A per-fixture network failure must cost that fixture,
            # not the sweep.
            try:
                markets = fetch_match_markets(session, int(ev["id"]))
                odds_map = fetch_odds_for_markets(session, markets)
            except Exception as e:
                consecutive_fetch_failures += 1
                unresolved_leagues[league] = unresolved_leagues.get(league, 0) + 1
                log.warning("[%d/%d] market fetch failed for %s vs %s (%s) — "
                            "fixture UNRESOLVED (%d in a row)",
                            i, len(matches), home, away, e,
                            consecutive_fetch_failures)
                if consecutive_fetch_failures >= _MAX_CONSECUTIVE_FETCH_FAILURES:
                    log.error("Coolbet unreachable: %d consecutive market fetches "
                              "failed. Aborting the sweep rather than spending "
                              "~30s per fixture on a dead endpoint.",
                              consecutive_fetch_failures)
                    console.print("[red]Coolbet unreachable — sweep aborted "
                                  f"at {i}/{len(matches)}.[/red]")
                    break
                time.sleep(sleep_s)
                continue
            consecutive_fetch_failures = 0
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
    all_leagues = sorted(set(matched_leagues) | set(missed_leagues) | set(unresolved_leagues))
    if all_leagues:
        t3 = Table(show_header=True, title="By league (matched / missed / unresolved)")
        t3.add_column("League")
        t3.add_column("Matched", justify="right")
        t3.add_column("Missed", justify="right")
        t3.add_column("Unresolved", justify="right")
        t3.add_column("Match %", justify="right")
        for lg in all_leagues:
            mt = matched_leagues.get(lg, 0)
            ms = missed_leagues.get(lg, 0)
            un = unresolved_leagues.get(lg, 0)
            # Match % is computed over RESOLVED fixtures only. Including
            # unreachable ones would read as poor Coolbet coverage when the
            # truth is that we never got to ask.
            pct = 100.0 * mt / (mt + ms) if (mt + ms) else 0
            t3.add_row(lg, str(mt), str(ms), str(un), f"{pct:.0f}%")
        console.print(t3)

    total_unresolved = sum(unresolved_leagues.values())
    if total_unresolved:
        console.print(
            f"[yellow]{total_unresolved} fixtures UNRESOLVED (Coolbet unreachable) — "
            "these are not evidence of missing coverage and must not be fed to any "
            "league-coverage prior.[/yellow]"
        )


def run_one_shot(match_id: str, raw: bool = False) -> None:
    rows = execute_query(
        """
        SELECT m.id::text AS id, m.date AS date,
               ht.name AS home, at2.name AS away,
               l.name AS league,
               m.league_id::text AS league_id
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

    # COOLBET-INGEST-ANON: single-match CLI debug — pure read path.
    session = CoolbetSession(require_auth=False)
    match_date = m.get("date")
    ev = search_coolbet_event(session, m["home"], m["away"], match_date)
    if ev is None:
        console.print("[dim]Search miss — loading full fo-category[/dim]")
        try:
            ev = fuzzy_match_event(m["home"], m["away"], fetch_coolbet_events(session),
                                   match_date, match_id=m.get("id"))
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
    if raw:
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
    ap.add_argument("--probe", action="store_true",
                    help="ONE request: is Coolbet answering right now? Prints "
                         "ok|challenged|wedged|down. Safe to poll while paused — it "
                         "does not rebuild the footprint. "
                         "Exit 0=ok, 1=challenged, 2=down, 3=wedged.")
    ap.add_argument("--fresh-session", action="store_true",
                    help="With --probe: use a throwaway FS session instead of the "
                         "long-lived one. DIAGNOSTIC ONLY — it tells a wedged "
                         "session apart from a live challenge. A fresh session that "
                         "still reports `challenged` means the flag IS live and the "
                         "footprint must stay paused; this is not a way around it.")
    ap.add_argument("--kickoff-band", metavar="LO:HI",
                    help="Sweep only fixtures kicking off in [LO,HI) hours from "
                         "now, e.g. '0:6'. Overrides --days. Cuts footprint AND "
                         "improves freshness where placement happens — see "
                         "load_matches_in_kickoff_band.")
    ap.add_argument("--limit", type=int, help="Cap bulk to first N matches (testing)")
    ap.add_argument("--sleep", type=float, default=0.25, help="Seconds between sidebets calls")
    ap.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    ap.add_argument("--raw", action="store_true",
                    help="With --match-id: dump all raw market names + type_ids before parsing. "
                         "Use this to discover unknown market_type_ids (AH, DC, etc.) so "
                         "_MTID_* sets can be updated.")
    ap.add_argument("--bets-only", action="store_true",
                    help="Bulk only over matches that have a pending value bet (active bots)")
    ap.add_argument("--board", action="store_true",
                    help="COOLBET-INGEST-REWORK: walk Coolbet's OWN category tree (100%% of the "
                         "board, no league map) and match to AF via country+date+names, instead "
                         "of per-match cross-league search. Supersedes the default bulk sweep.")
    ap.add_argument("--horizon-hours", type=float, default=96.0,
                    help="With --board: only consider Coolbet events kicking off within this many "
                         "hours (near-term filter; default 96)")
    args = ap.parse_args()

    # COOLBET-PROBE: deliberately ABOVE the pause check. The whole point of the
    # probe is to answer "has the Imperva flag decayed yet?" WHILE paused —
    # otherwise the only way to find out is to resume the sweep, which is what
    # sustains the escalation in the first place. One request; safe to poll.
    if getattr(args, "probe", False):
        _sess = None
        if getattr(args, "fresh_session", False):
            import time as _time
            _sess = f"coolbet_probe_{int(_time.time())}"
        try:
            r = probe_coolbet_reachable(session_name=_sess)
        finally:
            # A throwaway session must actually be thrown away — otherwise each
            # diagnostic run leaves a Chrome context behind in FlareSolverr and
            # a habit of probing slowly exhausts it.
            if _sess:
                try:
                    from workers.automation.coolbet_session import _fs_call
                    _fs_call({"cmd": "sessions.destroy", "session": _sess},
                             timeout_s=30)
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass
        colour = {"ok": "green", "challenged": "yellow",
                  "wedged": "magenta", "down": "red"}[r["state"]]
        console.print(f"[{colour}]coolbet probe: {r['state'].upper()}[/{colour}] "
                      f"({r['elapsed_s']}s, {r['bytes']} bytes) — {r['detail']}")
        if r["state"] == "challenged":
            console.print("[dim]still flagged — keep the footprint paused and "
                          "re-probe later; do NOT resume the sweep to test it[/dim]")
        elif r["state"] == "wedged":
            console.print("[dim]our own FS session is stuck — this says NOTHING "
                          "about whether the flag decayed. Re-run with "
                          "--fresh-session to get Coolbet's actual answer.[/dim]")
        sys.exit({"ok": 0, "challenged": 1, "down": 2, "wedged": 3}[r["state"]])

    # COOLBET-DAEMONS-PAUSE: the global footprint pause (set from /admin/shadow-bots
    # to calm Imperva). Skip the sweep runs — a manual --match-id inspect is still
    # allowed for debugging.
    if not args.match_id:
        from workers.automation.coolbet_state import is_daemons_paused
        paused, reason = is_daemons_paused()
        if paused:
            print(f"coolbet daemons PAUSED ({reason or 'no reason'}) — skipping Coolbet "
                  f"sweep to reduce Imperva footprint")
            return

    if args.match_id:
        run_one_shot(args.match_id, raw=args.raw)
    elif args.board:
        run_board_sweep(dry_run=args.dry_run, horizon_hours=args.horizon_hours, sleep_s=args.sleep)
    else:
        band = None
        if getattr(args, "kickoff_band", None):
            try:
                _lo, _hi = args.kickoff_band.split(":")
                band = (float(_lo), float(_hi))
                if band[0] < 0 or band[1] <= band[0]:
                    raise ValueError("need 0 <= LO < HI")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]--kickoff-band must be LO:HI hours "
                              f"(e.g. 0:6) — {e}[/red]")
                return 2
        run_bulk(args.days, args.dry_run, args.sleep, args.limit,
                 bets_only=args.bets_only, kickoff_band=band)


if __name__ == "__main__":
    main()
