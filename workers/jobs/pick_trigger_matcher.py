"""
BOOK-AGNOSTIC-EDGE-ENGINE Stage B — the matcher (PAPER).
See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.

Joins each book's latest swept odds (`odds_snapshots`) against the Stage A trigger
windows (`pick_triggers`) and writes a `shadow_bet` for any price landing in
[min_odds, max_odds] — the CORRECT Coolbet-native selection (edge evaluated at the
book's OWN odds). Runs PAPER alongside the current simulated_bets-copy mirror so
the wider universe can be measured before it is ever trusted with money.

SAFETY: the emit bot (`bot_coolbet_trigger_v1`) is NOT in the placer's
PLACEABLE_BOTS whitelist and has no coolbet_placer_bots toggle — it can never
stake money; it only writes shadow_bets. No model call, no HTTP — a bounded DB
join. Idempotent upsert. Run:  python3 -m workers.jobs.pick_trigger_matcher
"""
from __future__ import annotations

import logging
import uuid

from workers.automation.anchor_sanity import ANCHOR_BOOK, consensus_median_quotes, is_anchor_sane

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
}


def _cohort_for(book: str) -> str:
    """shadow_cohort per book so each book's trigger picks group + settle separately."""
    return "unibet_trigger" if book.lower().startswith("unibet") else "coolbet_trigger"


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
    """Emit shadow_bets for every upcoming fixture whose `book` `market` price lands
    inside its Stage A `strategy` trigger window, under `bot_name`. Never raises."""
    counters = {"book": book, "market": market, "strategy": strategy,
                "matched": 0, "written": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_id = _bot_id(bot_name)
        if not bot_id:
            log.info("trigger matcher: bot %s is retired or not registered — "
                     "skipping (RETIRED-BOTS-KEPT-GENERATING)", bot_name)
            return counters
        cohort = _cohort_for(book)

        rows = execute_query(
            """
            WITH latest AS (  -- latest pre-match price per (match, selection) for this book+market
              SELECT DISTINCT ON (o.match_id, o.selection)
                     o.match_id::text AS mid, o.selection, o.odds::float AS odds,
                     -- SHARP-TIGHT-FRESHNESS (2026-09-15): how old this quote is
                     -- at decision time. One row per poll, so the row's own
                     -- timestamp IS the last observation of the price.
                     EXTRACT(EPOCH FROM (NOW() - o.timestamp)) / 60.0 AS age_min
                FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
               WHERE o.bookmaker = %s AND o.market = %s
                 AND o.timestamp <= m.date AND m.date > NOW()
               ORDER BY o.match_id, o.selection, o.timestamp DESC
            ),
            -- ANCHOR-PRICE-SANITY (2026-09-20). The SECOND pricing engine, and
            -- it had no anchor check either — the exact "second code path
            -- inheriting no gates" shape in docs/RELIABILITY_LEDGER.md. Joined
            -- LEFT so a fixture Pinnacle does not price still emits (fail open,
            -- see workers/automation/anchor_sanity). Deliberately NOT
            -- freshness-capped: it answers "is this the right match?", not
            -- "may I stake here?".
            anchor AS (
              SELECT DISTINCT ON (o.match_id, o.selection)
                     o.match_id::text AS mid, o.selection, o.odds::float AS odds
                FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
               WHERE o.bookmaker = %s AND o.market = %s
                 AND o.is_live IS NOT TRUE
                 AND o.timestamp <= m.date AND m.date > NOW()
               ORDER BY o.match_id, o.selection, o.timestamp DESC
            )
            SELECT t.match_id::text AS mid, t.market, t.selection,
                   t.cal_prob::float AS cal, l.odds AS book_odds,
                   l.age_min::float AS age_min,
                   a.odds AS anchor_odds,
                   t.model_version AS mv
              FROM pick_triggers t
              JOIN latest l ON l.mid = t.match_id::text AND l.selection = t.selection
              LEFT JOIN anchor a ON a.mid = t.match_id::text AND a.selection = t.selection
             WHERE t.market = %s AND t.strategy = %s AND t.kickoff_at > NOW()
               AND l.odds >= t.min_odds
               AND (t.max_odds IS NULL OR l.odds <= t.max_odds)
            """,
            [book, market, ANCHOR_BOOK, market, market, strategy],
        )
        counters["matched"] = len(rows)
        run_id = str(uuid.uuid4())
        cons_ref: dict = {}
        for r in rows:
            price = float(r["book_odds"])
            if price <= 1.0:
                continue
            age_min = float(r["age_min"]) if r.get("age_min") is not None else None
            # SHARP-TIGHT-FRESHNESS-REFUSES-STALE (2026-09-15, OWN Phase 1a).
            # OWN-ANCHOR-GATE-VERIFICATION measured the instrument's slope at
            # +1.31 on stale decision quotes and +0.35 once the quote had to be
            # ≤60 min old — 26-45% of legs priced off a quote >4h stale, and
            # across a >12h gap 72% of Coolbet quotes had moved. A stale quote is
            # a price nobody could take. The instrument refuses it; every other
            # strategy still records the age so the same cut can be made later.
            if not is_fresh_enough(strategy, age_min):
                counters["stale_skipped"] = counters.get("stale_skipped", 0) + 1
                continue
            # ANCHOR-PRICE-SANITY (2026-09-20). A quote the anchor contradicts
            # by >1.56x is a price from ANOTHER FIXTURE, and it arrives looking
            # like the best edge on the board — `edge = cal - 1/price` rewards
            # exactly the rows that are most wrong. Refuse before it is written.
            ref, ref_label = r.get("anchor_odds"), ANCHOR_BOOK
            if ref is None:
                # #113: no Pinnacle quote → median of >=4 other books (fail-open below that)
                if r["mid"] not in cons_ref:
                    cons_ref[r["mid"]] = consensus_median_quotes(r["mid"], r["market"])
                ref, ref_label = cons_ref[r["mid"]].get(r["selection"]), "4+-book median"
            if not is_anchor_sane(price, ref):
                counters["anchor_insane_skipped"] = counters.get("anchor_insane_skipped", 0) + 1
                log.warning(
                    "ANCHOR-PRICE-SANITY: skipped %s %s/%s on %s — book %.2f vs "
                    "%s %.2f. Mis-mapped fixture, not an edge.",
                    book, r["market"], r["selection"], r["mid"], price,
                    ref_label, float(ref),
                )
                continue
            edge = float(r["cal"]) - 1.0 / price   # edge at the book's OWN price
            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker, model_version, decision_quote_age_min)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick      = EXCLUDED.odds_at_pick,
                        odds_at_pick_live = EXCLUDED.odds_at_pick_live,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent,
                        recommended_bookmaker = EXCLUDED.recommended_bookmaker,
                        model_version         = EXCLUDED.model_version""",
                # decision_quote_age_min is deliberately NOT in the DO UPDATE set:
                # it records the age at the FIRST decision (pick_time), and a
                # 30-min re-evaluation must not overwrite it (verifier 2026-09-15).
                # TRIGGER-BOOK-UNATTRIBUTED (2026-09-11): every trigger row was
                # written with recommended_bookmaker NULL — 100% of them, 639 of
                # a 950-pick sample landing in the unattributed bucket. `book`
                # has been right here in scope the whole time. Three things were
                # broken by the omission: per-book analysis was impossible for
                # two-thirds of the data; cross-book dedup had nothing to key on
                # (the same failure class as unibet_placer writing nothing —
                # two bots raising the same pick at two books with neither
                # recording which); and settlement's closing-price lookup is
                # per-book, so with none it fell back to "any book" and computed
                # CLV against a price we never had. That last one matters most
                # here, because CLV is the metric these bots are judged on.
                [run_id, cohort, bot_id, r["mid"], r["market"], r["selection"],
                 # TRIGGER-CALIBRATOR-REVISION (2026-09-11): carry the
                 # window's stamped model_version onto the PICK. The window
                 # knew which calibrator shaped it; the pick did not, so
                 # nothing downstream could tell a pre-fix row (pooled
                 # calibration, HOME under-estimated 10-15pp, longshot-only
                 # fires) from a post-fix one. The 3-5 day CLV re-read that
                 # gates the whole convergence epic depends on being able to
                 # separate them — pooled, it would average a known-biased
                 # sample with a corrected one and report neither.
                 price, price, STAKE_EUR, r["cal"], r["cal"], edge, book,
                 r["mv"], (round(age_min, 1) if age_min is not None else None)],
            )
            counters["written"] += 1
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
FRESHNESS_MAX_AGE_MIN: dict[str, float] = {
    "sharp_1x2_tight": 60.0,
    "sharp_1x2": 60.0,
    "sharp_ou25": 60.0,
}


def is_fresh_enough(strategy: str, age_min: float | None) -> bool:
    """True unless `strategy` has a freshness ceiling AND the quote is older
    than it (or its age is unknown — unknown is stale for a gated strategy)."""
    cap = FRESHNESS_MAX_AGE_MIN.get(strategy)
    if cap is None:
        return True
    return age_min is not None and age_min <= cap


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
