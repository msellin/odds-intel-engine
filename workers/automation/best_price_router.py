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

SAFETY (three modes, see route()):
  * default DRY-RUN (execute=False, stage=False) — report the decision, touch nothing.
  * DRY-TEST-IN-ACTION (stage=True) — dispatch each routed pick to the winning book's
    executor in its own stage mode: drives the real slip on the live site and STOPS
    before the place click. A complete no-op against the account.
  * REAL MONEY (execute=True) — places at the winning book. DOUBLE-GATED: refused
    unless env `ROUTER_ALLOW_REAL` is truthy, so a stray execute=True cannot move
    money. This is the owner-gated cutover.
The executor arms are `coolbet_ui_placer.stage_bet` (Coolbet) and
`unibet_placer.place_bet` after `unibet_odds_feed.resolve_event_url` (Unibet), each
of which re-reads LIVE odds and gates on min_odds at dispatch time.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

PLACEABLE_BOOKS = ("Coolbet", "Unibet-Site")  # order = tiebreak preference on equal odds
ODDS_FRESH_MAX_MIN = 180  # a book's snapshot older than this doesn't compete
STAKE_EUR = float(os.getenv("ROUTER_STAKE_EUR", "10"))
# Band around the routed price used to pin the RIGHT outcome button on the site
# (button text is "<name><odds>"; a tight band separates e.g. 1x2-home from
# DNB-home). Wide enough to tolerate normal line movement; the executor still
# re-reads LIVE odds and gates on min_odds, so a big move aborts safely.
_ODDS_BAND_PCT = 0.12


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


def _unibet_outcome_name(market: str, selection: str, home: str, away: str):
    """Map a (market, selection) to the Unibet outcome-button text that
    unibet_placer.place_bet matches against. 1x2 buttons carry the team name
    (draw = 'X'); total buttons carry the Estonian 'Üle'/'Alla'. Returns None
    for a market we don't place on Unibet."""
    from workers.canonical_market import market_family
    fam = market_family(market) or ""
    s = (selection or "").lower()
    if fam == "1x2":
        if "home" in s:
            return home
        if "away" in s:
            return away
        if "draw" in s or s == "x":
            return "X"
    if fam.startswith("over_under") or fam in ("o/u", "ou"):
        if "over" in s:
            return "Üle"
        if "under" in s:
            return "Alla"
    return None


def _dispatch_unibet(pick: dict, decision: dict, *, execute: bool) -> dict:
    """Drive the winning Unibet slip: resolve the event URL, then place_bet.
    execute=False STAGES the slip (dry-test-in-action) and stops before the
    place click; execute=True places for real. Never raises."""
    try:
        from workers.automation.unibet_odds_feed import resolve_event_url
        from workers.automation import unibet_placer
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"import failed: {e}"}
    name = _unibet_outcome_name(pick["market"], pick["selection"],
                                pick.get("home_team"), pick.get("away_team"))
    if not name:
        return {"book": "Unibet-Site", "ok": False,
                "reason": f"no Unibet outcome mapping for {pick['market']}/{pick['selection']}"}
    try:
        r = resolve_event_url(pick.get("home_team"), pick.get("away_team"), pick.get("match_date"))
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"resolve_event_url raised: {e}"}
    if not r.get("url"):
        return {"book": "Unibet-Site", "ok": False, "reason": "resolve_event_url: no Unibet event URL"}
    o = float(decision["winner_odds"])
    lo, hi = round(o * (1 - _ODDS_BAND_PCT), 2), round(o * (1 + _ODDS_BAND_PCT), 2)
    try:
        res = unibet_placer.place_bet(
            r["url"], name, min_odds=float(decision["odds_floor"]),
            odds_lo=lo, odds_hi=hi, execute=execute, stake=STAKE_EUR)
    except Exception as e:  # noqa: BLE001
        return {"book": "Unibet-Site", "ok": False, "reason": f"place_bet raised: {e}",
                "event_url": r["url"], "outcome": name}
    placed = bool(res.get("placed"))
    staged = (not execute) and bool(res.get("reason") and "PAPER" in str(res.get("reason")))
    return {"book": "Unibet-Site", "ok": placed or staged, "event_url": r["url"],
            "outcome": name, "placed": placed, "staged": staged, "result": res}


def _dispatch_coolbet(pick: dict, *, execute: bool) -> dict:
    """Drive the winning Coolbet slip via coolbet_ui_placer.stage_bet.
    execute=False STAGES the slip (dry-test-in-action, a complete no-op against
    the account); execute=True places for real. Never raises."""
    try:
        from workers.automation import coolbet_ui_placer as up
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        return {"book": "Coolbet", "ok": False, "reason": f"import failed: {e}"}
    try:
        with sync_playwright() as pw:
            page = up.attach(pw)
            res = up.stage_bet(page, pick, STAKE_EUR, execute=execute)
    except Exception as e:  # noqa: BLE001
        return {"book": "Coolbet", "ok": False, "reason": f"stage_bet raised: {e}"}
    placed = bool(getattr(res, "placed", False))
    staged = (not execute) and bool(getattr(res, "ok", False))
    return {"book": "Coolbet", "ok": placed or staged, "placed": placed, "staged": staged,
            "result": {"placed": placed, "ok": getattr(res, "ok", None),
                       "notes": list(getattr(res, "notes", []) or [])}}


def _dispatch(winner: str, pick: dict, decision: dict, *, execute: bool) -> dict:
    """Route one decision to the winning book's executor arm."""
    if winner == "Unibet-Site":
        return _dispatch_unibet(pick, decision, execute=execute)
    if winner == "Coolbet":
        return _dispatch_coolbet(pick, execute=execute)
    return {"book": winner, "ok": False, "reason": f"no executor arm for book {winner}"}


def route(execute: bool = False, *, stage: bool = False, limit: int | None = None) -> dict:
    """Route the Family-1 real-money candidates across placeable books by best price.

    Three modes:
      * default (execute=False, stage=False) = DRY-RUN: report the routing
        decision, touch nothing.
      * stage=True = DRY-TEST-IN-ACTION: dispatch each routed pick to the winning
        book's executor with the executor's own execute=False, which drives the
        real slip (search → open event → select outcome → set+read-back stake →
        read slip) and STOPS before the place click. A complete no-op against the
        account, but exercises the entire wiring on the live site.
      * execute=True = REAL MONEY: places at the winning book. OWNER-GATED — refused
        unless env `ROUTER_ALLOW_REAL` is truthy, so a stray execute=True can never
        move money on its own.

    Never raises."""
    from workers.automation.coolbet_placer import _min_odds_for
    from scripts.place_coolbet_ui import PLACEABLE_BOTS, BOT_THRESHOLDS, load_picks

    # Real-money is double-gated: the caller's execute=True AND an explicit env
    # opt-in. Without the env flag, a True degrades to report-only (never staged
    # silently either) so nothing places by accident.
    real_allowed = os.getenv("ROUTER_ALLOW_REAL", "").lower() in ("1", "true", "yes")
    real = bool(execute and real_allowed)
    mode = "real" if real else ("stage" if stage else "report")

    out = {"candidates": 0, "routed": 0, "no_book_clears": 0, "already_placed": 0,
           "would_place": [], "skipped": [], "execute": execute, "mode": mode,
           "dispatched": 0}
    if execute and not real_allowed:
        out["real_refused"] = ("execute=True but ROUTER_ALLOW_REAL is not set — "
                               "real-money placement is owner-gated; reporting only")
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

        # Dispatch to the winning book's executor when staging (dry-drive) or
        # placing for real. Report mode touches nothing. `limit` caps how many
        # picks we actually drive (keeps a dry-test-in-action controlled).
        if (stage or real) and (limit is None or out["dispatched"] < limit):
            disp = _dispatch(winner, p, decision, execute=real)
            decision["dispatch"] = disp
            out["dispatched"] += 1
        elif real or stage:
            decision["dispatch"] = {"skipped": f"limit {limit} reached"}
    return out


def main() -> int:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Best-price router (Coolbet vs Unibet).")
    ap.add_argument("--stage", action="store_true",
                    help="DRY-TEST-IN-ACTION: drive the winning book's real slip and stop "
                         "before placing (no money moves)")
    ap.add_argument("--execute", action="store_true",
                    help="REAL MONEY at the winning book — owner-gated, also requires "
                         "env ROUTER_ALLOW_REAL=true; otherwise degrades to report-only")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap how many routed picks are actually driven (stage/execute)")
    a = ap.parse_args()
    res = route(execute=a.execute, stage=a.stage, limit=a.limit)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
