"""BEST-PRICE-EXECUTION-ROUTER (COOLBET-PICK-TABLE-AUDIT Stage 5) — DRY-RUN first.

The unified real-money placement decision the owner asked for: for each Family-1
`/picks` real-money candidate, read the CURRENT odds at every placeable book
(Coolbet + Unibet), gate each book on its OWN price, and place ONCE at the book
with the best clearing price — never two bets on the same (match, market, selection).

Router decision rule (BETTING_ARCHITECTURE §7, owner 2026-09-09):
  1. both books clear the gate  → place once at the BETTER price
  2. only one book clears        → place once there
  3. never two bets on the same (match, market, selection) — a cross-book exposure
     check (real_bets across books, placed_real IS NOT FALSE) blocks the second.
A book with no fresh odds simply doesn't compete (so one book being down never
costs the bet); the other still places if it clears.

Per-book gate = the SAME gate the placer enforces, evaluated at THAT book's price:
  edge = calibrated_prob − 1/odds  ≥  the bot's BOT_THRESHOLDS floor
  AND odds ≥ `_min_odds_for(market)` (1x2 2.80 / o/u 1.80).
This is why a soft book can rescue a pick the reference price misses (a home
underdog at 3.30 on Coolbet vs 3.55 on Unibet → route to Unibet).

SAFETY: `execute=False` (default) is DRY-RUN — it reports the routing decision and
places NOTHING. Real placement (`execute=True`) dispatches to the winning book's
executor (coolbet_ui_placer / unibet_placer) and is OWNER-GATED per cutover — it is
deliberately NOT wired yet; this module first proves the routing on live odds.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PLACEABLE_BOOKS = ("Coolbet", "Unibet-Site")  # order = tiebreak preference on equal odds
ODDS_FRESH_MAX_MIN = 180  # a book's snapshot older than this doesn't compete


def _latest_book_odds(match_id: str, market: str, selection: str):
    """Latest fresh pre-match odds per placeable book for one (match,market,selection).
    Returns {book: {'odds': float, 'age_min': int}}."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.bookmaker) o.bookmaker,
               o.odds::float AS odds,
               EXTRACT(EPOCH FROM (now() - o.timestamp))/60.0 AS age_min
          FROM odds_snapshots o
         WHERE o.match_id = %s AND o.market = %s AND o.selection = %s
           AND o.bookmaker = ANY(%s)
         ORDER BY o.bookmaker, o.timestamp DESC
        """,
        (match_id, market, selection, list(PLACEABLE_BOOKS)),
    )
    out = {}
    for r in rows or []:
        if r["odds"] and r["odds"] > 1 and float(r["age_min"]) <= ODDS_FRESH_MAX_MIN:
            out[r["bookmaker"]] = {"odds": float(r["odds"]), "age_min": round(float(r["age_min"]))}
    return out


def _has_exposure(match_id: str, market: str, selection: str) -> bool:
    """Cross-book dedup: is there already a REAL (non-paper) bet on this selection?"""
    from workers.api_clients.db import execute_query
    r = execute_query(
        """SELECT 1 FROM real_bets
            WHERE match_id = %s AND market = %s AND lower(selection) = lower(%s)
              AND placed_real IS NOT FALSE
            LIMIT 1""",
        (match_id, market, selection),
    )
    return bool(r)


def decide_book(cal_prob: float, threshold: float, odds_floor: float,
                book_odds: dict) -> dict:
    """Pure routing decision. `book_odds` = {book: odds}. A book clears iff its edge
    (cal_prob − 1/odds) ≥ threshold AND odds ≥ odds_floor. Winner = best clearing
    price; ties break by PLACEABLE_BOOKS order. Returns {clearing, winner, ...}."""
    clearing = {}
    for book, o in book_odds.items():
        if not o or o <= 1:
            continue
        edge = cal_prob - 1.0 / o
        if o >= odds_floor and edge >= threshold:
            clearing[book] = {"odds": o, "edge": round(edge, 4)}
    if not clearing:
        return {"clearing": {}, "winner": None}
    winner = max(clearing, key=lambda b: (clearing[b]["odds"],
                 -(PLACEABLE_BOOKS.index(b) if b in PLACEABLE_BOOKS else 99)))
    return {"clearing": clearing, "winner": winner,
            "winner_odds": clearing[winner]["odds"], "winner_edge": clearing[winner]["edge"]}


def route(execute: bool = False) -> dict:
    """Route the Family-1 real-money candidates across placeable books by best price.
    execute=False (default) = DRY-RUN (report only). Never raises."""
    from workers.automation.coolbet_placer import _min_odds_for
    from scripts.place_coolbet_ui import PLACEABLE_BOTS, BOT_THRESHOLDS, load_picks

    out = {"candidates": 0, "routed": 0, "no_book_clears": 0, "already_placed": 0,
           "would_place": [], "skipped": [], "execute": execute}
    # market label → placer floor key (o/u floors are keyed 'o/u')
    def _floor_key(m):
        from workers.canonical_market import market_family
        return market_family(m)

    picks = []
    for bot in sorted(PLACEABLE_BOTS):
        try:
            picks.extend(load_picks(bot))
        except Exception as e:  # noqa: BLE001
            log.warning("router: load_picks(%s) failed: %s", bot, e)
    out["candidates"] = len(picks)

    for p in picks:
        mid = p["match_id"]; market = p["market"]; sel = p["selection"]
        bot = p["bot_name"]; cal = p.get("calibrated_prob")
        label = f"{p.get('home_team')} v {p.get('away_team')} | {market}/{sel}"
        if cal is None:
            out["skipped"].append({"pick": label, "reason": "no calibrated_prob"}); continue
        cal = float(cal)
        if _has_exposure(mid, market, sel):
            out["already_placed"] += 1
            out["skipped"].append({"pick": label, "reason": "already have a real bet (cross-book dedup)"}); continue

        threshold = float(BOT_THRESHOLDS.get(bot, 0.03))
        odds_floor = _min_odds_for(_floor_key(market))
        books = _latest_book_odds(mid, market, sel)
        dec = decide_book(cal, threshold, odds_floor, {b: d["odds"] for b, d in books.items()})
        if not dec["winner"]:
            out["no_book_clears"] += 1
            out["skipped"].append({"pick": label, "reason": "no book clears the gate",
                                   "books": {b: d["odds"] for b, d in books.items()},
                                   "threshold": threshold, "odds_floor": odds_floor,
                                   "cal_prob": round(cal, 4)})
            continue
        winner = dec["winner"]
        out["routed"] += 1
        decision = {"pick": label, "bot": bot, "cal_prob": round(cal, 4),
                    "winner": winner, "winner_odds": dec["winner_odds"],
                    "winner_edge": dec["winner_edge"],
                    "all_clearing": dec["clearing"], "threshold": threshold, "odds_floor": odds_floor}
        out["would_place"].append(decision)
        if execute:
            # OWNER-GATED — deliberately not wired. Routing is proven dry-run first.
            decision["executed"] = False
            decision["execute_note"] = ("execute path not wired — Stage 5 dry-run only; "
                                        "wiring the winning book's executor is the owner-gated cutover")
    return out


def main() -> int:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Best-price router (Coolbet vs Unibet) — dry-run.")
    ap.add_argument("--execute", action="store_true",
                    help="(owner-gated, not wired) place at the winning book; default DRY-RUN")
    a = ap.parse_args()
    res = route(execute=a.execute)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
