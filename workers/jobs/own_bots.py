"""[[#182]] OWN BOT — the paper bot designed for OWN, priced only at the Estonian books we can bet (2026-09-26).

Owner: *"it doesn't matter how many picks — they need to be better quality than /picks, on par with VIP
or better. Maybe we should build our own bots for OWN picks — designed very precisely to our OWN needs."*

ONE BOT PER MARKET (owner): `bot_own_1x2_v1`; the book is recorded on each pick (best clearing book).

THE RULE (from the pre-registered #182 filter study, dev/active/own-bot-filter-study-findings.md):
per-book sharp-lag picks hold up against an INDEPENDENT close only when taken in the LAST 3 HOURS before
kick-off (+4.9%, n 190, holdout +4.7%, Holm p < 0.001); earlier, the fair-price move usually reverts.
Pooled rule +5.7% [+3.9, +7.6], n 134. So each bot fires when, for a 1X2 selection at ITS book:

  1. fair value = the v2 ANCHOR (workers/utils/anchor.py: Pinnacle + Betfair Exchange blend or either
     alone; a Pinnacle-vs-exchange conflict gives NO price; else a >= 5-book consensus WITHOUT this book)
     — the same anchors_for_books the OWN board uses;
  2. the book's price beats it by EV >= 3%, clearing the sharp engine's gates (8% ceiling, outlier cap,
     wrong-fixture guard, quote <= 60 min old) — own_bet_board.build does exactly this;
  3. kick-off is less than 3 h away (and more than 3 min — the placer's block);
  4. CONFIRMATION: >= 3 other books (not Pinnacle, not the exchange, not our Estonian books) have a
     fresh complete line whose median fair probability for the selection is >= 0.97 x the anchor's.

⚠️ WHAT IS NOT KNOWN (why these are PAPER bots, maturity 'experimental', not public):
  * flat ROI on the study's legs was −18% ± 10 while CLV was +5.7% — CLV is the pre-registered judge;
  * near-kick-off prices may not survive to placement (the stale-window study: 33-50% still on the board
    at the next sweep) — the paper record measures that only through the price we actually recorded;
  * "confirmation" partly favours itself by construction (the judge's close uses many of the same books).
Judged on clv_cons at the recorded (executable) price; n ~= 60-80 per book to see +3%; ~2-3 weeks.

Writes shadow_bets (first decision is the pick: ON CONFLICT DO NOTHING — #162 W2.1).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from statistics import median

from workers.jobs.own_bet_board import EDGE_FLOOR, KO_BLOCK_MIN, anchors_for_books, build
from workers.utils.anchor import EXCHANGE, PIN, _EX_MARKETS, load_exchange, load_sets, market_sides
from workers.model.devig import devig

log = logging.getLogger(__name__)

RULE_VERSION = "r1"
MARKET = "1x2"
MAX_HOURS_TO_KO = 3.0
CONFIRM_MIN_BOOKS = 3
CONFIRM_RATIO = 0.97
CONFIRM_MAX_AGE_MIN = 180
STAKE_EUR = 10.0
# ONE bot per market, the book recorded on every pick (owner 2026-09-26: "better a single bot, or at least
# one per market, not a bot for every book — it gets too messy"). The study measured the rule pooled over
# books too. Per selection it takes the BEST clearing book — the one to bet — and records it in
# recommended_bookmaker, so a per-book record is one GROUP BY away.
OWN_BOT = "bot_own_1x2_v1"
# Every book we can bet (ACCESSIBLE_BOOKMAKERS). Tonybet included on the owner's question ("why not
# Tonybet?"): its price sits ~11% under its own Sportradar fair (ANALYSIS_GOTCHAS §87) so it will rarely
# win, but that is for the record to show.
OWN_BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Tonybet")
_NOT_CONFIRMERS = {PIN, EXCHANGE, "Coolbet", "Unibet-Site", "Epicbet", "Tonybet", "Optibet"}


def confirmation(sets: dict, sides: tuple[str, ...], sel: str, p_anchor: float, at) -> tuple[int, float | None]:
    """Pure: (books, median fair prob of `sel`) over fresh complete lines of confirming books."""
    ps = []
    for b, (odds, ts) in sets.items():
        if b in _NOT_CONFIRMERS or (at - ts).total_seconds() / 60.0 > CONFIRM_MAX_AGE_MIN:
            continue
        p = devig(odds)
        if p:
            ps.append(p[sides.index(sel)])
    return len(ps), (median(ps) if ps else None)


def select(board_rows: list[dict], sets_by_line: dict, at) -> list[dict]:
    """Pure: the OWN picks from board rows — rule steps 2-4, the best clearing, confirmed book per selection."""
    out = []
    sides = market_sides(MARKET)
    for r in board_rows:
        if r["market"] != MARKET:
            continue
        hours = (r["kickoff"] - at).total_seconds() / 3600.0
        if not (KO_BLOCK_MIN / 60.0 < hours <= MAX_HOURS_TO_KO):
            continue
        best = None
        for book in OWN_BOOKS:
            pr = r["prices"].get(book)
            if not pr or pr["refusal"] is not None or not pr.get("p_fair"):
                continue
            n, pc = confirmation(sets_by_line.get((r["match_id"], MARKET), {}), sides, r["selection"], pr["p_fair"], at)
            if n < CONFIRM_MIN_BOOKS or pc is None or pc < CONFIRM_RATIO * pr["p_fair"]:
                continue
            if best is None or pr["odds"] > best["odds"]:
                best = {"bot": OWN_BOT, "book": book, "match_id": r["match_id"], "selection": r["selection"],
                        "odds": pr["odds"], "p_fair": pr["p_fair"], "edge": pr["edge"], "age_min": pr["age_min"],
                        "anchor": pr["anchor"], "confirm_books": n, "confirm_p": pc}
        if best:
            out.append(best)
    return out


def _all_candidates(at) -> tuple[list[dict], dict]:
    """Every 1X2 selection kicking off within MAX_HOURS_TO_KO, priced like the board (not only bot picks)."""
    from workers.api_clients.db import execute_query
    from workers.automation.sharp_engine import load_lines
    books = OWN_BOOKS
    matches = execute_query(
        """SELECT m.id::text AS match_id, m.date AS kickoff FROM matches m
            WHERE m.status = 'scheduled' AND m.date > now() + make_interval(mins => %s)
              AND m.date <= now() + make_interval(mins => %s)""", (KO_BLOCK_MIN, int(MAX_HOURS_TO_KO * 60))) or []
    if not matches:
        return [], {}
    picks = [{"source": "own", "pick_id": "", "bot_name": "own", "match_id": m["match_id"], "market": MARKET,
              "selection": s, "pick_time": "", "kickoff": m["kickoff"]}
             for m in matches for s in market_sides(MARKET)]
    lines, now_ts = load_lines((MARKET,), books)
    sides = market_sides(MARKET)
    sets_by_line, anchors = {}, {}
    for m in matches:
        k = (m["match_id"], MARKET)
        sets = load_sets(m["match_id"], MARKET, sides, at=at)
        ex = load_exchange(m["match_id"], MARKET, sides, at=at) if MARKET in _EX_MARKETS else None
        sets_by_line[k] = sets
        anchors[k] = anchors_for_books(sets, ex, sides, books, at)
    return build(picks, lines, now_ts, books, anchors), sets_by_line


def run(dry_run: bool = False) -> dict:
    from workers.api_clients.db import execute_query, execute_write
    at = datetime.now(timezone.utc)
    rows, sets_by_line = _all_candidates(at)
    picks = select(rows, sets_by_line, at)
    ids = {r["name"]: r["id"] for r in execute_query(
        "SELECT name, id::text AS id FROM bots WHERE name = %s AND retired_at IS NULL", (OWN_BOT,)) or []}
    written, run_id = 0, str(uuid.uuid4())
    for p in picks:
        if dry_run or p["bot"] not in ids:
            continue
        written += int(execute_write(
            """INSERT INTO shadow_bets
                   (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                    odds_at_pick, odds_at_pick_live, pick_time, stake,
                    model_probability, calibrated_prob, edge_percent,
                    recommended_bookmaker, model_version, decision_quote_age_min)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
            [run_id, "own", ids[p["bot"]], p["match_id"], MARKET, p["selection"],
             p["odds"], p["odds"], STAKE_EUR, p["p_fair"], p["p_fair"], p["p_fair"] - 1.0 / p["odds"],
             p["book"], f"own_v2anchor_{p['anchor']}", p["age_min"]]) or 0)
    log.info("OWN-BOTS: %d selections < %.0f h, %d picks, %d written%s", len(rows), MAX_HOURS_TO_KO,
             len(picks), written, " (dry run)" if dry_run else "")
    return {"candidates": len(rows), "picks": len(picks), "written": written,
            "sample": picks[:5] if dry_run else None}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    print(run(dry_run="--dry-run" in sys.argv))
