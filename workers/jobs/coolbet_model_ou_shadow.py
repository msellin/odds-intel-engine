"""
COOLBET-MODEL-OU-SHADOW-BOT-2026-09-08 — mirror the calibrated model's
Over/Under picks into shadow_bets under bot_coolbet_ou_model_v1 so they place
through the PROVEN Coolbet UI placer with the validated per-market gates
(edge>=8% on calibrated_prob, odds>=1.80).

Why this bot exists (COOLBET-OWN-UNIFIED-FLOW-EPIC decision (a)): the strategy
backtest found the model-edge O/U instrument is +15% (fold-robust) at executable
prices where the line-shop bot's O/U is -17% (negative every month, n~1100 — a
live money leak). This job does NOT invent model logic: it COPIES qualifying
picks the calibrated model already produced.

Source: simulated_bets rows on the calibrated cohort (bots.maturity_label =
'calibrated'), market='o/u' (store_bet lowercases it), result='pending', future
kickoff, calibrated_prob NOT NULL, singles only, and the operator has not
already placed/skipped it. DISTINCT ON (match, line, side).

MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11) — what this job takes from the
pipeline, and what it does NOT. It used to select on the pipeline's stored
`edge_percent` and carry the pipeline's `odds_at_pick`. Both are the wrong basis
for a bot that bets at specific books:

  * the carried price is whichever book `recommended_bookmaker` happened to be,
    which is usually not a book we can bet. On the 1x2 side this dropped
    Nancy v Reims on Betano's 3.15 while Coolbet was live at 3.25 and clearing;
  * `edge_percent` is stored independently of price and probability and had
    drifted from its own row on 9 of 11 pending picks
    (EDGE-IS-DERIVED-NOT-STORED) — this bot carried a Clermont row priced 2.49
    with edge 0.0800, whose own UI then demanded 2.51.

So the ONLY thing inherited from the pipeline is `calibrated_prob`, the sole
model output. Price, edge and gate come from the books we actually bet, via the
router's `_latest_book_odds` (180-min freshness cap) and `decide_book` (edge per
book, best clearing price wins). `edge_percent` is DERIVED from the winning
price, so it can no longer disagree with `odds_at_pick` in the same row, and
`recommended_bookmaker` records which book qualified it — provenance only; the
placer re-decides on live odds at placement.

Vocabulary conversion — WRITE IN THE LINE-SHOP VOCABULARY so the new bot reuses
the line-shop O/U search/place path in coolbet_ui_placer:
    'o/u' + 'over 2.5'  -> market='over_under_25', selection='over'
    'o/u' + 'under 2.5' -> market='over_under_25', selection='under'
    'o/u' + 'over 3.5'  -> market='over_under_35', selection='over'
    'o/u' + 'under 3.5' -> market='over_under_35', selection='under'
Only lines 2.5 and 3.5 are supported — any other line (1.5, 4.5, ...) is skipped
(not rounded — rounding would fabricate a price and a settlement line).

Settlement: over_under_25/35 are STANDARD goals O/U markets. The generic shadow
settler (settlement.py _r_ou_goals, matched by the resolver registry's
"over_under" predicate) grades them from the goal score exactly as it grades the
line-shop bot's identical markets — the corners_ou_% skip in
_PENDING_SHADOW_BETS_SQL does NOT touch over_under_%. No settler branch here.

Real money is OFF BY DEFAULT: the UI placer only stakes this bot when its
coolbet_placer_bots row is toggled ui_place_enabled=true (superadmin, and only
for a bot in the code-level PLACEABLE_BOTS whitelist — COOLBET-PLACER-CONTROL).
This job only writes shadow_bets; it never places or touches a bankroll.

Idempotent: ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
updates the carried fields on re-run rather than duplicating. Run as:
    python3 -m workers.jobs.coolbet_model_ou_shadow
"""
from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)

BOT_NAME = "bot_coolbet_ou_model_v1"
SHADOW_COHORT = "coolbet_ou_model"
STAKE_EUR = 10.0
# Mirrors _MIN_EDGE_BY_MARKET['o/u'] in coolbet_placer.py (fraction, not pct).
# FLOORS-ONE-SOURCE (2026-09-11): DERIVE the default from the engine registry
# rather than re-typing it. This was `os.getenv(..., "0.08")` — a literal that
# only happened to equal _MIN_EDGE_BY_MARKET['o/u'], with nothing keeping the
# two in step. The env override stays for experiments; the DEFAULT is now the
# engine's own number.
#
# A null value in the registry means the market is RETIRED (btts and
# double_chance are None there, and _min_edge_for returns infinity so nothing
# clears). Fail loudly rather than silently substituting a number: this mirror
# selecting on a resurrected floor for a market we no longer bet is exactly the
# silent-wrong-floor failure this consolidation exists to remove.
from workers.automation.coolbet_placer import _MIN_EDGE_BY_MARKET  # noqa: E402

_OU_REGISTRY_FLOOR = _MIN_EDGE_BY_MARKET.get("o/u")
if _OU_REGISTRY_FLOOR is None:
    raise RuntimeError(
        "_MIN_EDGE_BY_MARKET['o/u'] is None — the o/u market is retired in the "
        "engine, so this paper mirror must not keep selecting picks for it. "
        "Retire the bot or restore the floor; do not hardcode one here."
    )
EDGE_FLOOR = float(os.getenv("COOLBET_MODEL_OU_EDGE_FLOOR",
                             str(_OU_REGISTRY_FLOOR)))

# MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11): this mirror previously applied NO
# odds floor at all — it carried whatever price the pipeline recommended and
# left the floor entirely to the placer. Now that it gates per placeable book it
# needs the floor here too, DERIVED from the same registry as everything else
# rather than re-typed (the o/u floor already had four copies once).
from workers.automation.coolbet_placer import _min_odds_for  # noqa: E402

MIN_ODDS = float(os.getenv("COOLBET_MODEL_OU_MIN_ODDS",
                           str(_min_odds_for("o/u"))))

# 'o/u' selection ("over 2.5" / "under 3.5") -> (over_under market, side).
# Only 2.5 and 3.5 are supported; every other line returns None and is skipped.
# CANONICAL-MARKET-VOCAB-2026-09-09: the O/U line encoding now lives in the shared
# workers.canonical_market.ou_selection_to_storage (COOLBET-PICK-TABLE-AUDIT Stage 1).


def _convert(market: str, selection: str) -> tuple[str, str] | None:
    """Return (over_under_market, side) for a supported O/U pick, else None.

    MARKET-VOCAB-CANONICAL: routes through the ONE normalizer so it accepts BOTH
    the legacy simulated_bets encoding ('o/u' + 'over 2.5') AND the canonical one
    ('over_under_25' + 'over') — behaviour-identical on legacy data, correct after
    the writer/backfill flip. Only the placeable 2.5 / 3.5 lines are supported.
    """
    from workers.canonical_market import normalize
    c = normalize(market, selection)
    if not c or c["family"] != "o/u" or c["market"] not in ("over_under_25", "over_under_35"):
        return None
    return c["market"], c["selection"]


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def generate_picks() -> dict:
    """Mirror qualifying calibrated model O/U picks into shadow_bets. One row per
    (match, over_under line, side) via ON CONFLICT upsert. Never raises."""
    counters = {"scanned": 0, "written": 0, "skipped_unsupported_line": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write

        bot_id = _bot_id()
        if not bot_id:
            log.warning("model-ou shadow: bot %s not registered (migration 309 unapplied?)", BOT_NAME)
            return counters

        # Highest-edge calibrated O/U pick per (match, selection). selection
        # carries the line ("over 2.5"), so (match_id, selection) is exactly
        # (match, line, side). Convert to the line-shop vocabulary below.
        rows = execute_query(
            """
            SELECT DISTINCT ON (sb.match_id, sb.market, sb.selection)
                   sb.match_id::text AS match_id,
                   sb.market,
                   sb.selection,
                   COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) AS odds_at_pick,
                   sb.calibrated_prob,
                   sb.model_probability,
                   sb.edge_percent
              FROM simulated_bets sb
              JOIN bots    b ON b.id = sb.bot_id
              JOIN matches m ON m.id = sb.match_id
             WHERE b.maturity_label = 'calibrated'
               -- MARKET-VOCAB-CANONICAL: accept BOTH legacy ('o/u') and canonical
               -- ('over_under_25'/'35') O/U spellings so this keeps feeding the
               -- real-money O/U bot across the migration. DISTINCT ON now includes
               -- market so the line is distinguished whether it lives in the
               -- market (canonical) or the selection (legacy).
               AND lower(sb.market) IN ('o/u', 'over_under_25', 'over_under_35')
               AND sb.result = 'pending'
               AND sb.combo_legs IS NULL
               AND sb.calibrated_prob IS NOT NULL
               -- MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11): the
               -- `sb.edge_percent >= ...` pre-filter that used to sit here is
               -- GONE. It tested the PIPELINE's stored edge, which belongs to
               -- whichever book `recommended_bookmaker` was -- not a book this
               -- bot can bet -- and had drifted from its own price on 9 of 11
               -- pending picks. On the 1x2 side that dropped Nancy v Reims on
               -- Betano's 3.15 while Coolbet was live at 3.25 and clearing.
               -- The only sound pre-filter is the NECESSARY condition: edge is
               -- cal_prob minus 1/odds, so it is always below cal_prob, and
               -- nothing can clear unless cal_prob exceeds the floor.
               -- (Keep literal per-cent signs OUT of this comment: psycopg2
               --  scans the whole query for parameter placeholders, SQL
               --  comments included, and a stray one raises IndexError before
               --  the database sees the statement. SQL-PERCENT-GUARD.)
               AND sb.calibrated_prob > %s
               AND sb.user_placed_at IS NULL
               AND sb.user_skipped_at IS NULL
               AND m.date > NOW()
             ORDER BY sb.match_id, sb.market, sb.selection, sb.edge_percent DESC
            """,
            [EDGE_FLOOR],
        )

        run_id = str(uuid.uuid4())
        for r in rows:
            conv = _convert(r["market"], r["selection"])
            if conv is None:
                counters["skipped_unsupported_line"] += 1
                continue
            market, side = conv
            counters["scanned"] += 1

            # ── MIRROR-PRICES-AT-ITS-OWN-BOOKS (2026-09-11) ──────────────
            # Identical treatment to the 1x2 mirror. Re-price the model's
            # probability against the books we can ACTUALLY BET and gate on that
            # edge, instead of copying `odds_at_pick` + `edge_percent` from
            # simulated_bets (a foreign book's price, plus an edge stored
            # independently of it and therefore free to drift).
            #
            # NB the lookup uses the CONVERTED market/selection: `_convert`
            # returns the canonical `over_under_25|35` + `over|under`, which is
            # the vocabulary `odds_snapshots` is keyed on. Looking up the legacy
            # 'o/u' + 'over 2.5' spelling would silently match nothing and drop
            # every pick.
            from workers.automation.best_price_router import (
                _latest_book_odds, decide_book,
            )
            cal_prob = float(r["calibrated_prob"] or 0)
            book_odds = _latest_book_odds(r["match_id"], market, side)
            if not book_odds:
                counters["no_book_price"] = counters.get("no_book_price", 0) + 1
                continue
            decision = decide_book(
                cal_prob, EDGE_FLOOR, MIN_ODDS,
                {b: v["odds"] for b, v in book_odds.items()},
                market=market, selection=side,
            )
            won_book = decision.get("winner")          # book NAME, e.g. 'Coolbet'
            if not won_book:
                counters["no_book_clears"] = counters.get("no_book_clears", 0) + 1
                continue
            price = float(decision["winner_odds"])
            # Derived from the winning price, never copied, so `edge_percent`
            # cannot disagree with `odds_at_pick` in the same row. That
            # contradiction was visible on this very bot: a Clermont row priced
            # 2.49 carrying edge 0.0800, whose own UI then demanded 2.51.
            edge_at_price = cal_prob - 1.0 / price

            # odds_at_pick == odds_at_pick_live: we already source the live
            # (executable) quote via the COALESCE above, so there is no
            # high-water inflation to distinguish. calibrated_prob is the value
            # the placer's live-edge gate reads.
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
                        model_probability = EXCLUDED.model_probability,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent,
                        recommended_bookmaker = EXCLUDED.recommended_bookmaker""",
                [run_id, SHADOW_COHORT, bot_id, r["match_id"], market, side,
                 price, price, STAKE_EUR,
                 r["model_probability"], r["calibrated_prob"], edge_at_price,
                 won_book],
            )
            counters["written"] += 1

        log.info("model-ou shadow: scanned %d supported picks, %d written/updated, "
                 "%d skipped (unsupported line)",
                 counters["scanned"], counters["written"],
                 counters["skipped_unsupported_line"])
    except Exception as e:
        log.warning("model-ou shadow generate_picks raised (non-fatal): %s", e)
    return counters


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(generate_picks(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
