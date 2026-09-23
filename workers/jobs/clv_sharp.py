"""CLV-SHARP ([[#024]], migration 386) — every settled leg judged against the
de-vigged Pinnacle close, in ONE definition.

    clv_sharp = odds × Shin(Pinnacle close) − 1

THE CLOSE. For the leg's market, the latest COMPLETE Pinnacle set (every side of
the market), each side within ±ASSEMBLE_MIN of the first side's quote (so a set
spans at most 2×ASSEMBLE_MIN), taken no
earlier than FRESH_MIN before kickoff. The existing
`settlement.get_devigged_pinnacle_close_prob` is deliberately NOT reused: its
close has no age bound (a 6-hour-old quote counts as "closing") and it fetches
each side separately, so one close can mix moments. Its age is stored
(`close_age_min`) so a reader can always tell how fresh the close was.

THE PRICE a leg is judged at:
  * picks_forward_test — the published odds ('published')
  * simulated_bets / shadow_bets — `odds_at_pick_live` when set ('executable'),
    else `odds_at_pick`, which is a MAX() high-water mark ('high_water') —
    ANALYSIS_GOTCHAS §30. The basis is stored so the two are never pooled blind.

MARKETS. 1x2, 1x2_1h, over_under_*, team_total_*, corners_* directly;
double_chance and draw_no_bet DERIVED exactly from the 1x2 close (DC = union of
1x2 outcomes; DNB = P(side | not draw)). Asian handicap and BTTS are recorded as
'unsupported_market' (AH needs the line threaded through; Pinnacle quotes no
BTTS through our feed).

CONSENSUS CLOSE ([[#113]], migration 393). Beside the Pinnacle close, every leg is
also judged against a >=5-book consensus close (workers/utils/anchor.py; Pinnacle
and the leg's own book excluded) → p_close_cons / clv_cons / cons_status. It fills
the 36% of legs with no fresh Pinnacle close and BTTS, and it is computed for legs
WITH a Pinnacle close too so the two are always comparable. clv_sharp's own
definition is untouched; readers that fall back must carry the source.

    python3 -m workers.jobs.clv_sharp            # all settled legs not yet scored
    python3 -m workers.jobs.clv_sharp --limit 5000
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import timedelta

log = logging.getLogger(__name__)

FRESH_MIN = 60          # close no older than this at kickoff
ASSEMBLE_MIN = 2        # every side within ±2 min of the anchor side (set spans <=4 min)
# A leg with no fresh close is retried for 3 days after kickoff (a late closing
# snap can still land); after that its 'no_fresh_close' is final.
BATCH_MATCHES = 400
_DC = {"1x": (0, 1), "12": (0, 2), "x2": (1, 2)}


def sides_for(market: str) -> tuple[str, ...] | None:
    m = (market or "").lower()
    if m in ("1x2", "1x2_1h", "double_chance", "draw_no_bet"):
        return ("home", "draw", "away")
    if m.startswith(("over_under", "team_total", "corners_")):
        return ("over", "under")
    return None


def base_market(market: str) -> str:
    return "1x2" if market in ("double_chance", "draw_no_bet") else market


def assemble_close(quotes: dict[str, list], sides: tuple[str, ...], kickoff):
    """quotes: side -> [(ts, odds)] ascending. Latest set where every side has a
    quote within ±ASSEMBLE_MIN of an anchor, all within FRESH_MIN of kickoff.
    Returns (odds list in `sides` order, close_ts) or None."""
    lo = kickoff - timedelta(minutes=FRESH_MIN)
    win = {s: [(t, o) for t, o in quotes.get(s, []) if lo <= t <= kickoff and o > 1.0]
           for s in sides}
    if any(not v for v in win.values()):
        return None
    tol = timedelta(minutes=ASSEMBLE_MIN)
    for t_anchor, _ in sorted(win[sides[0]], reverse=True):
        picked = []
        for s in sides:
            near = [(t, o) for t, o in win[s] if abs((t - t_anchor).total_seconds()) <= tol.total_seconds()]
            if not near:
                break
            picked.append(max(near))        # latest within the tolerance
        else:
            return [o for _, o in picked], max(t for t, _ in picked)
    return None


def leg_prob(market: str, selection: str, probs: list[float], sides: tuple[str, ...]):
    sel = (selection or "").lower()
    if market == "double_chance":
        ij = _DC.get(sel.replace(" ", ""))
        return (probs[ij[0]] + probs[ij[1]]) if ij else None
    if market == "draw_no_bet":
        if sel not in ("home", "away"):
            return None
        i = 0 if sel == "home" else 2
        d = probs[0] + probs[2]
        return probs[i] / d if d > 0 else None
    return probs[sides.index(sel)] if sel in sides else None


_LEGS_SQL = {
    "picks_forward_test": """
        SELECT p.id::text leg_id, p.match_id::text match_id, p.market, p.selection,
               p.odds::float odds, 'published' basis, m.date kickoff, p.bookmaker bk
          FROM picks_forward_test p JOIN matches m ON m.id = p.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = p.id
         WHERE p.outcome IN ('won','lost')
           AND (c.leg_id IS NULL
                OR (c.status = 'no_fresh_close' AND m.date > now() - interval '3 days')
                OR (c.cons_status IS NULL AND m.date > now() - make_interval(days => %(cons_days)s)))""",
    "simulated_bets": """
        SELECT s.id::text leg_id, s.match_id::text match_id, s.market, s.selection,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float odds,
               CASE WHEN s.odds_at_pick_live IS NOT NULL THEN 'executable' ELSE 'high_water' END basis,
               m.date kickoff, s.recommended_bookmaker bk
          FROM simulated_bets s JOIN matches m ON m.id = s.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets' AND c.leg_id = s.id
         WHERE s.result IN ('won','lost')
           AND (c.leg_id IS NULL
                OR (c.status = 'no_fresh_close' AND m.date > now() - interval '3 days')
                OR (c.cons_status IS NULL AND m.date > now() - make_interval(days => %(cons_days)s)))""",
    "shadow_bets": """
        SELECT s.id::text leg_id, s.match_id::text match_id, s.market, s.selection,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float odds,
               CASE WHEN s.odds_at_pick_live IS NOT NULL THEN 'executable' ELSE 'high_water' END basis,
               m.date kickoff, s.recommended_bookmaker bk
          FROM shadow_bets s JOIN matches m ON m.id = s.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets' AND c.leg_id = s.id
         WHERE s.result IN ('won','lost')
           AND (c.leg_id IS NULL
                OR (c.status = 'no_fresh_close' AND m.date > now() - interval '3 days')
                OR (c.cons_status IS NULL AND m.date > now() - make_interval(days => %(cons_days)s)))""",
}


def run(limit: int | None = None, cons_days: int = 7) -> dict:
    """`cons_days`: how far back legs WITHOUT a consensus close are (re)computed.
    Retention keeps only the latest pre-kickoff row per series after ~7 days
    (ANALYSIS_GOTCHAS §59), so older consensus closes rest on thinner data."""
    from psycopg2.extras import execute_values
    from workers.api_clients.db import execute_query, get_conn
    from workers.model.devig import devig
    from workers.utils.anchor import compute_anchor, sets_from_rows
    from workers.utils.anchor import market_sides as anchor_sides

    counts = defaultdict(int)
    legs = []
    for ledger, sql in _LEGS_SQL.items():
        rows = execute_query(sql + (f" LIMIT {int(limit)}" if limit else ""),
                             {"cons_days": int(cons_days)})
        legs += [dict(r, ledger=ledger) for r in rows]
    by_match = defaultdict(list)
    for l in legs:
        by_match[l["match_id"]].append(l)
    mids = list(by_match)
    for i in range(0, len(mids), BATCH_MATCHES):
        chunk = mids[i:i + BATCH_MATCHES]
        markets = sorted({base_market(l["market"]) for m in chunk for l in by_match[m]})
        snaps = execute_query("""
            SELECT o.match_id::text mid, o.market, lower(o.selection) selection, o.bookmaker bk,
                   o.timestamp ts, o.odds::float od
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
               AND o.is_live IS NOT TRUE AND o.odds > 1.01
               AND o.timestamp <= m.date AND o.timestamp >= m.date - make_interval(mins => %s)
             ORDER BY o.timestamp""", (chunk, markets, FRESH_MIN))
        q = defaultdict(lambda: defaultdict(list))
        allbooks = defaultdict(list)          # (mid, market) -> rows of every book, for the consensus
        for s in snaps:
            if s["bk"] == "Pinnacle":
                q[(s["mid"], s["market"])][s["selection"]].append((s["ts"], s["od"]))
            else:
                allbooks[(s["mid"], s["market"])].append(
                    {"bookmaker": s["bk"], "sel": s["selection"], "odds": s["od"], "timestamp": s["ts"]})
        cons_cache: dict = {}
        out, closes = [], {}
        for mid in chunk:
            for l in by_match[mid]:
                sides = sides_for(l["market"])
                row = [l["ledger"], l["leg_id"], mid, l["market"], l["selection"], l["odds"],
                       l["basis"], None, None, None, None, None, None, None, None, None]
                # #113 — the SAME measure against a >=5-book consensus close (Pinnacle and
                # the leg's own book excluded), for every leg incl. BTTS.
                csides = sides_for(l["market"]) or anchor_sides(base_market(l["market"]))
                if csides is None or l["odds"] is None:
                    row[15] = "unsupported_market"
                else:
                    ck = (mid, base_market(l["market"]), l.get("bk"))
                    if ck not in cons_cache:
                        sets = sets_from_rows(allbooks.get(ck[:2], []), csides)
                        cons_cache[ck] = compute_anchor(sets, csides, at=l["kickoff"],
                                                        exclude_book=l.get("bk"), max_age_min=FRESH_MIN)
                    a = cons_cache[ck]
                    pc = (leg_prob(l["market"], l["selection"], [a.probs[x] for x in csides], csides)
                          if a.source == "consensus" else None)
                    if pc is not None and 0 < pc < 1:
                        row[12], row[13], row[14], row[15] = pc, l["odds"] * pc - 1, a.n_books, "ok"
                    else:
                        row[15] = "no_consensus"
                        row[14] = a.n_books or None
                if sides is None or l["odds"] is None or l["market"] == "btts":
                    row[11] = "unsupported_market"
                else:
                    key = (mid, base_market(l["market"]))
                    if key not in closes:
                        closes[key] = assemble_close(q.get(key, {}), sides, l["kickoff"])
                    c = closes[key]
                    probs = devig(c[0]) if c else None
                    p = leg_prob(l["market"], l["selection"], probs, sides) if probs else None
                    if p is None or not 0 < p < 1:
                        row[11] = "no_fresh_close" if not c else "unsupported_market"
                    else:
                        row[7], row[8] = p, c[1]
                        row[9] = round((l["kickoff"] - c[1]).total_seconds() / 60, 1)
                        row[10] = l["odds"] * p - 1
                        row[11] = "ok"
                counts[f"{l['ledger']}:{row[11]}"] += 1
                counts[f"{l['ledger']}:cons_{row[15]}"] += 1
                out.append(tuple(row))
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO leg_clv_sharp (ledger, leg_id, match_id, market, selection, odds,
                        odds_basis, p_close, close_ts, close_age_min, clv_sharp, status,
                        p_close_cons, clv_cons, cons_n_books, cons_status)
                    VALUES %s ON CONFLICT (ledger, leg_id) DO UPDATE SET
                        -- the Pinnacle half keeps its original rule: only a
                        -- no_fresh_close row may be overwritten
                        p_close = CASE WHEN leg_clv_sharp.status = 'no_fresh_close' THEN EXCLUDED.p_close ELSE leg_clv_sharp.p_close END,
                        close_ts = CASE WHEN leg_clv_sharp.status = 'no_fresh_close' THEN EXCLUDED.close_ts ELSE leg_clv_sharp.close_ts END,
                        close_age_min = CASE WHEN leg_clv_sharp.status = 'no_fresh_close' THEN EXCLUDED.close_age_min ELSE leg_clv_sharp.close_age_min END,
                        clv_sharp = CASE WHEN leg_clv_sharp.status = 'no_fresh_close' THEN EXCLUDED.clv_sharp ELSE leg_clv_sharp.clv_sharp END,
                        status = CASE WHEN leg_clv_sharp.status = 'no_fresh_close' THEN EXCLUDED.status ELSE leg_clv_sharp.status END,
                        -- the consensus half fills once and is then final
                        p_close_cons = COALESCE(leg_clv_sharp.p_close_cons, EXCLUDED.p_close_cons),
                        clv_cons = COALESCE(leg_clv_sharp.clv_cons, EXCLUDED.clv_cons),
                        cons_n_books = COALESCE(leg_clv_sharp.cons_n_books, EXCLUDED.cons_n_books),
                        cons_status = CASE WHEN leg_clv_sharp.cons_status = 'ok' THEN 'ok' ELSE EXCLUDED.cons_status END,
                        computed_at = now()
                    WHERE leg_clv_sharp.status = 'no_fresh_close' OR leg_clv_sharp.cons_status IS DISTINCT FROM 'ok'""",
                    out, page_size=2000)
            conn.commit()
        log.info("clv_sharp: %d/%d matches", min(i + BATCH_MATCHES, len(mids)), len(mids))
    return dict(counts)


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cons-days", type=int, default=7,
                    help="recompute the consensus close for legs up to this many days back")
    a = ap.parse_args()
    for k, v in sorted(run(a.limit, cons_days=a.cons_days).items()):
        print(f"  {k:40s} {v:7d}")


if __name__ == "__main__":
    main()
