"""SHARP ENGINE ([[#162]] W7.6, 2026-09-26) — ONE producer for every sharp-anchor bot.

WHAT A "SHARP" BOT IS: fair value = the de-vigged Pinnacle line (no model), and a pick is a
soft book's quote that beats it. Until this module that one idea had three implementations,
each with its own fair-price method, Pinnacle freshness rule, floor / ceiling, outlier cap and
book set (audit dev/archive/bot-refactor-audit/A-producers.md §2.1-§2.2, R13):

  * pick_generator `_candidates_from_sharp`  (bot_trigger_1x2_sharp_v1 / bot_trigger_ou_sharp_v1)
    Shin for 1x2 AND O/U, book quote <= 180 min (the router's), ceiling checked on the winning
    book only;
  * pick_triggers Stage A -> pick_trigger_matcher (the per-book sharp bots + the tight
    instrument): Shin, fair price frozen in a window at :05 and matched at :15/:45 (up to 40 min
    later), book quote <= 60 min, NO edge ceiling;
  * ou_sharp_outlier (O/U EARLY = VIP #2, O/U TWO-ANCHOR): its own power de-vig, EV unit,
    everything <= 3 h.

Now each of them is a `SharpRule` — a config — and `evaluate()` is the one decision. What stays
per producer is only what is genuinely per producer: how it LOADS quotes (the O/U job keeps its
own 72 h / 3 h / publishable-book loader) and how it WRITES (shadow_bets vs simulated_bets,
cohort names, ON CONFLICT DO NOTHING first-write-wins, VIP notification). Those are unchanged.

THE SHARED DEFAULTS (one number each, imported by every producer):
  * fair price   `devig.fair_prob` (#162 W3.1: Shin for 3-way, power for 2-way). The O/U sharp
                 trigger bots moved Shin -> power with this (rule_version r2).
  * anchor age   `anchor.anchor_line_too_old` (#162 W8.3: 7 h, 2 h inside 12 h of kick-off);
                 a rule may set a stricter fixed `anchor_max_age_h` (O/U EARLY: 3 h).
  * book quote   SHARP_BOOK_MAX_AGE_MIN = 60 — the engine's definition of a fresh decision quote
                 (SHARP-TRIGGERS-REFUSE-STALE: stale legs score a fake positive CLV).
  * ceiling      SHARP_EDGE_CEILING = 0.08 pp (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES: the largest
                 genuine overlay ever seen on Pinnacle is +6.6 %; the smallest phantom was +10.9 %).
  * outlier cap  anchor_sanity.OUTLIER_MULT (1.6) x the window's min odds.
  * wrong fixture anchor_sanity.is_anchor_sane against Pinnacle's own quote on that selection.
Every gate is applied to EVERY book before the best price is chosen, so a mis-priced top book can
no longer hide a clean second book (the generator used to check the ceiling on the winner only).

Pure where it can be: `evaluate` and everything above it take plain dicts and a clock, so the
smoke fixture and the parity replay feed it synthetic or point-in-time quotes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from workers.automation.anchor_sanity import OUTLIER_MULT, is_anchor_sane
from workers.model.devig import fair_prob
from workers.utils.anchor import anchor_line_too_old, market_sides, SHARP_ANCHOR_MAX_AGE_H

log = logging.getLogger(__name__)

ANCHOR_BOOK = "Pinnacle"
SHARP_BOOK_MAX_AGE_MIN = 60.0      # = shadow_bets_own_book_clv.decision_quote_fresh (<= 60)
SHARP_EDGE_CEILING = 0.08          # pp; between the +6.6 % real maximum and the +10.9 % smallest phantom
SHARP_ODDS_FLOOR = 1.01            # effectively off — the sharp paper bots observe every band

PICK_EACH_BOOK = "each_book"        # one candidate per (book, selection) that clears — per-book bots
PICK_BEST_BOOK = "best_book"        # per selection, the best clearing price across the rule's books
PICK_BEST_PER_LINE = "best_per_line"  # per (match, market), the single best-edge (book, selection)


@dataclass(frozen=True)
class SharpRule:
    """Everything that distinguishes one sharp bot from another. Nothing else should."""
    bot_name: str
    books: tuple[str, ...] | None            # books it may bet (order = tie-break); None = every non-anchor book
    selections: tuple[str, ...] | None = None  # None = every side of the market
    edge_unit: str = "pp"                    # 'pp': p - 1/odds  |  'ev': odds x p - 1
    edge_floor: float = 0.03
    edge_ceiling: float | None = SHARP_EDGE_CEILING
    odds_floor: float = SHARP_ODDS_FLOOR
    odds_ceiling: float | None = None
    outlier_mult: float | None = OUTLIER_MULT  # price <= max(1/(p - floor), odds floor) x mult (pp rules)
    anchor_book: str = ANCHOR_BOOK
    anchor_max_age_h: float | None = None    # None = the shared W8.3 rule (anchor_line_too_old)
    book_max_age_min: float = SHARP_BOOK_MAX_AGE_MIN
    overround: tuple[float, float] | None = None  # (lo, hi) open interval a line's sum(1/odds) must sit in
    sanity_guard: bool = True                # refuse a price Pinnacle's own quote contradicts (wrong fixture)
    min_hours_to_ko: float | None = None     # timing rule, e.g. O/U EARLY >= 12 h before kick-off
    consensus_min_books: int | None = None   # leave-one-out consensus of the other books (computed when set)
    consensus_edge_min: float | None = None  # ...and required to be beaten by this much (when set)
    pick: str = PICK_EACH_BOOK


def edge_of(unit: str, p: float, odds: float) -> float:
    return odds * p - 1.0 if unit == "ev" else p - 1.0 / odds


def fair_line(odds: list[float], overround: tuple[float, float] | None = None) -> list[float] | None:
    """Fair probabilities of a complete line (fixed order in, same order out) by THE shared rule,
    or None when the line fails the rule's overround guard or cannot be de-vigged."""
    if overround is not None:
        if any(o is None or o <= 0 for o in odds):
            return None
        lo, hi = overround
        if not (lo < sum(1.0 / o for o in odds) < hi):
            return None
    return fair_prob(list(odds))


def window(p: float, rule: SharpRule) -> tuple[float, float | None] | None:
    """(min_odds, max_odds) a pp-unit rule accepts for fair probability p, or None when no price
    can clear. The pick_triggers Stage-A formula: min = max(1/(p - floor), odds floor),
    max = min x OUTLIER_MULT, and an odds ceiling caps it (the tight instrument's 2.50)."""
    if p is None or p <= rule.edge_floor or p >= 1.0:
        return None
    lo = max(1.0 / (p - rule.edge_floor), rule.odds_floor)
    hi = lo * rule.outlier_mult if rule.outlier_mult else None
    if rule.odds_ceiling is not None:
        if lo > rule.odds_ceiling:
            return None              # the window lies entirely above the cap
        hi = rule.odds_ceiling if hi is None else min(hi, rule.odds_ceiling)
    return lo, hi


def price_refusal(rule: SharpRule, p: float, odds: float, anchor_odds: float | None,
                  hours_to_ko: float, edge_cons: float | None) -> str | None:
    """The one gate stack. None = the price clears; otherwise the first failing reason."""
    if odds is None or odds <= 1.0:
        return "no_price"
    if odds < rule.odds_floor:
        return "below_odds_floor"
    if rule.odds_ceiling is not None and odds > rule.odds_ceiling:
        return "above_odds_ceiling"
    e = edge_of(rule.edge_unit, p, odds)
    if e < rule.edge_floor:
        return "below_edge_floor"
    if rule.edge_ceiling is not None and e > rule.edge_ceiling:
        return "above_ceiling"
    if rule.outlier_mult and rule.edge_unit == "pp":
        w = window(p, rule)
        if w is None or (w[1] is not None and odds > w[1]):
            return "above_outlier_cap"
    if rule.sanity_guard and not is_anchor_sane(odds, anchor_odds):
        return "anchor_insane"
    if rule.min_hours_to_ko is not None and hours_to_ko < rule.min_hours_to_ko:
        return "too_close_to_ko"
    if rule.consensus_edge_min is not None and (edge_cons is None or edge_cons < rule.consensus_edge_min):
        return "consensus_not_beaten"
    return None


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def quote_fresh(age_min: float | None, max_age_min: float) -> bool:
    """THE fresh-decision-quote predicate: an age is known and within the cap (unknown = stale)."""
    return age_min is not None and age_min <= max_age_min


def _fresh(q: tuple[float, float] | None, now_ts: float, max_age_min: float) -> float | None:
    """The quote's odds if it exists and is fresh (quote_fresh), else None."""
    if q is None or q[0] is None or q[0] <= 1.0:
        return None
    return q[0] if quote_fresh((now_ts - q[1]) / 60.0, max_age_min) else None


def anchor_fair(rule: SharpRule, anchor_quotes: dict | None, sides: tuple[str, ...],
                now_ts: float, ko_ts: float) -> list[float] | None:
    """Fair probabilities from the anchor book's latest line, or None when it is incomplete,
    too old (the rule's fixed cap, else the shared W8.3 rule) or fails the overround guard."""
    if not anchor_quotes:
        return None
    qs = [anchor_quotes.get(s) for s in sides]
    if any(q is None or q[0] is None or q[0] <= 1.0 for q in qs):
        return None                  # need the complete line to de-vig honestly
    oldest_h = max((now_ts - q[1]) / 3600.0 for q in qs)
    if rule.anchor_max_age_h is not None:
        if oldest_h > rule.anchor_max_age_h:
            return None
    elif anchor_line_too_old(oldest_h, (ko_ts - now_ts) / 3600.0):
        return None
    return fair_line([q[0] for q in qs], rule.overround)


def evaluate(rule: SharpRule, lines: dict, now_ts: float, counts: dict | None = None,
             on_decision=None) -> list[dict]:
    """THE sharp decision. `lines` = {(match_id, market): {"ko": epoch s, "quotes": {book:
    {selection: (odds, epoch s)}}}} holding each book's latest pre-kick-off quote. Returns candidate
    picks {bot, match_id, market, selection, bookmaker, odds, p_fair, edge, edge_cons,
    quote_age_min, anchor_odds}. Book iteration follows the dict's insertion order, so equal-edge
    ties resolve exactly as the producers' own loops did. `counts`, when given, tallies why lines and
    prices were refused (for the producers' logs). `on_decision(rule, cand, why, book_quotes)`, when
    given, is called for every priced (book, selection) with its refusal reason (None = cleared) — the
    O/U job's candidate_funnel (#162 W7.5) reads it. It can never change or stop a pick: any error in
    it is swallowed."""
    out: list[dict] = []
    tally = counts if counts is not None else {}
    for (mid, market), line in lines.items():
        ko = line.get("ko")
        if ko is None or ko <= now_ts:
            continue
        sides = market_sides(market)
        if not sides:
            continue                 # no known full-market shape: never de-vig a partial line
        quotes = line.get("quotes") or {}
        anchor = quotes.get(rule.anchor_book)
        probs = anchor_fair(rule, anchor, sides, now_ts, ko)
        if probs is None:
            tally["no_fresh_anchor"] = tally.get("no_fresh_anchor", 0) + 1
            continue
        p_by = dict(zip(sides, probs))
        hours = (ko - now_ts) / 3600.0
        # leave-one-out consensus: each other book's own fair line, averaged in logit space
        cons: dict[str, list[float]] = {}
        if rule.consensus_min_books and len(sides) == 2:
            for b, q in quotes.items():
                if b == rule.anchor_book:
                    continue
                fo = [_fresh(q.get(s), now_ts, rule.book_max_age_min) for s in sides]
                if any(o is None for o in fo):
                    continue
                fp = fair_line(fo, rule.overround)
                if fp is not None:
                    cons[b] = [_logit(x) for x in fp]
        per_sel_best: dict[str, dict] = {}
        line_best: dict | None = None
        for b, q in quotes.items():
            if b == rule.anchor_book or (rule.books is not None and b not in rule.books):
                continue
            for sel in sides:
                if rule.selections and sel not in rule.selections:
                    continue
                if q.get(sel) is None:
                    continue
                o = _fresh(q.get(sel), now_ts, rule.book_max_age_min)
                if o is None:
                    tally["stale_skipped"] = tally.get("stale_skipped", 0) + 1
                    continue
                p = p_by[sel]
                edge_cons = None
                if rule.consensus_min_books and len(sides) == 2:
                    # 2-way only (the O/U TWO-ANCHOR rule, its one user): average the other books'
                    # logit of side 0 and take the complement for side 1
                    others = [v for k, v in cons.items() if k != b]
                    if len(others) >= rule.consensus_min_books:
                        pc0 = 1 / (1 + math.exp(-sum(v[0] for v in others) / len(others)))
                        edge_cons = edge_of(rule.edge_unit, pc0 if sides.index(sel) == 0 else 1 - pc0, o)
                aq = anchor.get(sel) if anchor else None
                a_odds = aq[0] if aq else None
                why = price_refusal(rule, p, o, a_odds, hours, edge_cons)
                if on_decision is not None:
                    try:
                        on_decision(rule, {"match_id": mid, "market": market, "selection": sel,
                                           "bookmaker": b, "odds": o, "p_fair": p,
                                           "edge": edge_of(rule.edge_unit, p, o), "edge_cons": edge_cons},
                                    why, q)
                    except Exception:  # noqa: BLE001 — diagnostics never touch the decision
                        pass
                if why is not None:
                    tally[why] = tally.get(why, 0) + 1
                    if why in ("anchor_insane", "above_ceiling", "above_outlier_cap"):
                        # a price this far from fair value is a stale or mis-mapped quote, not a gift
                        log.warning("SHARP-ENGINE %s: %s refused %s/%s on %s — %s %.2f vs Pinnacle %s "
                                    "(fair p %.3f)", rule.bot_name, why, market, sel, mid, b, o,
                                    a_odds, p)
                    continue
                cand = {"bot": rule.bot_name, "match_id": mid, "market": market, "selection": sel,
                        "bookmaker": b, "odds": o, "p_fair": p, "edge": edge_of(rule.edge_unit, p, o),
                        "edge_cons": edge_cons, "anchor_odds": a_odds,
                        "quote_age_min": round((now_ts - q[sel][1]) / 60.0, 1)}
                if rule.pick == PICK_BEST_PER_LINE:
                    if line_best is None or cand["edge"] > line_best["edge"]:
                        line_best = cand
                elif rule.pick == PICK_BEST_BOOK:
                    cur = per_sel_best.get(sel)
                    # best price; an equal price keeps the earlier book in rule.books (the router's tie-break)
                    if cur is None or o > cur["odds"] or (o == cur["odds"] and _rank(rule, b) < _rank(rule, cur["bookmaker"])):
                        per_sel_best[sel] = cand
                else:
                    out.append(cand)
        if rule.pick == PICK_BEST_PER_LINE and line_best is not None:
            out.append(line_best)
        elif rule.pick == PICK_BEST_BOOK:
            out.extend(per_sel_best[s] for s in sides if s in per_sel_best)
    return out


def _rank(rule: SharpRule, book: str) -> int:
    return rule.books.index(book) if rule.books and book in rule.books else 99


def lines_from_rows(rows: list[dict]) -> dict:
    """Group loader rows {match_id, market, bookmaker, selection, odds, ts, ko} into `lines`,
    keeping row order (so book order is the loader's ORDER BY)."""
    lines: dict = {}
    for r in rows:
        ln = lines.setdefault((str(r["match_id"]), r["market"]), {"ko": float(r["ko"]), "quotes": {}})
        ln["quotes"].setdefault(r["bookmaker"], {})[r["selection"]] = (float(r["odds"]), float(r["ts"]))
    return lines


def load_lines(markets: tuple[str, ...], books: tuple[str, ...],
               anchor_book: str = ANCHOR_BOOK) -> tuple[dict, float]:
    """(lines, now_ts): the latest pre-kick-off quote per (match, market, book, selection) for
    upcoming scheduled matches, from the anchor book and `books`. Only quotes newer than the
    shared anchor age cap are read: an older latest quote fails every freshness rule anyway (the
    anchor's W8.3 cap is the loosest one), so leaving it out changes no decision. The clock is the
    DB's NOW(), the same instant every age in the old SQL was measured from."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
               o.match_id::text AS match_id, o.market, o.bookmaker, o.selection,
               o.odds::float8 AS odds, EXTRACT(EPOCH FROM o."timestamp")::float8 AS ts,
               EXTRACT(EPOCH FROM m.date)::float8 AS ko, EXTRACT(EPOCH FROM NOW())::float8 AS now_ts
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE m.date > NOW() AND m.status = 'scheduled'
           AND o.market = ANY(%s) AND o.bookmaker = ANY(%s)
           AND o.is_live IS NOT TRUE AND o."timestamp" <= m.date
           AND o."timestamp" > NOW() - (%s * INTERVAL '1 hour')
         ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o."timestamp" DESC
        """,
        (list(markets), sorted(set(books) | {anchor_book}), SHARP_ANCHOR_MAX_AGE_H),
    ) or []
    if rows:
        now_ts = float(rows[0]["now_ts"])
    else:
        r = execute_query("SELECT EXTRACT(EPOCH FROM NOW())::float8 AS t") or [{"t": 0.0}]
        now_ts = float(r[0]["t"])
    return lines_from_rows(rows), now_ts


def run(rule: SharpRule, markets: tuple[str, ...]) -> list[dict]:
    """Load and evaluate one rule on live data. May raise (a DB error): callers run it inside their
    own never-raise wrapper, as they did the code it replaced."""
    lines, now_ts = load_lines(markets, rule.books or ())
    return evaluate(rule, lines, now_ts)
