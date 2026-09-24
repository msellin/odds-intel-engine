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
# #119 step D — second pass, ONLY on events whose match odds are liquid (bounds requests):
# sharp prices for markets readers can bet even where we cannot (quarter-line AH).
_EXTRA = {"BOTH_TEAMS_TO_SCORE": "btts", "OVER_UNDER_15": "over_under_15",
          "OVER_UNDER_35": "over_under_35", "ASIAN_HANDICAP": "asian_handicap",
          "ALT_TOTAL_GOALS": "goal_line"}
EXTRA_MIN_MATCHED = 1000.0    # EUR on the event's MATCH_ODDS before its extra markets are read
_LINE_MAX_SPREAD = 0.20       # store an AH/goal line only when both sides are two-sided within this

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


def list_event_markets(sess, event_ids: list[str], market_types: list[str]) -> dict:
    """Markets of the given types for specific events → {marketId: meta}."""
    out = {}
    for i in range(0, len(event_ids), 50):
        body = {"filter": {"productTypes": ["EXCHANGE"], "contentGroup": {"language": "en"},
                           "maxResults": 0, "eventIds": [int(e) for e in event_ids[i:i + 50]],
                           "marketTypeCodes": market_types},
                "facets": [{"type": "MARKET", "maxValues": 1000, "skipValues": 0, "applyNextTo": 0}],
                "currencyCode": "EUR", "locale": "en"}
        r = sess.post(_NAV, params={"_ak": _AK, "alt": "json"}, json=body, timeout=40)
        r.raise_for_status()
        out.update((r.json().get("attachments") or {}).get("markets") or {})
        time.sleep(_SLEEP_S)
    return out


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
    if market_type == "BOTH_TEAMS_TO_SCORE":
        return {"Yes": "yes", "No": "no"}.get(n)
    if market_type in ("OVER_UNDER_15", "OVER_UNDER_35"):
        return "over" if n.startswith("Over") else "under" if n.startswith("Under") else None
    if market_type == "ASIAN_HANDICAP":
        return "home" if n == home else "away" if n == away else None
    if market_type == "ALT_TOTAL_GOALS":
        return {"Over": "over", "Under": "under"}.get(n)
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


def line_of(market_type: str, selection: str, handicap: float | None) -> float | None:
    """Our convention: AH rows carry the HOME line on both sides; goal lines the total."""
    if handicap is None:
        return None
    if market_type == "ASIAN_HANDICAP":
        return float(handicap) if selection == "home" else -float(handicap)
    if market_type == "ALT_TOTAL_GOALS":
        return float(handicap)
    # fixed-line O/U markets carry their line too — odds_snapshots stores 2.5 on
    # over_under_25, and a NULL here made every exchange-vs-Pinnacle O/U join empty
    return {"OVER_UNDER_15": 1.5, "OVER_UNDER_25": 2.5, "OVER_UNDER_35": 3.5}.get(market_type)


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
    liquid_events: set[str] = set()
    names_all = {**_MARKETS, **_EXTRA}

    def build(market_ids, meta_by_id, price_by_id):
        for mid in market_ids:
            meta, node = meta_by_id[mid], price_by_id.get(mid)
            st = (node or {}).get("state") or {}
            if not node or st.get("status") != "OPEN" or st.get("inplay"):
                continue
            match_id, (home, away), start = ev_match[str(meta["eventId"])]
            mtype = meta["marketType"]
            names = {(r["selectionId"], float(r.get("handicap") or 0)): r.get("runnerName")
                     for r in meta.get("runners") or []}
            matched = st.get("totalMatched")
            if mtype == "MATCH_ODDS" and (matched or 0) >= EXTRA_MIN_MATCHED:
                liquid_events.add(str(meta["eventId"]))
            lined = mtype in ("ASIAN_HANDICAP", "ALT_TOTAL_GOALS")
            by_line: dict = {}
            liquid_any = False
            for rn in node.get("runners") or []:
                hc = float(rn.get("handicap") or 0)
                sel = runner_selection(mtype, names.get((rn["selectionId"], hc)), home, away)
                if not sel:
                    continue
                ex = rn.get("exchange") or {}
                back, bsz = _best(ex.get("availableToBack"))
                lay, lsz = _best(ex.get("availableToLay"))
                liquid_any |= is_liquid(back, lay, matched)
                row = (match_id, EXCHANGE, names_all[mtype], sel, back, bsz, lay, lsz,
                       (rn.get("state") or {}).get("lastPriceTraded"), matched, mid, str(meta["eventId"]),
                       int((start - now).total_seconds() // 60), line_of(mtype, sel, hc))
                if lined:
                    by_line.setdefault(row[-1], []).append(row)
                else:
                    rows.append(row)
            # a line is kept only when BOTH sides carry a two-sided price within _LINE_MAX_SPREAD —
            # far lines are one-sided junk (e.g. 13.5 back / no lay)
            for line_rows in by_line.values():
                if len(line_rows) == 2 and all(r[4] and r[6] and r[6] / r[4] - 1 <= _LINE_MAX_SPREAD
                                               for r in line_rows):
                    rows.extend(line_rows)
            c["liquid_markets"] += liquid_any

    build(wanted, markets, prices)
    # second pass (#119 D): extra markets only for events whose match odds are liquid
    c["extra_events"] = len(liquid_events)
    if liquid_events:
        extra_meta = list_event_markets(sess, sorted(liquid_events), list(_EXTRA))
        extra_ids = [m for m, v in extra_meta.items() if str(v.get("eventId")) in ev_match]
        build(extra_ids, extra_meta, fetch_prices(sess, extra_ids))
        c["extra_markets"] = len(extra_ids)
    c["rows"] = len(rows)
    if rows and not dry_run:
        from psycopg2.extras import execute_values
        from workers.api_clients.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """INSERT INTO exchange_quotes (match_id, exchange, market, selection,
                    back, back_size, lay, lay_size, last_traded, market_matched, market_id, event_id,
                    minutes_to_kickoff, handicap_line) VALUES %s""", rows, page_size=1000)
            conn.commit()
        from workers.api_clients.supabase_client import record_book_events
        record_book_events(EXCHANGE, mapped)
    log.info("betfair-exchange: %s", c)
    return c


RETAIN_FULL_WITHIN_MIN = 360   # keep every capture within 6 h of kickoff (the close studies need it)
THIN_AFTER_DAYS = 2


def prune(*, dry_run: bool = False) -> int:
    """RETENTION (#119, 2026-09-24): ~230k rows/day since the step-D widening. After
    THIN_AFTER_DAYS, captures more than RETAIN_FULL_WITHIN_MIN before kickoff are thinned
    to the FIRST per hour per (match, market, selection, line); captures near kickoff are
    kept in full. Returns rows deleted (or that would be)."""
    from workers.api_clients.db import execute_query, execute_write
    sql_sel = """SELECT id FROM (
                   SELECT id, row_number() OVER (
                            PARTITION BY match_id, market, selection, COALESCE(handicap_line, -999),
                                         date_trunc('hour', captured_at)
                            ORDER BY captured_at) AS rn
                     FROM exchange_quotes
                    WHERE captured_at < now() - make_interval(days => %s)
                      AND minutes_to_kickoff > %s) d
                  WHERE d.rn > 1"""
    params = (THIN_AFTER_DAYS, RETAIN_FULL_WITHIN_MIN)
    if dry_run:
        return len(execute_query(sql_sel, params) or [])
    return execute_write(f"DELETE FROM exchange_quotes WHERE id IN ({sql_sel})", params) or 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--horizon-hours", type=float, default=48)
    a = ap.parse_args()
    print(run_bulk(horizon_hours=a.horizon_hours, dry_run=a.dry_run))
