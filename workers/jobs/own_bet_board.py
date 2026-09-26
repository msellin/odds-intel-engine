"""[[#182]] OWN BOARD — "where do I put my real money right now?" (2026-09-26).

WHAT: every pending pre-match pick of a live bot, grouped per (match, market, selection), priced at
every book we can bet from Estonia (ACCESSIBLE_BOOKMAKERS) and judged against Pinnacle's de-vigged
fair price. Written to `own_bet_board` (migration 470), read by the admin OWN board.

WHY THIS SHAPE (owner, 2026-09-26): the ranking is "sharp price + a bot agrees" — the bots are the
shortlist, the price vs Pinnacle's fair line is the judge. /picks is for readers and prices at every
book; this board is for the operator's own money, so it only prices books we can actually bet.
`take_at` is the lowest price still worth taking, so a pick whose best price is at Epicbet can still
be matched at Coolbet if Coolbet reaches that number.

NO SECOND FAIR-PRICE COMPUTATION: fair value, freshness, the ceiling, the outlier cap and the
wrong-fixture guard all come from workers/automation/sharp_engine.py (the one sharp engine), with
BOARD_RULE as its config. The OWN bots (#182 part 2) will be further SharpRules on the same engine.

⚠️ Honest status: no bot has yet shown an edge that survives at Estonian books against an
independent close (#150, #172, #121). The board ranks current prices; it does not prove them.
"""
from __future__ import annotations

import json
import logging

from workers.automation.sharp_engine import (
    SharpRule, anchor_fair, edge_of, load_lines, price_refusal, quote_fresh,
)
from workers.utils.anchor import market_sides

log = logging.getLogger(__name__)

EDGE_FLOOR = 0.03        # EV vs Pinnacle fair — the per-book sharp triggers' floor
KO_BLOCK_MIN = 3         # the placer refuses inside this; so does the board
BOARD_RULE = SharpRule(bot_name="own_board", books=None, edge_unit="ev", edge_floor=EDGE_FLOOR)


def accessible_books() -> tuple[str, ...]:
    from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS
    return tuple(sorted(ACCESSIBLE_BOOKMAKERS))


def load_picks() -> list[dict]:
    from workers.api_clients.db import execute_query
    return execute_query(
        """
        SELECT l.source, l.pick_id::text AS pick_id, l.bot_name, l.match_id::text AS match_id,
               l.market, lower(l.selection) AS selection, l.pick_time,
               d.display_name, d.status, coalesce(d.vip, false) AS vip,
               m.date AS kickoff, th.name AS home, ta.name AS away, lg.name AS league
          FROM bot_ledger l
          JOIN matches m ON m.id = l.match_id
          LEFT JOIN teams th ON th.id = m.home_team_id
          LEFT JOIN teams ta ON ta.id = m.away_team_id
          LEFT JOIN leagues lg ON lg.id = m.league_id
          LEFT JOIN bot_distribution d ON d.bot_name = l.bot_name
         WHERE l.result = 'pending' AND NOT coalesce(l.is_inplay, false)
           AND m.status = 'scheduled' AND m.date > now() + make_interval(mins => %s)
           AND coalesce(d.status, '') <> 'retired'
        """, (KO_BLOCK_MIN,)) or []


def build(picks: list[dict], lines: dict, now_ts: float, books: tuple[str, ...]) -> list[dict]:
    """Pure: group picks per selection and price each at every accessible book."""
    groups: dict = {}
    for p in picks:
        k = (p["match_id"], p["market"], p["selection"])
        g = groups.setdefault(k, {"pick": p, "bots": {}})
        b = g["bots"].setdefault(p["bot_name"], {
            "bot": p["bot_name"], "display": p.get("display_name") or p["bot_name"],
            "status": p.get("status"), "vip": bool(p.get("vip")), "source": p["source"],
            "pick_id": p["pick_id"], "pick_time": str(p["pick_time"])})
        if str(p["pick_time"]) < b["pick_time"]:
            b.update(source=p["source"], pick_id=p["pick_id"], pick_time=str(p["pick_time"]))
    out = []
    for (mid, market, sel), g in groups.items():
        p0 = g["pick"]
        line = lines.get((mid, market)) or {}
        quotes = line.get("quotes") or {}
        ko = line.get("ko") or p0["kickoff"].timestamp()
        sides = market_sides(market)
        p_fair = pin_age = None
        anchor = quotes.get(BOARD_RULE.anchor_book) or {}
        if sides and sel in sides:
            probs = anchor_fair(BOARD_RULE, anchor, sides, now_ts, ko)
            if probs:
                p_fair = probs[sides.index(sel)]
                pin_age = round(max((now_ts - anchor[s][1]) / 60.0 for s in sides), 1)
        prices = {}
        best = None
        for bk in books:
            q = (quotes.get(bk) or {}).get(sel)
            if not q:
                continue
            odds, ts = q
            age = round((now_ts - ts) / 60.0, 1)
            edge = edge_of("ev", p_fair, odds) if p_fair else None
            if p_fair is None:
                why = "no_fair_price"
            elif not quote_fresh(age, BOARD_RULE.book_max_age_min):
                why = "stale_quote"
            else:
                a = anchor.get(sel)
                why = price_refusal(BOARD_RULE, p_fair, odds, a[0] if a else None, (ko - now_ts) / 3600.0, None)
            prices[bk] = {"odds": odds, "age_min": age,
                          "edge": round(edge, 4) if edge is not None else None, "refusal": why}
            if why is None and (best is None or odds > best[1]):
                best = (bk, odds, edge)
        out.append({
            "match_id": mid, "market": market, "selection": sel, "kickoff": p0["kickoff"],
            "home": p0.get("home"), "away": p0.get("away"), "league": p0.get("league"),
            "bots": sorted(g["bots"].values(), key=lambda b: (not b["vip"], b["bot"])),
            "n_bots": len(g["bots"]),
            "p_fair": p_fair, "fair_odds": (1 / p_fair) if p_fair else None,
            "take_at": ((1 + EDGE_FLOOR) / p_fair) if p_fair else None, "pin_age_min": pin_age,
            "prices": prices,
            "best_book": best[0] if best else None, "best_odds": best[1] if best else None,
            "best_edge": best[2] if best else None, "clears": best is not None,
        })
    return out


COLS = ["match_id", "market", "selection", "kickoff", "home", "away", "league", "bots", "n_bots",
        "p_fair", "fair_odds", "take_at", "pin_age_min", "prices", "best_book", "best_odds",
        "best_edge", "clears"]


def run() -> dict:
    from psycopg2.extras import execute_values
    from workers.api_clients.db import get_conn
    books = accessible_books()
    picks = load_picks()
    markets = tuple(sorted({p["market"] for p in picks}))
    lines, now_ts = load_lines(markets, books) if markets else ({}, 0.0)
    rows = build(picks, lines, now_ts, books)
    vals = [tuple(json.dumps(r[c]) if c in ("bots", "prices") else r[c] for c in COLS) for r in rows]
    with get_conn() as conn:                    # replace the board atomically
        with conn.cursor() as cur:
            cur.execute("DELETE FROM own_bet_board")
            if vals:
                execute_values(cur, f"INSERT INTO own_bet_board ({', '.join(COLS)}) VALUES %s", vals)
        conn.commit()
    n_clear = sum(r["clears"] for r in rows)
    log.info("OWN-BOARD: %d selections, %d clear at an Estonian book", len(rows), n_clear)
    return {"written": len(rows), "clears": n_clear}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
