"""BETFAIR EXCHANGE READER ([[#117]], 2026-09-24) — exchange back/lay prices + liquidity
for our fixtures, as a second sharp reference beside AF-Pinnacle.

WHY. ~41% of priced fixtures have no Pinnacle quote, and Pinnacle's own API blocks
datacenter IPs (#113/#115). Betfair Exchange prices are margin-free where liquid, and
on 7,328 main-league matches its close matched Pinnacle to 4 decimals (CSV history).

TRANSPORT. The exchange serves NO markets to our Finnish VPS (verified in a real
browser), so requests leave through a London Droplet: SOCKS 127.0.0.1:1082
(`oddsintel-egress@betfair`, a per-book fixed exit — #110 step 3), env
BETFAIR_EXCHANGE_PROXY. Never the Estonian exit. Reading public prices only: no
account, no login, no bets. The query shapes are the ones betfair.com's own exchange
page sends (recorded 2026-09-24).

COST. One navigation request lists every football MATCH_ODDS + OVER_UNDER_25 market
in the horizon (~200 events / ~390 markets); prices come 40 markets per request →
~11 requests per sweep. Metered as 'Betfair-Exchange' (workers/utils/footprint.py).

LIQUIDITY. Thin markets are placeholders (1.10 back / 110 lay, €0 matched). Every row
stores back, lay, their sizes and the market's matched volume; consumers decide what
counts as a price (see `is_liquid`). Stored in `exchange_quotes` (migration 395),
NOT odds_snapshots, until the sharpness study says how it may join the anchor.

    python3 -m workers.automation.betfair_exchange_feed --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

EXCHANGE = "Betfair-Exchange"
_PROXY = os.getenv("BETFAIR_EXCHANGE_PROXY", "socks5h://127.0.0.1:1082")
_AK = "nzIFcwyWhrlwYMrh"      # the public web-app key betfair.com's own page sends
_NAV = "https://scan-inbf.betfair.com/www/sports/navigation/facet/v1/search"
_PRICES = "https://ero.betfair.com/www/sports/exchange/readonly/v1/bymarket"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/140.0 Safari/537.36")
_BATCH = 40
_SLEEP_S = 1.0
_MARKETS = {"MATCH_ODDS": "1x2", "OVER_UNDER_25": "over_under_25"}

# Liquidity thresholds for treating a quote as a PRICE (not a placeholder).
MAX_SPREAD = 0.05             # lay/back − 1 on the runner
MIN_MARKET_MATCHED = 1000.0   # EUR matched on the whole market


def is_liquid(back: float | None, lay: float | None, market_matched: float | None,
              *, max_spread: float = MAX_SPREAD, min_matched: float = MIN_MARKET_MATCHED) -> bool:
    if not back or not lay or back <= 1.0 or lay < back:
        return False
    return (lay / back - 1.0) <= max_spread and (market_matched or 0.0) >= min_matched


def _session():
    from workers.utils.footprint import metered_session
    s = metered_session(EXCHANGE)
    s.headers.update({"User-Agent": _UA, "Accept": "application/json",
                      "Referer": "https://www.betfair.com/"})
    if _PROXY:
        s.proxies = {"http": _PROXY, "https": _PROXY}
    return s


def list_markets(sess, horizon_hours: float) -> tuple[dict, dict]:
    """→ (events {eventId: {...}}, markets {marketId: {...}}) for football in the horizon."""
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    body = {"filter": {"marketBettingTypes": ["ODDS"], "productTypes": ["EXCHANGE"],
                       "marketTypeCodes": list(_MARKETS), "contentGroup": {"language": "en"},
                       "turnInPlayEnabled": True, "maxResults": 0, "selectBy": "FIRST_TO_START",
                       "eventTypeIds": [1], "marketStartingAfter": now.strftime(fmt),
                       "marketStartingBefore": (now + timedelta(hours=horizon_hours)).strftime(fmt)},
            "facets": [{"type": "EVENT", "skipValues": 0, "maxValues": 1000, "applyNextTo": 0,
                        "next": {"type": "MARKET", "maxValues": 2}}],
            "currencyCode": "EUR", "locale": "en"}
    r = sess.post(_NAV, params={"_ak": _AK, "alt": "json"}, json=body, timeout=40)
    r.raise_for_status()
    a = r.json().get("attachments") or {}
    return a.get("events") or {}, a.get("markets") or {}


def fetch_prices(sess, market_ids: list[str]) -> dict:
    """→ {marketId: marketNode} (state + runners with best back/lay)."""
    out = {}
    for i in range(0, len(market_ids), _BATCH):
        chunk = market_ids[i:i + _BATCH]
        r = sess.get(_PRICES, timeout=40, params={
            "_ak": _AK, "alt": "json", "currencyCode": "EUR", "locale": "en",
            "marketIds": ",".join(chunk), "rollupLimit": "10", "rollupModel": "STAKE",
            "types": "MARKET_STATE,RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST"})
        r.raise_for_status()
        for et in r.json().get("eventTypes") or []:
            for en in et.get("eventNodes") or []:
                for mn in en.get("marketNodes") or []:
                    out[mn["marketId"]] = mn
        time.sleep(_SLEEP_S)
    return out


def split_event_name(name: str) -> tuple[str, str] | None:
    parts = (name or "").split(" v ")
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def runner_selection(market_type: str, runner_name: str, home: str, away: str) -> str | None:
    n = (runner_name or "").strip()
    if market_type == "MATCH_ODDS":
        if n == "The Draw":
            return "draw"
        if n == home:
            return "home"
        if n == away:
            return "away"
        return None
    if market_type == "OVER_UNDER_25":
        return {"Over 2.5 Goals": "over", "Under 2.5 Goals": "under"}.get(n)
    return None


def _best(ladder: list | None) -> tuple[float | None, float | None]:
    if not ladder:
        return None, None
    return ladder[0].get("price"), ladder[0].get("size")


def run_bulk(*, horizon_hours: float = 48, dry_run: bool = False) -> dict:
    from workers.automation.coolbet_explorer import _load_af_candidates
    from workers.automation.coolbet_matching import match_event_to_af
    c = {"events": 0, "markets": 0, "matched_events": 0, "rows": 0, "liquid_markets": 0}
    sess = _session()
    events, markets = list_markets(sess, horizon_hours)
    c["events"], c["markets"] = len(events), len(markets)
    af = _load_af_candidates(horizon_hours)
    ev_match: dict[str, tuple] = {}
    mapped = []
    for eid, ev in events.items():
        ht = split_event_name(ev.get("name"))
        if not ht:
            continue
        start = datetime.fromisoformat(ev["openDate"].replace("Z", "+00:00"))
        row, score, _ = match_event_to_af(ht[0], ht[1], ev.get("countryCode"), start, af)
        if row:
            ev_match[str(eid)] = (row["id"], ht, start)
            mapped.append((row["id"], str(eid), ev.get("openDate"), score))
    c["matched_events"] = len(ev_match)
    wanted = [mid for mid, m in markets.items() if str(m.get("eventId")) in ev_match]
    prices = fetch_prices(sess, wanted)
    now = datetime.now(timezone.utc)
    rows = []
    for mid in wanted:
        meta, node = markets[mid], prices.get(mid)
        if not node or (node.get("state") or {}).get("status") != "OPEN" or (node.get("state") or {}).get("inplay"):
            continue
        match_id, (home, away), start = ev_match[str(meta["eventId"])]
        names = {r["selectionId"]: r.get("runnerName") for r in meta.get("runners") or []}
        matched = (node.get("state") or {}).get("totalMatched")
        liquid_any = False
        for rn in node.get("runners") or []:
            sel = runner_selection(meta["marketType"], names.get(rn["selectionId"]), home, away)
            if not sel:
                continue
            ex = rn.get("exchange") or {}
            back, bsz = _best(ex.get("availableToBack"))
            lay, lsz = _best(ex.get("availableToLay"))
            liquid_any |= is_liquid(back, lay, matched)
            rows.append((match_id, EXCHANGE, _MARKETS[meta["marketType"]], sel, back, bsz, lay, lsz,
                         (rn.get("state") or {}).get("lastPriceTraded"), matched, mid, str(meta["eventId"]),
                         int((start - now).total_seconds() // 60)))
        c["liquid_markets"] += liquid_any
    c["rows"] = len(rows)
    if rows and not dry_run:
        from psycopg2.extras import execute_values
        from workers.api_clients.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """INSERT INTO exchange_quotes (match_id, exchange, market, selection,
                    back, back_size, lay, lay_size, last_traded, market_matched, market_id, event_id,
                    minutes_to_kickoff) VALUES %s""", rows, page_size=1000)
            conn.commit()
        from workers.api_clients.supabase_client import record_book_events
        record_book_events(EXCHANGE, mapped)
    log.info("betfair-exchange: %s", c)
    return c


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--horizon-hours", type=float, default=48)
    a = ap.parse_args()
    print(run_bulk(horizon_hours=a.horizon_hours, dry_run=a.dry_run))
