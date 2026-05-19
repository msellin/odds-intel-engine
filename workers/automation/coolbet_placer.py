"""
Coolbet automated bet placer.

Flow:
  1. Query DB for today's simulated_bets with edge > threshold and no real_bet yet.
  2. Fetch Coolbet football events (fo-category endpoint).
  3. Fuzzy-match team names → Coolbet matchId.
  4. Get market details via fo-market/sidebets → betOfferId + outcomeId.
  5. Verify current odds via sb-odds/current/fo → also gets oddsId UUID needed for bet.
  6. Place bet via POST /s/bets/bets.
  7. Write to real_bets table.

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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rapidfuzz import fuzz, process as rfprocess
from workers.api_clients.db import execute_query
from workers.api_clients.supabase_client import store_real_bet
from workers.automation.coolbet_session import CoolbetSession

log = logging.getLogger(__name__)

_CATEGORY_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_SIDEBETS_URL  = "https://www.coolbet.com/s/sbgate/sports/fo-market/sidebets"
_ODDS_URL      = "https://www.coolbet.com/s/sb-odds/odds/current/fo"
_BET_URL       = "https://www.coolbet.com/s/bets/bets"

_FOOTBALL_CATEGORY_ID = 62
_DEFAULT_STAKE   = float(os.getenv("COOLBET_STAKE",        "10.0"))
_MIN_EDGE        = float(os.getenv("COOLBET_MIN_EDGE",      "0.03"))
_ODDS_TOLERANCE  = float(os.getenv("COOLBET_ODDS_TOLERANCE","0.05"))
_FUZZY_THRESHOLD = 70


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
    """Return today's simulated_bets qualifying for automated placement."""
    rows = execute_query(
        """
        SELECT
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
            m.date            AS match_date
        FROM simulated_bets sb
        JOIN bots          b   ON b.id   = sb.bot_id
        JOIN matches       m   ON m.id   = sb.match_id
        JOIN teams         ht  ON ht.id  = m.home_team_id
        JOIN teams         at2 ON at2.id = m.away_team_id
        WHERE sb.result          = 'pending'
          AND DATE(m.date)       = CURRENT_DATE
          AND m.date             > NOW()
          AND sb.edge_percent    >= %s * 100
          AND NOT EXISTS (
              SELECT 1 FROM real_bets rb
              WHERE rb.match_id  = sb.match_id
                AND rb.market    = sb.market
                AND rb.selection = sb.selection
                AND DATE(rb.placed_at) = CURRENT_DATE
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


def fetch_sidebets(session: CoolbetSession, match_id: int) -> list[dict]:
    """
    GET /s/sbgate/sports/fo-market/sidebets?matchId=... for a single match.
    Returns bet_offers in same format as _parse_event.
    """
    resp = session.get(_SIDEBETS_URL, params={
        "matchId":    match_id,
        "country":    "EE",
        "language":   "en",
        "layout":     "EUROPEAN",
    })
    if resp.status_code != 200:
        log.warning("sidebets %d returned %d", match_id, resp.status_code)
        return []

    data = resp.json()
    bet_offers = []
    for bo in data.get("betOffers") or []:
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
        log.debug("No Coolbet event for '%s vs %s' (best %d)", home, away, score)
        return None
    log.debug("Matched '%s vs %s' → '%s' (score %d)", home, away, event_keys[idx], score)
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

def place_all_bets(execute: bool = False, min_edge: float | None = None) -> list[dict]:
    """
    Run the full placement cycle.

    Args:
        execute: False = dry-run (print only); True = call API and write real_bets.
        min_edge: Override COOLBET_MIN_EDGE for this run (fraction, e.g. 0.03).

    Returns:
        List of result dicts per candidate bet.
    """
    global _MIN_EDGE
    if min_edge is not None:
        _MIN_EDGE = min_edge

    session = CoolbetSession()
    pending = load_qualified_bets()

    if not pending:
        log.info("No qualifying bets found for today.")
        return []

    log.info("Found %d qualifying simulated_bets to evaluate", len(pending))
    events = fetch_coolbet_events(session)

    results = []
    for bet in pending:
        home       = bet["home_team"]
        away       = bet["away_team"]
        mkt        = bet["market"]
        sel        = bet["selection"]
        model_odds = float(bet["model_odds"])
        edge_pct   = float(bet["edge_percent"])
        label      = f"{home} vs {away} | {mkt} {sel} @ {model_odds:.3f} (edge {edge_pct:+.2f}%)"

        # 1. Find Coolbet event
        ev = fuzzy_match_event(home, away, events)
        if ev is None:
            log.info("No Coolbet event for %s — skipping", label)
            results.append({**bet, "outcome": "no_event"})
            continue

        coolbet_match_id = ev["id"]

        # 2. Get market details — use sidebets for richer data, fall back to fo-category
        if execute:
            bet_offers = fetch_sidebets(session, coolbet_match_id)
        else:
            bet_offers = ev["bet_offers"]  # fo-category already has basic offers

        bo_id, oc_id, ev_odds = find_market_outcome(bet_offers, mkt, sel)
        if bo_id is None:
            log.info("Market %s/%s not found for %s (Coolbet matchId=%s)",
                     mkt, sel, label, coolbet_match_id)
            results.append({**bet, "outcome": "no_market"})
            continue

        if not execute:
            print(f"  [DRY-RUN] {label}  →  coolbet_match={coolbet_match_id} "
                  f"bo={bo_id} oc={oc_id} coolbet_odds={ev_odds:.3f} "
                  f"stake=€{_DEFAULT_STAKE:.2f}")
            results.append({**bet, "outcome": "dry_run",
                             "coolbet_match_id": coolbet_match_id,
                             "bet_offer_id": bo_id, "outcome_id": oc_id,
                             "ev_odds": ev_odds})
            continue

        # 3. Verify live odds and get oddsId UUID (required for bet payload)
        live_odds, odds_uuid = get_live_odds_and_id(session, bo_id, oc_id, model_odds)
        if live_odds is None:
            results.append({**bet, "outcome": "odds_dropped"})
            continue
        if odds_uuid is None:
            log.warning("Could not get oddsId UUID for %s — cannot place bet", label)
            results.append({**bet, "outcome": "no_odds_uuid"})
            continue

        # 4. Place bet
        match_name = f"{ev['home']} - {ev['away']}"
        try:
            ticket_id = _place_bet_api(
                session, oc_id, odds_uuid, _DEFAULT_STAKE, match_name, f"{mkt} {sel}"
            )
        except Exception as e:
            log.error("Placement failed for %s: %s", label, e)
            results.append({**bet, "outcome": "api_error", "error": str(e)})
            continue

        # 5. Write to real_bets
        real_bet_id = store_real_bet(
            match_id=str(bet["match_id"]),
            market=mkt,
            selection=sel,
            bookmaker="Coolbet",
            captured_odds=ev_odds,
            actual_odds=live_odds,
            stake=_DEFAULT_STAKE,
            bot_id=str(bet["bot_id"]),
            simulated_bet_id=str(bet["simulated_bet_id"]),
            notes=f"auto ticket={ticket_id} edge={edge_pct:+.2f}%",
        )
        log.info("✓ Placed %s  ticket=%s real_bet=%s", label, ticket_id, real_bet_id)
        results.append({**bet, "outcome": "placed",
                         "ticket_id": ticket_id, "real_bet_id": real_bet_id,
                         "live_odds": live_odds})

    return results
