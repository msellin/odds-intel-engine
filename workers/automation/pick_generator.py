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
    # EDGE CEILING — refuse a pick whose edge is TOO GOOD (None = no ceiling).
    #
    # SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20. Only meaningful when the
    # probability is derived from a near-true line: against a de-vigged Pinnacle
    # the largest overlay this project has ever seen is +6.6% (see
    # `prob_source` below, which has said so since the source was written), so an
    # apparent +20% is not a find — it is arithmetic on a price from a different
    # football match. `bot_trigger_1x2_sharp_v1` published 25 picks and every
    # single one sat above +10.9%, because a mis-mapped fixture maximises
    # `edge = p - 1/odds` and the bot's selection logic is a search for exactly
    # that. The generic `anchor_sanity` ratio guard catches the flagrant 80% of
    # them; this catches the rest, and it is the tighter gate precisely BECAUSE
    # it only applies where fair value is already near-true.
    #
    # Do NOT set this on a model-anchored bot. A model edge of 20% is ordinary
    # (the registry floor is 13%) and a ceiling there would gut the bot.
    edge_ceiling: float | None = None
    convert: Callable[[str, str], tuple[str, str] | None] | None = None
    # Source cohort for prob_source='pipeline': the bots whose pending picks feed this generator, BY NAME.
    # #162 W4.4 (2026-09-25): was `maturity=("calibrated",)` — "every bot labelled calibrated" — so the
    # #155 status rollout (statuses = distribution, owner policy §3.1) would silently change what the
    # real-money-CAPABLE Coolbet model bots see. Real money is not a status. Same set as before the
    # change: bot_v10_1x2 was the only calibrated bot (verified on the DB 2026-09-25).
    source_bots: tuple[str, ...] = ("bot_v10_1x2",)
    # WHERE THE CANDIDATE PROBABILITIES COME FROM. This is the single most
    # consequential setting here, and getting it wrong is why the mirrors were
    # ~81x narrower than the trigger bots.
    #
    #   'pipeline'    — `simulated_bets.calibrated_prob`, i.e. only fixtures the
    #                   pipeline ALREADY picked. That pick list is built from AF
    #                   API odds, which are not current and DO NOT INCLUDE
    #                   COOLBET at all, so it is narrow for reasons that have
    #                   nothing to do with our edge. Measured over the next 48h:
    #                   1 fixture with a 1x2 pipeline pick.
    #   'predictions' — `predictions.model_probability` for EVERY fixture we
    #                   model, calibrated here. Same 48h: 81 fixtures. This is
    #                   the idea the trigger bots were built on, and it is the
    #                   reason they raised 318 picks in 7 days where the mirrors
    #                   raised 27.
    #
    # 'pipeline' is kept because it is the probability the real-money bots were
    # VALIDATED on; 'predictions' re-calibrates independently. Both are exposed
    # so the two can be compared on the same mechanism instead of argued about.
    #   'sharp_devig'  — fair value from the Shin-de-vigged Pinnacle line, not
    #                   our model at all. A bot on this source MUST set
    #                   `edge_floor` explicitly (3%-ish): inheriting the
    #                   registry's model floors would demand a 13% overlay on
    #                   Pinnacle, which is nearly unobservable (max seen +6.6%),
    #                   so the bot would never fire.
    prob_source: str = "pipeline"
    lookahead_hours: int | None = None           # None = any future kickoff
    notes: str = field(default="", compare=False)


def _bot_id(name: str) -> str | None:
    """Resolve a bot name to its id — and ONLY if the bot is still active.

    RETIRED-BOTS-KEPT-GENERATING (2026-09-14). This lookup used to be a bare
    `WHERE name=%s`, so retiring a bot by setting `retired_at` / `is_active`
    in the `bots` table did NOT stop it producing picks: the generator resolved
    its id exactly as before and kept writing shadow_bets under a bot the fleet
    considered dead. Every retirement migration in this repo's history therefore
    stopped the bot appearing on the page while leaving it running underneath.

    That is this repo's recurring "a second code path inheriting no gates"
    pattern (RELIABILITY_LEDGER), and the same gap is documented for the placer
    at SYSTEM_MAP 4c. Gating here closes it for BOTH generation paths at once —
    pick_generator's BotConfig list and pick_trigger_matcher's BOOK_MARKET_BOTS
    both funnel through a `_bot_id` lookup — so a DB retirement is now
    self-enforcing and no code edit is needed to make one take effect.
    """
    from workers.api_clients.db import execute_query
    r = execute_query(
        "SELECT id::text AS id FROM bots WHERE name=%s AND retired_at IS NULL",
        [name])
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
            log.info("pick_generator: bot %s is retired or not registered — "
                     "skipping (RETIRED-BOTS-KEPT-GENERATING)", cfg.bot_name)
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
        params: list = [list(cfg.markets), list(cfg.source_bots), loosest]
        if cfg.selections:
            sel_clause = "AND lower(sb.selection) = ANY(%s)"
            params.append(list(cfg.selections))
        ahead = ""
        if cfg.lookahead_hours:
            ahead = "AND m.date < NOW() + (%s * INTERVAL '1 hour')"
            params.append(cfg.lookahead_hours)

        if cfg.prob_source == "predictions":
            rows = _candidates_from_predictions(cfg, loosest)
        elif cfg.prob_source == "sharp_devig":
            rows = _candidates_from_sharp(cfg, loosest)
        else:
            rows = _candidates_from_pipeline(cfg, loosest, sel_clause, ahead, params)

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
            # SHARP-FLOOR-STACKED-ON-MODEL-FLOOR (2026-09-22, [[#007]]). When a
            # bot sets `edge_floor` explicitly, THAT is the policy — the router
            # must not also re-impose the selection-aware MODEL floor on top of
            # it. `_floors()` already resolves the two correctly (explicit wins);
            # `decide_book` was independently applying `clears_edge_floor()`
            # regardless, so the sharp bots ran a 10-13% effective floor against
            # a de-vigged Pinnacle whose maximum genuine overlay is +6.6%. See
            # the docstring in best_price_router.decide_book for why that made
            # them phantom-price detectors rather than a losing strategy.
            # Model-anchored bots (edge_floor=None) keep the stacked behaviour.
            decision = decide_book(cal_prob, ef, of, book_odds,
                                   market=market, selection=selection,
                                   apply_selection_floor=cfg.edge_floor is None)
            won_book = decision.get("winner")
            if not won_book:
                c["no_book_clears"] += 1
                continue
            price = float(decision["winner_odds"])
            edge = cal_prob - 1.0 / price     # derived; cannot disagree with price

            # EDGE CEILING (see BotConfig.edge_ceiling). Against a near-true
            # anchor an implausibly large edge is evidence the PRICE is wrong,
            # not that the opportunity is big — and it is the one error mode the
            # floor cannot catch, because the floor is a lower bound and this
            # fault pushes edges up.
            # OUTLIER CAP — INHERITED, NOT INVENTED (2026-09-20). The matcher
            # engine has bounded every sharp window since it was written:
            # the trigger writer emits `max_odds = min_odds x OUTLIER_MULT` and
            # `pick_trigger_matcher` enforces it. THIS path is a second
            # implementation of the same sharp anchor and it silently dropped
            # that half of the window — the clone kept the floor and lost the
            # cap. Applied to the 25 phantom picks the cap alone rejects 14,
            # including every egregious one (101.00, 18.00, 9.00).
            #
            # It is kept ALONGSIDE `edge_ceiling` because neither dominates:
            # the cap is a bound in ODDS space and the ceiling a bound in EDGE
            # space, and they cross near cal_prob ~0.18 — below it the cap is
            # tighter, above it the ceiling is. Importing `OUTLIER_MULT` rather
            # than re-typing 1.6 is the point; a copied constant is how these
            # two paths diverged in the first place.
            if cfg.prob_source == "sharp_devig" and cal_prob > ef:
                from workers.automation.anchor_sanity import OUTLIER_MULT
                max_odds = max(1.0 / (cal_prob - ef), of) * OUTLIER_MULT
                if price > max_odds:
                    c["above_outlier_cap"] = c.get("above_outlier_cap", 0) + 1
                    log.warning(
                        "OUTLIER-CAP: %s skipped %s/%s on %s — %s %.2f is above "
                        "the window's max_odds %.2f. A price this far above fair "
                        "value is a stale or mis-mapped quote, not a gift.",
                        cfg.bot_name, market, selection, r["match_id"],
                        won_book, price, max_odds,
                    )
                    continue

            if cfg.prob_source not in ("predictions", "sharp_devig"):
                ok, why = _own_outlier_ok(r["match_id"], market, selection, price)
                if not ok:
                    c[why] = c.get(why, 0) + 1
                    continue

            if cfg.edge_ceiling is not None and edge > cfg.edge_ceiling:
                c["above_ceiling"] = c.get("above_ceiling", 0) + 1
                log.warning(
                    "EDGE-CEILING: %s skipped %s/%s on %s — edge %.1f%% at %s "
                    "%.2f exceeds the %.1f%% ceiling. Fair value here is a "
                    "de-vigged sharp line, so this is a mis-priced or mis-mapped "
                    "quote, not an overlay.",
                    cfg.bot_name, market, selection, r["match_id"], edge * 100,
                    won_book, price, cfg.edge_ceiling * 100,
                )
                continue

            n_written = execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market,
                        selection, odds_at_pick, odds_at_pick_live, pick_time,
                        stake, model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker, model_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s,%s,%s,%s,%s,%s)
                   -- #162 W2.1 (owner 11A, 2026-09-25): the FIRST write is the pick. The old DO UPDATE
                   -- rewrote price/prob/edge on every sweep but kept the first pick_time, so 22-52 per cent of
                   -- rows carried a price 1.0-3.8 per cent above the quote at pick time (audit A §2).
                   -- odds_at_pick_live = the price at THIS bot's book at pick time (review: #159's backfill
                   -- would store the MAX across all four Estonian books — a price this bot could not take).
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
                [run_id, cfg.shadow_cohort, bot_id, r["match_id"], market,
                 selection, price, price, cfg.stake,
                 r["model_probability"], r["calibrated_prob"], edge, won_book,
                 r.get("model_version")],
            )
            c["written"] += int(n_written or 0)   # DO NOTHING on a re-sweep writes 0 rows

        log.info("pick_generator[%s/%s]: scanned %d, wrote %d "
                 "(no price %d, no clear %d, unsupported %d)",
                 cfg.bot_name, cfg.prob_source, c["scanned"], c["written"],
                 c["no_book_price"], c["no_book_clears"], c["unsupported"])
    except Exception as e:  # noqa: BLE001
        log.warning("pick_generator[%s] raised (non-fatal): %s", cfg.bot_name, e)
    return c


# #129 REVIEW FOLLOW-UP (2026-09-24). #129 widened the 👥 PICKS outlier anchor to every
# publishable book, so fixtures with no Pinnacle and < 3 Estonian books now reach
# `simulated_bets` — and `_candidates_from_pipeline` feeds OWN bots from that table.
# Before #129 those fixtures never reached it, so this bot family never saw them. OWN
# must keep the rule it had: a 1X2 / BTTS / DC price needs an anchor built from the
# Estonian reference set (Pinnacle, else the median of >= 3 ACCESSIBLE books) and must
# sit within the same 1.25x ceiling. `_latest_book_odds`' own sanity check fails OPEN
# without an anchor, which is why this is not redundant.
_OWN_OUTLIER_MULT = {"1x2": 1.25, "btts": 1.25, "double_chance": 1.25}


def _own_outlier_ok(match_id: str, market: str, selection: str, price: float) -> tuple[bool, str]:
    mult = _OWN_OUTLIER_MULT.get(market)
    if mult is None:
        return True, ""
    from statistics import median
    from workers.api_clients.db import execute_query
    from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS
    rows = execute_query(
        """SELECT DISTINCT ON (o.bookmaker) o.bookmaker, o.odds::float AS odds
             FROM odds_snapshots o
            WHERE o.match_id = %s AND o.market = %s AND o.selection = %s
              AND o.bookmaker = ANY(%s)
            ORDER BY o.bookmaker, o.timestamp DESC""",
        (match_id, market, selection, sorted(ACCESSIBLE_BOOKMAKERS | {"Pinnacle"})),
    ) or []
    quotes = {r["bookmaker"]: r["odds"] for r in rows if r["odds"] and r["odds"] > 1}
    anchor = quotes.get("Pinnacle")
    if anchor is None:
        acc = [v for b, v in quotes.items() if b in ACCESSIBLE_BOOKMAKERS]
        if len(acc) < 3:
            return False, "no_own_anchor"
        anchor = median(acc)
    if price > anchor * mult:
        return False, "above_own_outlier"
    return True, ""


def _candidates_from_pipeline(cfg, loosest, sel_clause, ahead, params):
    """Only fixtures the PIPELINE already picked. Narrow for reasons unrelated
    to our edge: that pick list is built from AF API odds, which are not current
    and do not include Coolbet."""
    from workers.api_clients.db import execute_query
    return execute_query(
            f"""
            SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                   sb.match_id::text AS match_id, sb.market, sb.selection,
                   sb.calibrated_prob, sb.model_probability
              FROM simulated_bets sb
              JOIN bots    b ON b.id = sb.bot_id
              JOIN matches m ON m.id = sb.match_id
             WHERE lower(sb.market) = ANY(%s)
               AND b.name = ANY(%s)
               AND b.is_active AND b.retired_at IS NULL   -- a retired source stops feeding at once (review 2026-09-25)
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


def _candidates_from_sharp(cfg, loosest):
    """Fair value = Shin-de-vigged Pinnacle, not our model.

    A THIRD kind of probability, and the reason `edge_kind` has to be a
    dimension everywhere: a sharp edge is measured against a near-true line, so
    3% is a real 3% overlay, where a 3% MODEL edge is noise. That is why
    `pick_triggers._SHARP_MIN_EDGE_BY_MARKET` is 3% while the model floors are
    13%/8%, and why a bot on this source must set `edge_floor` explicitly
    instead of inheriting the registry's model floors — inheriting them would
    demand a 13% overlay on Pinnacle, which is nearly unobservable (max seen
    +6.6%) and the bot would simply never fire.

    Needs the COMPLETE line to de-vig honestly: a partial market is skipped
    rather than de-vigged from two of three prices.
    """
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig
    from workers.jobs.pick_triggers import _SHARP_ANCHOR_BOOK
    from workers.utils.anchor import anchor_line_too_old

    # `sides` must be the full market in a fixed order — devig returns
    # probabilities positionally.
    market = cfg.markets[0]
    if market == "1x2":
        sides = ("home", "draw", "away")
    elif market.startswith("over_under"):
        sides = ("over", "under")
    else:
        log.info("pick_generator[%s]: sharp_devig does not know the full-market "
                 "shape for %s — generating nothing rather than de-vigging a "
                 "partial line", cfg.bot_name, market)
        return []

    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection)
               o.match_id::text AS mid, o.selection, o.odds::float AS odds,
               EXTRACT(EPOCH FROM (NOW() - o.timestamp)) / 3600.0 AS age_h,
               EXTRACT(EPOCH FROM (m.date - NOW())) / 3600.0 AS ko_in_h
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = %s AND o.market = %s
           AND o.timestamp <= m.date AND m.date > NOW() AND m.status = 'scheduled'
         ORDER BY o.match_id, o.selection, o.timestamp DESC
        """,
        [_SHARP_ANCHOR_BOOK, market],
    )
    by_match: dict[str, dict] = {}
    oldest_h: dict[str, float] = {}
    ko_in: dict[str, float] = {}
    for r in rows:
        by_match.setdefault(r["mid"], {})[r["selection"]] = r["odds"]
        oldest_h[r["mid"]] = max(oldest_h.get(r["mid"], 0.0), float(r["age_h"] or 0.0))
        ko_in[r["mid"]] = float(r["ko_in_h"] or 0.0)

    wanted = set(cfg.selections) if cfg.selections else set(sides)
    out = []
    stale = 0
    for mid, quotes_by_sel in by_match.items():
        # SHARP-ANCHOR-MAX-AGE (#162 W8.3): the ONE staleness rule (workers/utils/anchor.py). Only tightens.
        if anchor_line_too_old(oldest_h.get(mid, 0.0), ko_in.get(mid, 99.0)):
            stale += 1
            continue
        quotes = [quotes_by_sel.get(s) for s in sides]
        if any(q is None or q <= 1.0 for q in quotes):
            continue                      # incomplete line — do not guess
        probs = devig(quotes)
        if probs is None:
            continue
        for sel, p_sharp in zip(sides, probs):
            # SHARP-PREFILTER-COMPARES-PROB-TO-EDGE (2026-09-22, [[#007]]).
            # `loosest` is an EDGE floor; `p_sharp` is a PROBABILITY. Comparing
            # them is a category error that happens to be harmless at 0.03 (it
            # only drops sub-3% probabilities, which no floor would pass anyway)
            # and becomes silently destructive the moment a sharp `edge_floor`
            # is raised — at 0.30 it would discard every selection under 30%
            # probability for a reason having nothing to do with edge. The
            # NECESSARY condition on a probability is that a bet at the best
            # possible price could still clear the floor; since edge =
            # p - 1/price and price is unknown here, the only sound pre-filter
            # is p > 0. Edge is gated properly downstream by decide_book.
            if sel not in wanted or p_sharp is None or p_sharp <= 0.0:
                continue
            out.append({"match_id": mid, "market": market, "selection": sel,
                        "calibrated_prob": float(p_sharp),
                        "model_probability": float(p_sharp)})
    if stale:
        log.info("pick_generator[%s]: %d %s line(s) skipped — Pinnacle anchor too old", cfg.bot_name, stale, market)
    return out


def _candidates_from_predictions(cfg, loosest):
    """EVERY fixture we model, calibrated here — the idea the trigger bots were
    built on, and ~100x wider than the pipeline's pick list (~300-400 predicted
    fixtures a day x 3 selections, against ~10 pipeline picks).

    Calibration uses `pick_triggers._fit_calibrator`, which was made
    PER-SELECTION on 2026-09-11. Pooled, it under-estimated HOME by 10-15pp and
    the resulting windows only ever fired on longshots. Reused rather than
    re-fitted so there is one calibrator, not a third.

    Returns rows shaped like the pipeline source so the caller is source-blind.
    """
    from workers.api_clients.db import execute_query
    from workers.jobs.pick_triggers import _fit_calibrator

    want = {m.lower() for m in cfg.markets}
    if "1x2" not in want:
        # #162 W7.2 (2026-09-26): 1x2 only. The O/U half (`_predictions_ou`, and
        # the 'ou25'/'ou35' calibrator it used) was deleted with its only bot,
        # bot_trigger_ou_model_v1 (retired 2026-09-14). Every other market has
        # no calibrator, so no candidates rather than a guessed calibration.
        log.info("pick_generator[%s]: prob_source='predictions' supports 1x2 "
                 "only; %s is not 1x2, so no candidates", cfg.bot_name, cfg.markets)
        return []
    cal = _fit_calibrator("1x2")
    if cal is None:
        log.warning("pick_generator[%s]: 1x2 calibrator unavailable — "
                    "generating nothing rather than using raw probabilities",
                    cfg.bot_name)
        return []

    sels = cfg.selections or ("home", "draw", "away")
    pred_markets = [f"1x2_{s}" for s in sels]
    ahead_sql = ""
    params: list = [pred_markets]
    if cfg.lookahead_hours:
        ahead_sql = "AND m.date < NOW() + (%s * INTERVAL '1 hour')"
        params.append(cfg.lookahead_hours)
    rows = execute_query(
        f"""
        SELECT DISTINCT ON (p.match_id, p.market)
               p.match_id::text AS match_id, p.market AS pred_market,
               p.model_probability::float AS praw, p.model_version AS mv
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.market = ANY(%s) AND m.date > NOW() AND m.status = 'scheduled'
           -- #162 W2.2: the production model only. predictions is unique per (match, market, source), so this is
           -- exactly ONE row: the pipeline's latest production write (was: any source — AF / raw xgboost /
           -- national_team_v1 won the version-string sort on ~100 fixtures per 1x2 selection)
           AND p.source = 'ensemble'
           AND p.model_probability IS NOT NULL
           {ahead_sql}
         ORDER BY p.match_id, p.market, p.model_version DESC
        """,
        params,
    )
    # TRIGGER-CALIBRATOR-REVISION: stamp WHICH calibrator shaped these, exactly
    # as pick_triggers does for its windows. Without it, picks generated here
    # would carry a NULL model_version and `trigger_calibrator_check` would
    # classify them as PRE-fix — silently mixing corrected picks into the
    # known-biased bucket and destroying the comparison that gates the whole
    # convergence epic. A stamp is cheap; a corrupted baseline is not.
    from workers.jobs.pick_triggers import _stamp_cal
    out = []
    for r in rows:
        sel = r["pred_market"].replace("1x2_", "")
        cp = cal(r["praw"], sel)
        if cp is None or cp <= loosest:
            continue          # no price can clear when cal_prob <= the floor
        out.append({"match_id": r["match_id"], "market": "1x2",
                    "selection": sel, "calibrated_prob": cp,
                    "model_probability": r["praw"],
                    "model_version": _stamp_cal(r.get("mv"))})
    return out


def generate_all(configs: list[BotConfig] | None = None) -> dict:
    """Run every bot. The shared choke point, so adding a book or a bot needs no
    new wiring anywhere."""
    if configs is None:
        # ALL_CONFIGS, not CONFIGS: the wide-source paper twins must run on every
        # odds arrival too, or the comparison they exist for never accumulates.
        from workers.automation.bot_configs import ALL_CONFIGS
        configs = ALL_CONFIGS
    return {cfg.bot_name: generate(cfg) for cfg in configs}


def on_odds_written(book: str | None = None) -> dict:
    """ODDS-ARRIVAL HOOK — call this at the END of a book's odds sweep.

    This is the "trigger mechanism" the owner asked for, in the form that adds
    no cache. The generator already derives everything at decision time; firing
    it when a price LANDS rather than on a 30-minute clock removes the three
    remaining problems in one move:

      * STALENESS — the mirror gated Nancy on a 14.8h-old Coolbet quote of 3.10
        while the live price was 3.25 and clearing. When the price IS the event,
        a stale quote cannot gate anything.
      * THE RACE — Coolbet sweeps :03/:33, Unibet :15/:45, the generators ran
        :10/:40. A qualifying price could wait ~30 min, which for a match
        kicking off in 40 is the entire window.
      * MULTI-CHECK — no polling pass re-deriving every candidate on a clock
        whether or not anything changed.

    Deliberately NOT implemented as precomputed windows. A window is a stored
    derivation of `min_odds` from `cal_prob`, i.e. a cache that must be
    invalidated when the model re-predicts — and every bug found on 2026-09-11
    was a stored derivation drifting from its source. This keeps the derivation
    at decision time and only changes WHEN it happens.

    `book` is accepted for logging and future narrowing; today every bot
    compares across all its books, so a price at either one can change the
    winner and all configs are re-run.

    MUST NEVER RAISE: the caller's job is collecting odds, and that has to
    survive a pick-generation failure. `generate` already swallows per-bot
    errors; this adds a belt on top.
    """
    try:
        out = generate_all()
        total = sum(r.get("written", 0) for r in out.values())
        log.info("on_odds_written(%s): %d picks written/updated across %d bots",
                 book or "all", total, len(out))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("on_odds_written(%s) raised (non-fatal, odds collection "
                    "is unaffected): %s", book, e)
        return {}
