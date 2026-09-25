"""
BOOK-AGNOSTIC-EDGE-ENGINE Stage B — the per-book sharp bots (PAPER).
See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.

For each (book, market, sharp strategy) in BOOK_MARKET_BOTS, asks the ONE sharp engine
(workers/automation/sharp_engine.py, #162 W7.6) which of that book's latest quotes beat the
de-vigged Pinnacle line under the strategy's rule (`pick_triggers.sharp_rule`), and writes a
`shadow_bet` for each — the CORRECT book-native selection (edge evaluated at the book's OWN
odds). Until 2026-09-26 it joined the Stage A windows in `pick_triggers` instead; those rows are
still written, as a record, by the same engine functions.

SAFETY: these bots write shadow_bets only. They have a placement path since #139, but their
eligibility row is seeded OFF and only an audited /admin/bots action can switch it on. No model
call, no HTTP — one bounded DB read per (book, market). First write wins (ON CONFLICT DO NOTHING).
Run:  python3 -m workers.jobs.pick_trigger_matcher
"""
from __future__ import annotations

import logging
import uuid

log = logging.getLogger(__name__)

STAKE_EUR = 10.0
# (book, odds-snapshot market, pick_triggers.strategy) → the paper bot its matches
# emit into. One bot per (book × market × anchor) so each combo's ROI is tracked
# and gated independently. The `strategy` key is what keeps the model anchor and
# the sharp (de-vigged Pinnacle) anchor in separate bots even though they share a
# fixture, market and Coolbet price — see docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.
BOOK_MARKET_BOTS = {
    # OWN Phase 5 cull (2026-09-15): the four MODEL-anchored per-book entries
    # (bot_coolbet_trigger_1x2_v1 / _ou_v1, bot_unibet_trigger_1x2_v1 / _ou_v1)
    # were removed — all four are retired in the DB (migrations 336 / 348) and
    # `_bot_id` was already refusing them, so each entry ran a query every 30 min
    # and no-opped. The SHARP twins stay.
    ("Coolbet", "1x2",           "sharp_1x2"):  "bot_coolbet_trigger_sharp_1x2_v1",
    ("Coolbet", "over_under_25", "sharp_ou25"): "bot_coolbet_trigger_sharp_ou_v1",
    # UNIBET-TRIGGER-BOTS-2026-09-09 (Stage 3b): same trigger engine, second book.
    # Reads odds_snapshots bookmaker='Unibet-Site' (the broad site sweep). Paper.
    # NB the DRAW edge — which the model can't see — lives here on the SHARP anchor
    # (soft-book mispricing vs de-vig Pinnacle); see ANALYSIS_GOTCHAS §57.
    ("Unibet-Site", "1x2",           "sharp_1x2"):  "bot_unibet_trigger_sharp_1x2_v1",
    ("Unibet-Site", "over_under_25", "sharp_ou25"): "bot_unibet_trigger_sharp_ou_v1",

    # SHARP-TIGHT-INSTRUMENT-2026-09-15. THREE books into ONE bot, deliberately.
    # Every other row here is per-book, which was the older pattern; bot_configs
    # already records why it was collapsed ("the BOOK is not a strategy, it is a
    # venue"). More importantly the result this instrument exists to test was
    # measured POOLED across placeable books (n=225, +17.07%), so a per-book
    # split would measure something other than the thing under test, at a third
    # of the n. `recommended_bookmaker` still carries the venue per row, so the
    # per-book question stays answerable without three bots.
    ("Coolbet",     "1x2", "sharp_1x2_tight"): "bot_trigger_1x2_sharp_tight_v1",
    ("Unibet-Site", "1x2", "sharp_1x2_tight"): "bot_trigger_1x2_sharp_tight_v1",
    ("Epicbet",     "1x2", "sharp_1x2_tight"): "bot_trigger_1x2_sharp_tight_v1",
    # Tonybet ADDED 2026-09-24 (sweeper-odds audit): it became a placeable own book
    # on 2026-09-23 and this instrument pools across our placeable books. The venue
    # stays on each row (`recommended_bookmaker`), so pre/post-Tonybet is separable.
    ("Tonybet",     "1x2", "sharp_1x2_tight"): "bot_trigger_1x2_sharp_tight_v1",
}


def _cohort_for(book: str) -> str:
    """shadow_cohort per book so each book's trigger picks group + settle separately.

    PER-BOOK COHORT (2026-09-24, #125 review). This returned "coolbet_trigger" for
    EVERY non-Unibet book. The shadow_bets upsert key is (cohort, bot, match, market,
    selection), so Coolbet and Epicbet legs of the pooled tight bot overwrote each
    other (48 Coolbet vs 192 Epicbet rows had survived), and a later book's price
    could sit beside an earlier book's pick time and quote age. Coolbet and Unibet
    keep their historical cohort names; Epicbet and Tonybet get their own from here —
    a discontinuity in the cohort column for Epicbet, not in the bot's record."""
    b = book.lower()
    if b.startswith("unibet"):
        return "unibet_trigger"
    return f"{b.split('-')[0]}_trigger"


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


def match_and_emit(book: str, market: str, strategy: str, bot_name: str) -> dict:
    """Emit shadow_bets for every upcoming fixture whose `book` `market` price clears the sharp
    `strategy` under `bot_name`. Never raises.

    #162 W7.6 (2026-09-26, rule_version r2 for every bot here): the decision is the ONE sharp
    engine's (workers/automation/sharp_engine.py) on `pick_triggers.sharp_rule(strategy, book)`,
    evaluated NOW — it no longer joins the Stage-A window written at :05, whose fair price was up to
    40 min old by the :45 run. Same window formula, floors, odds cap, 60-min quote cap
    (the strategy's freshness ceiling below, age_min <= cap — see is_fresh_enough) and wrong-fixture guard
    (anchor_sanity.is_anchor_sane on Pinnacle's anchor_odds); new: the O/U fair price is power, not
    Shin (devig.fair_prob), and the 1x2 / O/U strategies carry the engine's edge ceiling. The write
    below is unchanged: this book's cohort, first write wins, the decision-quote age recorded."""
    counters = {"book": book, "market": market, "strategy": strategy,
                "matched": 0, "written": 0}
    try:
        from workers.api_clients.db import execute_write
        from workers.automation import sharp_engine as se
        from workers.jobs.pick_triggers import sharp_rule, _stamp_cal, _SHARP_MODEL_VERSION
        bot_id = _bot_id(bot_name)
        if not bot_id:
            log.info("trigger matcher: bot %s is retired or not registered — "
                     "skipping (RETIRED-BOTS-KEPT-GENERATING)", bot_name)
            return counters
        rule = sharp_rule(strategy, book, bot_name)
        if rule is None:
            log.warning("trigger matcher: %s is not a runnable sharp strategy (no rule or no "
                        "freshness ceiling) — %s emits nothing", strategy, bot_name)
            return counters
        cohort = _cohort_for(book)

        lines, now_ts = se.load_lines((market,), (book,))
        cands = se.evaluate(rule, lines, now_ts, counts=counters)
        counters["matched"] = len(cands)
        run_id = str(uuid.uuid4())
        mv = _stamp_cal(_SHARP_MODEL_VERSION)
        for r in cands:
            price = float(r["odds"])
            cal = float(r["p_fair"])
            age_min = r["quote_age_min"]
            edge = cal - 1.0 / price   # edge at the book's OWN price
            n_written = execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker, model_version, decision_quote_age_min)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s,%s,%s,%s)
                   -- #162 W2.1 (owner 11A, 2026-09-25): the FIRST decision is the pick — no 30-min
                   -- re-evaluation rewrite of price/prob/edge (it kept the first pick_time, so the recorded
                   -- price drifted from the quote at pick time). odds_at_pick_live = this bot's own-book
                   -- price at the decision (review: not #159's MAX across all four books).
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
                # decision_quote_age_min records the age at the FIRST decision (pick_time); since
                # #162 W2.1 nothing about the pick is rewritten by a later re-evaluation.
                # TRIGGER-BOOK-UNATTRIBUTED (2026-09-11): `book` is recorded — per-book analysis,
                # cross-book dedup and settlement's per-book closing price all key on it.
                # TRIGGER-CALIBRATOR-REVISION (2026-09-11): the stamped model_version rides onto the
                # pick so eras stay separable.
                [run_id, cohort, bot_id, r["match_id"], r["market"], r["selection"],
                 price, price, STAKE_EUR, cal, cal, edge, book,
                 mv, (round(age_min, 1) if age_min is not None else None)],
            )
            counters["written"] += int(n_written or 0)   # DO NOTHING on a re-sweep writes 0 rows
        log.info("trigger matcher (%s/%s/%s): %s", book, market, strategy, counters)
    except Exception as e:  # noqa: BLE001
        log.warning("trigger matcher raised (non-fatal): %s", e)
    return counters


# SHARP-TIGHT-FRESHNESS (2026-09-15). Strategies that REFUSE a stale decision
# quote, and the ceiling in minutes.
#
# ⭐ EXTENDED TO EVERY SHARP STRATEGY 2026-09-20 (SHARP-TRIGGERS-REFUSE-STALE).
# It was the tight instrument alone, and the other sharp strategies had NO
# ceiling at all — they took whatever the last sweep left in `odds_snapshots`,
# however old. On 2026-09-19 that meant `bot_coolbet_trigger_sharp_1x2_v1`
# raising a pick at 01:15 UTC against a Coolbet quote from 18:15 the previous
# evening: a SEVEN-HOUR-OLD price, during a feed outage, for a bot the operator
# places real money from.
#
# WHY THIS IS A CORRECTNESS FIX AND NOT A TUNING CHOICE. `edge` is computed
# against a price, and a price nobody could take is not a price. Worse, the
# error is DIRECTIONAL: CLV scores the quote against the close, so an old quote
# that the market has since moved away from scores as a WIN. Measured that week
# on the legs where an age is recorded (within the age-recorded era only, so the
# comparison is not confounded with time):
#
#     bot                       fresh (<=60m) CLV   stale (>60m) CLV
#     bot_coolbet_trigger_sharp_1x2_v1   -1.50% (n=62)   +5.30% (n=15)
#     bot_unibet_trigger_sharp_1x2_v1    -5.04% (n=42)   +0.37% (n=25)
#     bot_coolbet_trigger_sharp_ou_v1    -5.21% (n=11)   +7.35% (n=8)
#     bot_unibet_trigger_sharp_ou_v1     -3.90% (n=10)   +2.62% (n=10)
#     bot_trigger_1x2_sharp_tight_v1     -3.89% (n=138)  n=0  <- already gated
#
# Stale legs positive, fresh legs negative, every bot, same direction — and the
# one strategy that already had this gate has no stale legs to contribute. That
# is `ANCHOR_IS_NOT_SHARP`'s finding exactly: "the positive numbers came from
# comparing a six-hour-old book quote against a Pinnacle quote at kickoff. The
# edge was the gap." Small n on the stale side (8-25); the DIRECTION is what
# justifies the gate, not the magnitude.
#
# ⚠️ THIS PUTS A DISCONTINUITY IN FOUR BOTS' SERIES, AT 2026-09-20. They are
# accumulating toward the pre-registered n=300, and from here they collect a
# different population — roughly 25-40% fewer legs for the Unibet arms on recent
# days. That is deliberate: the excluded legs were never actionable, so keeping
# them to protect a clean series would mean measuring a strategy nobody could
# have executed. Anyone reading their CLV across this date must split on it.
# Nothing is backfilled or deleted; `decision_quote_age_min` is on every leg
# since 2026-09-15, so the same cut is reproducible over the history.
#
# 60.0 is not a new number — it is the engine's existing definition of a fresh
# decision quote (`shadow_bets_own_book_clv.decision_quote_fresh` is `<= 60`,
# and the shadow-bots page's DECISION_FRESH_MAX_MIN is 60).
# #162 W7.6: the value is the sharp engine's SHARP_BOOK_MAX_AGE_MIN, the one fresh-quote cap every
# sharp bot now runs on (the merged generator bots moved to it from the router's 180 min).
from workers.automation.sharp_engine import SHARP_BOOK_MAX_AGE_MIN as _FRESH  # noqa: E402
FRESHNESS_MAX_AGE_MIN: dict[str, float] = {
    "sharp_1x2_tight": _FRESH,
    "sharp_1x2": _FRESH,
    "sharp_ou25": _FRESH,
}


def is_fresh_enough(strategy: str, age_min: float | None) -> bool:
    """True unless `strategy` has a freshness ceiling AND the quote is older
    than it (or its age is unknown — unknown is stale for a gated strategy).
    The predicate is the sharp engine's `quote_fresh` — the same function its
    gate runs on every book quote — so this and the matcher cannot disagree."""
    from workers.automation.sharp_engine import quote_fresh
    cap = FRESHNESS_MAX_AGE_MIN.get(strategy)
    if cap is None:
        return True
    return quote_fresh(age_min, cap)


def run_all() -> dict:
    out = {}
    for (book, market, strategy), bot_name in BOOK_MARKET_BOTS.items():
        out[bot_name] = match_and_emit(book, market, strategy, bot_name)
    return out


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(run_all(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
