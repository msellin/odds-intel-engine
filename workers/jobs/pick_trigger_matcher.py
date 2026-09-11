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

log = logging.getLogger(__name__)

STAKE_EUR = 10.0
# (book, odds-snapshot market, pick_triggers.strategy) → the paper bot its matches
# emit into. One bot per (book × market × anchor) so each combo's ROI is tracked
# and gated independently. The `strategy` key is what keeps the model anchor and
# the sharp (de-vigged Pinnacle) anchor in separate bots even though they share a
# fixture, market and Coolbet price — see docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.
BOOK_MARKET_BOTS = {
    ("Coolbet", "1x2",           "model_1x2"):  "bot_coolbet_trigger_1x2_v1",
    ("Coolbet", "over_under_25", "model_ou25"): "bot_coolbet_trigger_ou_v1",
    ("Coolbet", "1x2",           "sharp_1x2"):  "bot_coolbet_trigger_sharp_1x2_v1",
    ("Coolbet", "over_under_25", "sharp_ou25"): "bot_coolbet_trigger_sharp_ou_v1",
    # UNIBET-TRIGGER-BOTS-2026-09-09 (Stage 3b): same trigger engine, second book.
    # Reads odds_snapshots bookmaker='Unibet-Site' (the broad site sweep). Paper.
    # NB the DRAW edge — which the model can't see — lives here on the SHARP anchor
    # (soft-book mispricing vs de-vig Pinnacle); see ANALYSIS_GOTCHAS §57.
    ("Unibet-Site", "1x2",           "model_1x2"):  "bot_unibet_trigger_1x2_v1",
    ("Unibet-Site", "over_under_25", "model_ou25"): "bot_unibet_trigger_ou_v1",
    ("Unibet-Site", "1x2",           "sharp_1x2"):  "bot_unibet_trigger_sharp_1x2_v1",
    ("Unibet-Site", "over_under_25", "sharp_ou25"): "bot_unibet_trigger_sharp_ou_v1",
}


def _cohort_for(book: str) -> str:
    """shadow_cohort per book so each book's trigger picks group + settle separately."""
    return "unibet_trigger" if book.lower().startswith("unibet") else "coolbet_trigger"


def _bot_id(name: str) -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [name])
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
            log.warning("trigger matcher: bot %s not registered (migration 321?)", bot_name)
            return counters
        cohort = _cohort_for(book)

        rows = execute_query(
            """
            WITH latest AS (  -- latest pre-match price per (match, selection) for this book+market
              SELECT DISTINCT ON (o.match_id, o.selection)
                     o.match_id::text AS mid, o.selection, o.odds::float AS odds
                FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
               WHERE o.bookmaker = %s AND o.market = %s
                 AND o.timestamp <= m.date AND m.date > NOW()
               ORDER BY o.match_id, o.selection, o.timestamp DESC
            )
            SELECT t.match_id::text AS mid, t.market, t.selection,
                   t.cal_prob::float AS cal, l.odds AS book_odds
              FROM pick_triggers t
              JOIN latest l ON l.mid = t.match_id::text AND l.selection = t.selection
             WHERE t.market = %s AND t.strategy = %s AND t.kickoff_at > NOW()
               AND l.odds >= t.min_odds
               AND (t.max_odds IS NULL OR l.odds <= t.max_odds)
            """,
            [book, market, market, strategy],
        )
        counters["matched"] = len(rows)
        run_id = str(uuid.uuid4())
        for r in rows:
            price = float(r["book_odds"])
            if price <= 1.0:
                continue
            edge = float(r["cal"]) - 1.0 / price   # edge at the book's OWN price
            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent,
                        recommended_bookmaker)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick      = EXCLUDED.odds_at_pick,
                        odds_at_pick_live = EXCLUDED.odds_at_pick_live,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent,
                        recommended_bookmaker = EXCLUDED.recommended_bookmaker""",
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
                 price, price, STAKE_EUR, r["cal"], r["cal"], edge, book],
            )
            counters["written"] += 1
        log.info("trigger matcher (%s/%s/%s): %s", book, market, strategy, counters)
    except Exception as e:  # noqa: BLE001
        log.warning("trigger matcher raised (non-fatal): %s", e)
    return counters


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
