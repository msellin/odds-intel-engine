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
kickoff, edge_percent >= 0.08 (edge is stored as a FRACTION — cal_prob - implied
— NOT a percentage; the placer's _MIN_EDGE_BY_MARKET['o/u']=0.08 compares to it
directly), calibrated_prob NOT NULL, singles only, and the operator has not
already placed/skipped it. DISTINCT ON (match, line, side) taking the highest
edge_percent.

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
EDGE_FLOOR = float(os.getenv("COOLBET_MODEL_OU_EDGE_FLOOR", "0.08"))

# 'o/u' selection ("over 2.5" / "under 3.5") -> (over_under market, side).
# Only 2.5 and 3.5 are supported; every other line returns None and is skipped.
# CANONICAL-MARKET-VOCAB-2026-09-09: the O/U line encoding now lives in the shared
# workers.canonical_market.ou_selection_to_storage (COOLBET-PICK-TABLE-AUDIT Stage 1).


def _convert(selection: str) -> tuple[str, str] | None:
    """'over 2.5' -> ('over_under_25', 'over'); unsupported line -> None."""
    from workers.canonical_market import ou_selection_to_storage
    return ou_selection_to_storage(selection)


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
            SELECT DISTINCT ON (sb.match_id, sb.selection)
                   sb.match_id::text AS match_id,
                   sb.selection,
                   COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) AS odds_at_pick,
                   sb.calibrated_prob,
                   sb.model_probability,
                   sb.edge_percent
              FROM simulated_bets sb
              JOIN bots    b ON b.id = sb.bot_id
              JOIN matches m ON m.id = sb.match_id
             WHERE b.maturity_label = 'calibrated'
               AND sb.market = 'o/u'
               AND sb.result = 'pending'
               AND sb.combo_legs IS NULL
               AND sb.calibrated_prob IS NOT NULL
               AND sb.edge_percent >= %s
               AND sb.user_placed_at IS NULL
               AND sb.user_skipped_at IS NULL
               AND m.date > NOW()
             ORDER BY sb.match_id, sb.selection, sb.edge_percent DESC
            """,
            [EDGE_FLOOR],
        )

        run_id = str(uuid.uuid4())
        for r in rows:
            conv = _convert(r["selection"])
            if conv is None:
                counters["skipped_unsupported_line"] += 1
                continue
            market, side = conv
            counters["scanned"] += 1

            price = r["odds_at_pick"]
            if price is None or float(price) <= 1.0:
                # No usable executable price to carry — skip rather than write a
                # pick the placer's drift check cannot anchor.
                continue

            # odds_at_pick == odds_at_pick_live: we already source the live
            # (executable) quote via the COALESCE above, so there is no
            # high-water inflation to distinguish. calibrated_prob is the value
            # the placer's live-edge gate reads.
            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick      = EXCLUDED.odds_at_pick,
                        odds_at_pick_live = EXCLUDED.odds_at_pick_live,
                        model_probability = EXCLUDED.model_probability,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent""",
                [run_id, SHADOW_COHORT, bot_id, r["match_id"], market, side,
                 price, price, STAKE_EUR,
                 r["model_probability"], r["calibrated_prob"], r["edge_percent"]],
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
