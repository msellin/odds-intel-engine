"""
OU35-MODEL-SHADOW-BOT-2026-09-08 — write Over/Under 3.5 model-edge picks into
shadow_bets under bot_ou35_model_v1, so the line OU-LINES-EDGE-TEST flagged (O/U
3.5 mirrors the live 2.5: +7.8% Coolbet-executable calibrated model-edge, not yet
fold-robust) accrues a FORWARD paper track record.

UNLIKE the 2.5 / 1x2 mirrors, this bot does NOT copy from simulated_bets — the
calibrated cohort never bets 3.5. It generates directly:
  1. fit isotonic calibration on settled `over35` predictions (the model pipeline
     does not calibrate 3.5) — X = predictions.model_probability, y = total>3.5;
  2. for each UPCOMING match with a Coolbet over_under_35 price, compute the
     calibrated edge per side (cal - 1/coolbet_price) — SINGLE-BOOK executable
     price, never best-of-books (§55: best-of-books + a gate is the line-shop
     selection artifact that shows even the profitable 2.5 as negative);
  3. write the qualifying side (edge >= EDGE_FLOOR) to shadow_bets.

PAPER ONLY: not in the placer's PLACEABLE_BOTS whitelist and no coolbet_placer_bots
row — it can never stake money. It is a 👥 PICKS / 🤖 OWN promotion candidate once
fold-robust (owner-gated). Settled by the generic goals-O/U resolver.

Idempotent: ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection).
Run: python3 -m workers.jobs.ou35_model_shadow
"""
from __future__ import annotations

import logging
import os
import uuid

log = logging.getLogger(__name__)

BOT_NAME = "bot_ou35_model_v1"
SHADOW_COHORT = "ou35_model"
MARKET = "over_under_35"
STAKE_EUR = 10.0
# The validated O/U gate (mirrors _MIN_EDGE_BY_MARKET['o/u']); fraction, not pct.
EDGE_FLOOR = float(os.getenv("OU35_MODEL_EDGE_FLOOR", "0.08"))


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def _fit_calibrator():
    """Isotonic P(over 3.5) from raw over35 model prob, fit on settled matches.
    Returns a callable prob->cal_prob, or None if too little history."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT po.model_probability::float AS p,
               ((m.score_home + m.score_away) > 3.5)::int AS y
          FROM matches m
          JOIN LATERAL (SELECT model_probability FROM predictions
                        WHERE match_id = m.id AND market = 'over35'
                        ORDER BY model_version DESC LIMIT 1) po ON true
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
        """
    )
    if len(rows) < 500:
        return None
    try:
        from sklearn.isotonic import IsotonicRegression
    except Exception as e:  # noqa: BLE001
        log.warning("ou35 shadow: sklearn unavailable (%s) — cannot calibrate", e)
        return None
    xs = [r["p"] for r in rows]
    ys = [r["y"] for r in rows]
    iso = IsotonicRegression(out_of_bounds="clip").fit(xs, ys)
    return lambda p: float(iso.predict([p])[0])


def generate_picks() -> dict:
    """Generate qualifying O/U 3.5 model-edge picks into shadow_bets. Never raises."""
    counters = {"scanned": 0, "written": 0, "no_calibrator": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write

        bot_id = _bot_id()
        if not bot_id:
            log.warning("ou35 shadow: bot %s not registered (migration 316 unapplied?)", BOT_NAME)
            return counters

        cal = _fit_calibrator()
        if cal is None:
            counters["no_calibrator"] = 1
            log.warning("ou35 shadow: insufficient settled over35 history to calibrate — skipping run")
            return counters

        # upcoming matches with a Coolbet over_under_35 over+under price + an over35 pred
        rows = execute_query(
            """
            WITH cb AS (
              SELECT DISTINCT ON (o.match_id, o.selection)
                     o.match_id::text AS mid, o.selection, o.odds::float AS odds
                FROM odds_snapshots o
                JOIN matches m ON m.id = o.match_id
               WHERE o.market = 'over_under_35' AND o.bookmaker = 'Coolbet'
                 AND o.selection IN ('over','under') AND m.date > NOW()
               ORDER BY o.match_id, o.selection, o.timestamp DESC
            )
            SELECT m.id::text AS mid,
                   MAX(cb.odds) FILTER (WHERE cb.selection='over')  AS o_over,
                   MAX(cb.odds) FILTER (WHERE cb.selection='under') AS o_under,
                   po.model_probability::float AS p_over
              FROM matches m
              JOIN cb ON cb.mid = m.id::text
              JOIN LATERAL (SELECT model_probability FROM predictions
                            WHERE match_id = m.id AND market = 'over35'
                            ORDER BY model_version DESC LIMIT 1) po ON true
             WHERE m.date > NOW()
             GROUP BY m.id, po.model_probability
            """
        )
        run_id = str(uuid.uuid4())
        for r in rows:
            if r["o_over"] is None or r["o_under"] is None or r["p_over"] is None:
                continue
            counters["scanned"] += 1
            cal_over = cal(float(r["p_over"]))
            cal_under = 1.0 - cal_over
            edge_over = cal_over - 1.0 / float(r["o_over"])
            edge_under = cal_under - 1.0 / float(r["o_under"])
            # pick the higher-edge side if it clears the floor
            if edge_over >= edge_under and edge_over >= EDGE_FLOOR:
                sel, price, calp, edge = "over", float(r["o_over"]), cal_over, edge_over
            elif edge_under >= EDGE_FLOOR:
                sel, price, calp, edge = "under", float(r["o_under"]), cal_under, edge_under
            else:
                continue
            if price <= 1.0:
                continue
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
                [run_id, SHADOW_COHORT, bot_id, r["mid"], MARKET, sel,
                 price, price, STAKE_EUR, float(r["p_over"]), calp, edge],
            )
            counters["written"] += 1

        log.info("ou35 shadow: scanned %d, wrote %d", counters["scanned"], counters["written"])
    except Exception as e:  # noqa: BLE001
        log.warning("ou35 shadow generate_picks raised (non-fatal): %s", e)
    return counters


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(generate_picks(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
