"""[[#182]] OWN BOARD — "where do I put my real money right now?" (2026-09-26).

WHAT: every pending pre-match pick of a live bot, grouped per (match, market, selection), priced at
every book we can bet from Estonia (ACCESSIBLE_BOOKMAKERS) and judged against Pinnacle's de-vigged
fair price. Written to `own_bet_board` (migration 470), read by the admin OWN board.

WHY THIS SHAPE (owner, 2026-09-26): the ranking is "sharp price + a bot agrees" — the bots are the
shortlist, the price vs Pinnacle's fair line is the judge. /picks is for readers and prices at every
book; this board is for the operator's own money, so it only prices books we can actually bet.
`take_at` is the lowest price still worth taking, so a pick whose best price is at Epicbet can still
be matched at Coolbet if Coolbet reaches that number.

NO SECOND FAIR-PRICE COMPUTATION. Fair value = the ANCHOR RESOLVER (workers/utils/anchor.py, #113 /
#119), owner 2026-09-26 ("does the sharp engine also use the Betfair exchange and a consensus of books
as fallback? we have improved the anchors"): the sharp tier first — Pinnacle + Betfair Exchange blended,
or either alone (fresh, tight / liquid); a Pinnacle-vs-exchange CONFLICT gives NO price (that is how a
phantom edge looks) — then a >= 5-book consensus that never contains the book being priced, then a wide
Pinnacle line. Every row carries its anchor source. The price gates (8% ceiling, outlier cap,
wrong-fixture guard, 60-min book freshness) are the sharp engine's (`price_refusal`), with BOARD_RULE as
its config. The existing sharp BOTS stay Pinnacle-only until #119 decides; the board and the OWN bots
use the v2 anchor from the start.

⚠️ Honest status: no bot has yet shown an edge that survives at Estonian books against an
independent close (#150, #172, #121). The board ranks current prices; it does not prove them.
"""
from __future__ import annotations

import json
import logging

from workers.automation.sharp_engine import (
    SharpRule, edge_of, load_lines, price_refusal, quote_fresh,
)
from workers.utils.anchor import (
    PIN, _EX_MARKETS, Anchor, compute_anchor, compute_sharp, load_exchange, load_sets, market_sides,
)

log = logging.getLogger(__name__)

EDGE_FLOOR = 0.03        # EV vs Pinnacle fair — the per-book sharp triggers' floor
KO_BLOCK_MIN = 3         # the placer refuses inside this; so does the board
# MARKET SPLIT (owner, 2026-09-26, EBK v GrIFK): Coolbet / Epicbet / Unibet had EBK at 1.33-1.61 while
# Pinnacle and eight global books had ~1.92 — the local books knew something the global feed did not yet,
# and the anchor made their DRAW look like +6..+24% value. When the median of OUR books' own de-vigged lines
# is this far from the anchor on ANY side (prob points), the fair price is not trusted: no price, no pick.
# Normal soft-book disagreement is a few points; the phantom was ~0.15.
SPLIT_MAX_GAP = 0.08
SPLIT_MIN_BOOKS = 2
# DE-VIG ROBUSTNESS (owner, 2026-09-26, San Marino v Finland draw at Tonybet 13.00 shown "+5.4%"): on a lopsided
# line the de-vig METHOD decides the answer — the anchor's Shin fair was 12.3, POWER on the same books 13-20,
# Tonybet's own Sportradar fair 17.4. A pick must clear the floor under BOTH methods (the anchor's own members
# re-de-vigged with power); near-even lines barely move, longshot artefacts drop out. Whether 1X2 should use
# power everywhere is #154 R2-A (pre-registered, ~10-14); this only stops the board trusting the fragile case.
BOARD_RULE = SharpRule(bot_name="own_board", books=None, edge_unit="ev", edge_floor=EDGE_FLOOR)


def accessible_books() -> tuple[str, ...]:
    from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS
    return tuple(sorted(ACCESSIBLE_BOOKMAKERS))


SHARP_SOURCES = ("sharp_blend", "pinnacle_tight", "exchange_liquid", "sharp_conflict")


def anchors_for_books(sets: dict, ex: dict | None, sides: tuple[str, ...], books: tuple[str, ...],
                      at) -> dict:
    """Pure: {book: Anchor}. The sharp tier (Pinnacle / exchange, or their conflict) is one anchor for
    every book; otherwise each book gets the resolver's anchor WITHOUT itself (exclude_book)."""
    sharp = compute_sharp(sets.get(PIN), ex, sides, at=at)
    out = ({b: sharp for b in books} if sharp.source in SHARP_SOURCES
           else {b: compute_anchor(sets, sides, at=at, exclude_book=b) for b in books})
    for a in {id(x): x for x in out.values()}.values():
        a.power_probs = power_fair(sets, sides, list(a.books))
    return out


def power_fair(sets: dict, sides: tuple[str, ...], members: list[str]) -> dict | None:
    """Pure: the anchor's own member books (those we hold a line for) re-de-vigged with POWER, averaged,
    renormalised — the second opinion the DE-VIG ROBUSTNESS rule needs. None when no member line is held."""
    from workers.model.devig import devig_by
    ps = [p for p in (devig_by("power", sets[m][0]) for m in members if m in sets) if p]
    if not ps:
        return None
    m = [sum(p[i] for p in ps) / len(ps) for i in range(len(sides))]
    s = sum(m)
    return {side: v / s for side, v in zip(sides, m)}


def line_anchors(mid: str, market: str, books: tuple[str, ...], at) -> dict:
    sides = market_sides(market)
    if not sides:
        return {b: Anchor("none") for b in books}
    sets = load_sets(mid, market, sides, at=at)
    ex = load_exchange(mid, market, sides, at=at) if market in _EX_MARKETS else None
    return anchors_for_books(sets, ex, sides, books, at)


def market_split(quotes: dict, books: tuple[str, ...], sides: tuple[str, ...] | None,
                 anchor_probs: dict | None) -> float | None:
    """Pure: the largest side gap between the median of OUR books' de-vigged complete lines and the anchor,
    or None when fewer than SPLIT_MIN_BOOKS of our books have a complete line (or there is no anchor)."""
    from statistics import median
    from workers.model.devig import fair_prob
    if not sides or not anchor_probs or any(s not in anchor_probs for s in sides):
        return None
    ps = []
    for bk in books:
        q = quotes.get(bk) or {}
        if all(s in q for s in sides):
            p = fair_prob([q[s][0] for s in sides])
            if p:
                ps.append(p)
    if len(ps) < SPLIT_MIN_BOOKS:
        return None
    return max(abs(median(p[i] for p in ps) - anchor_probs[s]) for i, s in enumerate(sides))


def _match_split(mid: str, lines: dict, anchors: dict, books: tuple[str, ...]) -> float | None:
    """The 1X2 market-split gap of a match, when its 1X2 line and anchor are loaded."""
    ln = lines.get((mid, "1x2")) or {}
    per = anchors.get((mid, "1x2")) or {}
    a = next((x for x in per.values() if x is not None and x.probs), None)
    return market_split(ln.get("quotes") or {}, books, market_sides("1x2"), a.probs if a else None)


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


def build(picks: list[dict], lines: dict, now_ts: float, books: tuple[str, ...], anchors: dict) -> list[dict]:
    """Pure: group picks per selection and price each at every accessible book.
    `anchors` = {(match_id, market): {book: Anchor}} (line_anchors)."""
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
        pin_q = (quotes.get(PIN) or {}).get(sel)
        per_book = anchors.get((mid, market)) or {}
        _a0 = next((x for x in per_book.values() if x is not None and x.probs), None)
        split_gap = market_split(quotes, books, market_sides(market), _a0.probs if _a0 else None)
        # the split is a property of the MATCH (EBK v GrIFK: the 1X2 split also made the local books' goals
        # line look like value) — the 1X2 line's gap counts for every market of that match
        _g1 = _match_split(mid, lines, anchors, books)
        if _g1 is not None and (split_gap is None or _g1 > split_gap):
            split_gap = _g1
        prices = {}
        best = None
        first_anchor = None
        take_ats = []
        for bk in books:
            a = per_book.get(bk)
            p = a.prob(sel) if a is not None else None
            if p and first_anchor is None:
                first_anchor = (a, p)
            if p and a.source != "sharp_conflict":
                # each book is judged against an anchor WITHOUT itself, so its bar differs slightly
                take_ats.append((1 + EDGE_FLOOR) / p)
            q = (quotes.get(bk) or {}).get(sel)
            if not q:
                continue
            odds, ts = q
            age = round((now_ts - ts) / 60.0, 1)
            edge = edge_of("ev", p, odds) if p else None
            if split_gap is not None and split_gap > SPLIT_MAX_GAP:
                why = "market_split"
            elif a is not None and a.source == "sharp_conflict":
                why = "sharp_conflict"
            elif not p:
                why = "no_fair_price"
            elif (getattr(a, "power_probs", None) or {}).get(sel) and odds * a.power_probs[sel] - 1 < EDGE_FLOOR \
                    and edge is not None and edge >= EDGE_FLOOR:
                why = "devig_sensitive"
            elif not quote_fresh(age, BOARD_RULE.book_max_age_min):
                why = "stale_quote"
            else:
                why = price_refusal(BOARD_RULE, p, odds, pin_q[0] if pin_q else None, (ko - now_ts) / 3600.0, None)
            prices[bk] = {"odds": odds, "age_min": age, "edge": round(edge, 4) if edge is not None else None,
                          "refusal": why, "anchor": a.source if a is not None else "none",
                          "p_fair": round(p, 5) if p else None,
                          "take_at": round((1 + EDGE_FLOOR) / p, 3) if p else None}
            if why is None and (best is None or odds > best[1]):
                best = (bk, odds, edge, a, p)
        a_row, p_row = (best[3], best[4]) if best else (first_anchor or (None, None))
        src = a_row.source if a_row is not None else next(
            (x.source for x in per_book.values() if x is not None and x.source == "sharp_conflict"), "none")
        out.append({
            "match_id": mid, "market": market, "selection": sel, "kickoff": p0["kickoff"],
            "home": p0.get("home"), "away": p0.get("away"), "league": p0.get("league"),
            "bots": sorted(g["bots"].values(), key=lambda b: (not b["vip"], b["bot"])),
            "n_bots": len(g["bots"]),
            "p_fair": p_row, "fair_odds": (1 / p_row) if p_row else None,
            # the STRICTEST book's bar: a price at or above it clears the edge floor at every book
            "take_at": max(take_ats) if take_ats else None,
            "pin_age_min": a_row.max_age_min if a_row is not None else None,
            "anchor_source": src, "anchor_books": a_row.n_books if a_row is not None else 0,
            "split_gap": round(split_gap, 4) if split_gap is not None else None,
            "prices": prices,
            "best_book": best[0] if best else None, "best_odds": best[1] if best else None,
            "best_edge": best[2] if best else None, "clears": best is not None,
        })
    return out


COLS = ["match_id", "market", "selection", "kickoff", "home", "away", "league", "bots", "n_bots",
        "p_fair", "fair_odds", "take_at", "pin_age_min", "anchor_source", "anchor_books", "prices",
        "best_book", "best_odds", "best_edge", "clears"]


def run() -> dict:
    from psycopg2.extras import execute_values
    from workers.api_clients.db import get_conn
    books = accessible_books()
    picks = load_picks()
    markets = tuple(sorted({p["market"] for p in picks} | {"1x2"}))   # 1X2 always: the match-level split
    lines, now_ts = load_lines(markets, books) if markets else ({}, 0.0)
    from datetime import datetime, timezone
    at = datetime.fromtimestamp(now_ts, tz=timezone.utc) if now_ts else datetime.now(timezone.utc)
    keys = {(p["match_id"], p["market"]) for p in picks} | {(p["match_id"], "1x2") for p in picks}
    anchors = {k: line_anchors(k[0], k[1], books, at) for k in keys}
    rows = build(picks, lines, now_ts, books, anchors)
    vals = [tuple(json.dumps(r[c]) if c in ("bots", "prices") else r[c] for c in COLS) for r in rows]
    with get_conn() as conn:                    # replace the board atomically
        with conn.cursor() as cur:
            cur.execute("DELETE FROM own_bet_board")
            if vals:
                execute_values(cur, f"INSERT INTO own_bet_board ({', '.join(COLS)}) VALUES %s", vals)
        conn.commit()
    n_clear = sum(r["clears"] for r in rows)
    from collections import Counter
    log.info("OWN-BOARD: %d selections, %d clear at an Estonian book; anchors %s", len(rows), n_clear,
             dict(Counter(r["anchor_source"] for r in rows)))
    return {"written": len(rows), "clears": n_clear}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
