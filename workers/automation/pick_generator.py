"""PICK-GENERATOR — one mechanism, N bots, differing only by configuration.

Built 2026-09-11 to the owner's brief: *"at some point we will merge the mirror
and trigger bots so that there will be like n amount of bots that real money ui
placers use, we need to keep that in mind and build for the future scalability,
so the bots look very similar in the way they work, only the gates and
configuration of the bots are different."*

THE MECHANISM (identical for every bot, and the only copy of it)
---------------------------------------------------------------
    1. take candidate PROBABILITIES from the pipeline (`calibrated_prob`)
    2. price each candidate at every book THIS BOT may bet
    3. compute the edge AT that book's price
    4. gate: edge >= the bot's edge floor AND price >= its odds floor
    5. emit at the best clearing book, storing the DERIVED edge and the book

WHAT A BOT IS, THEREFORE: a `BotConfig`. Adding one is a config entry, not a new
job file. That is the point — two near-identical 250-line mirrors had already
drifted apart in ways that cost real bets, and a third would have drifted
further.

WHY EACH STEP IS SHAPED THIS WAY — every one is a bug we actually shipped
------------------------------------------------------------------------
* **Price at OUR books, not the pipeline's.** The mirrors used to copy
  `odds_at_pick` from `simulated_bets`, which carries whichever book the
  pipeline recommended. Nancy v Reims was rejected on Betano's 3.15 (edge
  0.092) while Coolbet was live at 3.25 (edge 0.102 — clears). The placer
  re-checks at the live price so it can never STAKE a bad one, but it can only
  NARROW this set: a pick that clears at our book and not at the pipeline's was
  lost before the placer saw it.
* **Derive the edge, never carry it.** `edge_percent` was stored independently
  of price and probability and had drifted on 9 of 11 pending picks. One bot
  carried a row priced 2.49 with edge 0.0800 whose own UI then demanded 2.51,
  because the stored edge belonged to a 2.5113 price. Here edge is computed
  from the winning price and cannot disagree with it.
* **Record which book.** Trigger rows were written with
  `recommended_bookmaker` NULL — 100% of them. That made per-book analysis
  impossible, left cross-book dedup nothing to key on, and made settlement's
  per-book closing lookup fall back to "any book", computing CLV against a
  price we never had.
* **The pre-filter is the NECESSARY condition only.** `cal_prob > edge_floor`,
  because edge = cal_prob - 1/odds is always below cal_prob. Filtering on the
  pipeline's odds or edge pre-judges a price we are not going to use.
* **Derive at decision time; cache nothing.** Every bug found on 2026-09-11 was
  a stored derivation drifting from its source — stale odds, stale edge, six
  copies of the floors, a hardcoded bot list, tests pinning removed behaviour.
  A precomputed window would put that same shape back on the real-money path.

FLOORS COME FROM THE REGISTRY, NOT FROM HERE. `edge_floor=None` means "use the
shared selection-aware `min_edge_for_pick`", and `odds_floor=None` means "use
`_min_odds_for(market)`". A bot may override with a number, but the DEFAULT is
always the engine's own value — the 1x2 edge floor had six independent copies
once, and re-typing it per bot is how that happens.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    """Everything that distinguishes one bot from another. Nothing else should.

    `convert` handles the one genuinely per-bot transform: some markets are
    stored in the pipeline's vocabulary and must be written in the placer's
    (o/u lines live in the selection legacy-side and in the market
    canonical-side). It takes (market, selection) and returns the pair to WRITE,
    or None to skip an unsupported line. Identity when omitted.
    """
    bot_name: str
    shadow_cohort: str
    markets: tuple[str, ...]                     # matched case-insensitively
    books: tuple[str, ...]                       # books this bot may bet
    stake: float = 10.0
    selections: tuple[str, ...] | None = None    # None = every selection
    edge_floor: float | None = None              # None = selection-aware registry floor
    odds_floor: float | None = None              # None = registry per-market floor
    convert: Callable[[str, str], tuple[str, str] | None] | None = None
    maturity: tuple[str, ...] = ("calibrated",)  # source cohort in simulated_bets
    lookahead_hours: int | None = None           # None = any future kickoff
    notes: str = field(default="", compare=False)


def _bot_id(name: str) -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [name])
    return r[0]["id"] if r else None


def _floors(cfg: BotConfig, market: str, selection: str, odds: float | None):
    """(edge_floor, odds_floor) for one pick — registry-derived unless the bot
    deliberately overrides. Kept in ONE place so a new bot cannot re-type a
    floor and silently diverge from the engine."""
    from workers.automation.coolbet_placer import min_edge_for_pick, _min_odds_for
    of = cfg.odds_floor if cfg.odds_floor is not None else float(_min_odds_for(market))
    if cfg.edge_floor is not None:
        return float(cfg.edge_floor), of
    # Selection-aware: 1x2 home-underdogs clear at 10%, everything else pooled.
    # Evaluated at the ODDS FLOOR, which is exact rather than approximate — no
    # price below the odds floor can be emitted, so a HOME candidate is
    # necessarily in underdog territory.
    return float(min_edge_for_pick(market, selection, odds if odds else of)), of


def generate(cfg: BotConfig) -> dict:
    """Run one bot. Never raises — a generation failure must not take down the
    sweep or pipeline that called it."""
    c = {"scanned": 0, "written": 0, "no_book_price": 0,
         "no_book_clears": 0, "unsupported": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        from workers.automation.best_price_router import (
            _latest_book_odds, decide_book,
        )

        bot_id = _bot_id(cfg.bot_name)
        if not bot_id:
            log.warning("pick_generator: bot %s not registered — skipping",
                        cfg.bot_name)
            return c

        # The floor used for the SQL pre-filter must be the loosest this bot can
        # apply, or the pre-filter would drop candidates a per-selection floor
        # would have admitted. For a selection-aware bot that is the minimum
        # across selections; for a fixed-floor bot it is that floor.
        from workers.automation.coolbet_placer import (
            min_edge_for_pick, _min_odds_for,
        )
        if cfg.edge_floor is not None:
            loosest = float(cfg.edge_floor)
        else:
            cands = [min_edge_for_pick(m, s, float(_min_odds_for(m)))
                     for m in cfg.markets
                     for s in (cfg.selections or ("home", "draw", "away",
                                                  "over", "under"))]
            finite = [x for x in cands if x != float("inf")]
            loosest = min(finite) if finite else float("inf")
        if loosest == float("inf"):
            log.info("pick_generator: %s has no finite floor (retired market?) "
                     "— generating nothing", cfg.bot_name)
            return c

        sel_clause = ""
        params: list = [list(cfg.markets), list(cfg.maturity), loosest]
        if cfg.selections:
            sel_clause = "AND lower(sb.selection) = ANY(%s)"
            params.append(list(cfg.selections))
        ahead = ""
        if cfg.lookahead_hours:
            ahead = "AND m.date < NOW() + (%s * INTERVAL '1 hour')"
            params.append(cfg.lookahead_hours)

        rows = execute_query(
            f"""
            SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                   sb.match_id::text AS match_id, sb.market, sb.selection,
                   sb.calibrated_prob, sb.model_probability
              FROM simulated_bets sb
              JOIN bots    b ON b.id = sb.bot_id
              JOIN matches m ON m.id = sb.match_id
             WHERE lower(sb.market) = ANY(%s)
               AND b.maturity_label = ANY(%s)
               AND sb.calibrated_prob IS NOT NULL
               AND sb.calibrated_prob > %s
               AND sb.result = 'pending'
               AND sb.combo_legs IS NULL
               AND sb.user_placed_at IS NULL
               AND sb.user_skipped_at IS NULL
               AND m.date > NOW()
               {sel_clause}
               {ahead}
             ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
            """,
            params,
        )

        run_id = str(uuid.uuid4())
        for r in rows:
            src_market = (r["market"] or "").lower().strip()
            src_sel = (r["selection"] or "").lower().strip()
            if cfg.convert:
                conv = cfg.convert(src_market, src_sel)
                if conv is None:
                    c["unsupported"] += 1
                    continue
                market, selection = conv
            else:
                market, selection = src_market, src_sel
            c["scanned"] += 1

            cal_prob = float(r["calibrated_prob"] or 0)
            book_odds = _latest_book_odds(r["match_id"], market, selection)
            book_odds = {b: v["odds"] for b, v in book_odds.items()
                         if b in cfg.books}
            if not book_odds:
                c["no_book_price"] += 1
                continue

            ef, of = _floors(cfg, market, selection, None)
            decision = decide_book(cal_prob, ef, of, book_odds,
                                   market=market, selection=selection)
            won_book = decision.get("winner")
            if not won_book:
                c["no_book_clears"] += 1
                continue
            price = float(decision["winner_odds"])
            edge = cal_prob - 1.0 / price     # derived; cannot disagree with price

            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market,
                        selection, odds_at_pick, odds_at_pick_live, pick_time,
                        stake, model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s,%s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick          = EXCLUDED.odds_at_pick,
                        odds_at_pick_live     = EXCLUDED.odds_at_pick_live,
                        model_probability     = EXCLUDED.model_probability,
                        calibrated_prob       = EXCLUDED.calibrated_prob,
                        edge_percent          = EXCLUDED.edge_percent,
                        recommended_bookmaker = EXCLUDED.recommended_bookmaker""",
                [run_id, cfg.shadow_cohort, bot_id, r["match_id"], market,
                 selection, price, price, cfg.stake,
                 r["model_probability"], r["calibrated_prob"], edge, won_book],
            )
            c["written"] += 1

        log.info("pick_generator[%s]: scanned %d, wrote %d "
                 "(no price %d, no clear %d, unsupported %d)",
                 cfg.bot_name, c["scanned"], c["written"],
                 c["no_book_price"], c["no_book_clears"], c["unsupported"])
    except Exception as e:  # noqa: BLE001
        log.warning("pick_generator[%s] raised (non-fatal): %s", cfg.bot_name, e)
    return c


def generate_all(configs: list[BotConfig]) -> dict:
    """Run every bot. Used by the odds-arrival hook so one call regenerates
    everything a new price could have changed — the shared choke point that
    means adding a book or a bot needs no new wiring."""
    return {cfg.bot_name: generate(cfg) for cfg in configs}
