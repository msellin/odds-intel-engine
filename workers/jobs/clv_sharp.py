"""CLV-SHARP ([[#024]], migration 386) — every settled leg judged against the
de-vigged Pinnacle close, in ONE definition.

    clv_sharp = odds × Shin(Pinnacle close) − 1

THE CLOSE. For the leg's market, the latest COMPLETE Pinnacle set (every side of
the market) whose quotes sit within ±ASSEMBLE_MIN of each other, taken no
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
ASSEMBLE_MIN = 2        # every side of the close within ±2 min of each other
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
               p.odds::float odds, 'published' basis, m.date kickoff
          FROM picks_forward_test p JOIN matches m ON m.id = p.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = p.id
         WHERE p.outcome IN ('won','lost') AND c.leg_id IS NULL""",
    "simulated_bets": """
        SELECT s.id::text leg_id, s.match_id::text match_id, s.market, s.selection,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float odds,
               CASE WHEN s.odds_at_pick_live IS NOT NULL THEN 'executable' ELSE 'high_water' END basis,
               m.date kickoff
          FROM simulated_bets s JOIN matches m ON m.id = s.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets' AND c.leg_id = s.id
         WHERE s.result IN ('won','lost') AND c.leg_id IS NULL""",
    "shadow_bets": """
        SELECT s.id::text leg_id, s.match_id::text match_id, s.market, s.selection,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float odds,
               CASE WHEN s.odds_at_pick_live IS NOT NULL THEN 'executable' ELSE 'high_water' END basis,
               m.date kickoff
          FROM shadow_bets s JOIN matches m ON m.id = s.match_id
          LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets' AND c.leg_id = s.id
         WHERE s.result IN ('won','lost') AND c.leg_id IS NULL""",
}


def run(limit: int | None = None) -> dict:
    from psycopg2.extras import execute_values
    from workers.api_clients.db import execute_query, get_conn
    from workers.model.devig import devig

    counts = defaultdict(int)
    legs = []
    for ledger, sql in _LEGS_SQL.items():
        rows = execute_query(sql + (f" LIMIT {int(limit)}" if limit else ""))
        legs += [dict(r, ledger=ledger) for r in rows]
    by_match = defaultdict(list)
    for l in legs:
        by_match[l["match_id"]].append(l)
    mids = list(by_match)
    for i in range(0, len(mids), BATCH_MATCHES):
        chunk = mids[i:i + BATCH_MATCHES]
        markets = sorted({base_market(l["market"]) for m in chunk for l in by_match[m]})
        snaps = execute_query("""
            SELECT o.match_id::text mid, o.market, o.selection, o.timestamp ts, o.odds::float od
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
               AND o.bookmaker = 'Pinnacle' AND o.is_live IS NOT TRUE
               AND o.timestamp <= m.date AND o.timestamp >= m.date - make_interval(mins => %s)
             ORDER BY o.timestamp""", (chunk, markets, FRESH_MIN))
        q = defaultdict(lambda: defaultdict(list))
        for s in snaps:
            q[(s["mid"], s["market"])][s["selection"]].append((s["ts"], s["od"]))
        out, closes = [], {}
        for mid in chunk:
            for l in by_match[mid]:
                sides = sides_for(l["market"])
                row = [l["ledger"], l["leg_id"], mid, l["market"], l["selection"], l["odds"],
                       l["basis"], None, None, None, None, None]
                if sides is None or l["odds"] is None:
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
                out.append(tuple(row))
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO leg_clv_sharp (ledger, leg_id, match_id, market, selection, odds,
                        odds_basis, p_close, close_ts, close_age_min, clv_sharp, status)
                    VALUES %s ON CONFLICT (ledger, leg_id) DO NOTHING""", out, page_size=2000)
            conn.commit()
        log.info("clv_sharp: %d/%d matches", min(i + BATCH_MATCHES, len(mids)), len(mids))
    return dict(counts)


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    for k, v in sorted(run(a.limit).items()):
        print(f"  {k:40s} {v:7d}")


if __name__ == "__main__":
    main()
