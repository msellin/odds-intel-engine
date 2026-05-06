"""
OddsIntel — Calibration Diagnostic (CAL-DIAG-1)

Runs 3 targeted queries on settled 1X2 home bets to identify the root cause
of the 42% predicted vs 26% actual win rate failure observed 2026-05-06.

Queries:
  1. Ensemble decomposition: avg model_probability vs calibrated vs market implied
     → tells you if Platt is inflating or if the base model is already overconfident
  2. Sharp signal check: avg sharp_consensus, Pinnacle implied, soft implied
     → tells you if the losses were betting against sharp money
  3. Threshold count: how many losing home bets had negative sharp_consensus
     → validates the CAL-SHARP-GATE -0.02 threshold

Run:
    python3 scripts/run_cal_diag.py
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query


def run():
    print("\n=== CAL-DIAG-1: Calibration Failure Diagnostic ===\n")

    # -------------------------------------------------------------------------
    # Query 1: Ensemble decomposition for settled 1X2 home bets
    # -------------------------------------------------------------------------
    print("── Query 1: Ensemble decomposition (settled 1X2 home) ──")
    rows = execute_query(
        """
        SELECT
            COUNT(*)                                        AS n_bets,
            ROUND(AVG(model_probability)::numeric, 4)       AS avg_model_prob,
            ROUND(AVG(calibrated_prob)::numeric, 4)         AS avg_calibrated_prob,
            ROUND(AVG(1.0 / odds_at_pick)::numeric, 4)      AS avg_market_implied,
            ROUND(
                (SUM(CASE WHEN result = 'won' THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*),0))::numeric,
                4
            )                                               AS actual_win_rate
        FROM simulated_bets
        WHERE market = '1X2'
          AND selection = 'home'
          AND result != 'pending'
        """,
        [],
    )
    if rows:
        r = rows[0]
        print(f"  n_bets:              {r['n_bets']}")
        print(f"  avg_model_prob:      {r['avg_model_prob']}  (pre-Platt)")
        print(f"  avg_calibrated_prob: {r['avg_calibrated_prob']}  (post-Platt)")
        print(f"  avg_market_implied:  {r['avg_market_implied']}  (1/odds)")
        print(f"  actual_win_rate:     {r['actual_win_rate']}")
        if r['avg_model_prob'] and r['avg_calibrated_prob']:
            delta = float(r['avg_calibrated_prob']) - float(r['avg_model_prob'])
            print(f"  platt_delta:         {delta:+.4f}  (+ means Platt inflated; - means it corrected down)")
    else:
        print("  No settled 1X2 home bets found.")

    # -------------------------------------------------------------------------
    # Query 2: Sharp signal direction
    # -------------------------------------------------------------------------
    print("\n── Query 2: Sharp signal direction (settled 1X2 home) ──")
    rows2 = execute_query(
        """
        SELECT
            COUNT(*)                                           AS n_bets,
            ROUND(AVG(ms_sc.signal_value)::numeric, 4)        AS avg_sharp_consensus,
            ROUND(AVG(ms_pin.signal_value)::numeric, 4)       AS avg_pinnacle_implied,
            ROUND(AVG(1.0 / sb.odds_at_pick)::numeric, 4)     AS avg_soft_implied,
            ROUND(
                (SUM(CASE WHEN sb.result = 'won' THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*),0))::numeric,
                4
            )                                                  AS actual_win_rate
        FROM simulated_bets sb
        LEFT JOIN LATERAL (
            SELECT signal_value FROM match_signals
            WHERE match_id = sb.match_id AND signal_name = 'sharp_consensus_home'
            ORDER BY captured_at DESC LIMIT 1
        ) ms_sc ON true
        LEFT JOIN LATERAL (
            SELECT signal_value FROM match_signals
            WHERE match_id = sb.match_id AND signal_name = 'pinnacle_implied_home'
            ORDER BY captured_at DESC LIMIT 1
        ) ms_pin ON true
        WHERE sb.market = '1X2'
          AND sb.selection = 'home'
          AND sb.result != 'pending'
        """,
        [],
    )
    if rows2:
        r = rows2[0]
        print(f"  n_bets:               {r['n_bets']}")
        print(f"  avg_sharp_consensus:  {r['avg_sharp_consensus']}  (neg = sharps say home less likely than softs)")
        print(f"  avg_pinnacle_implied: {r['avg_pinnacle_implied']}")
        print(f"  avg_soft_implied:     {r['avg_soft_implied']}  (1/odds)")
        print(f"  actual_win_rate:      {r['actual_win_rate']}")

    # -------------------------------------------------------------------------
    # Query 3: CAL-SHARP-GATE coverage
    # -------------------------------------------------------------------------
    print("\n── Query 3: Sharp gate coverage (sharp_consensus < -0.02 threshold) ──")
    rows3 = execute_query(
        """
        SELECT
            COUNT(*) FILTER (WHERE sb.result = 'lost')                              AS total_losses,
            COUNT(*) FILTER (WHERE sb.result = 'won')                               AS total_wins,
            COUNT(*) FILTER (WHERE sb.result = 'lost' AND ms_sc.signal_value < -0.02)  AS losses_caught,
            COUNT(*) FILTER (WHERE sb.result = 'won'  AND ms_sc.signal_value < -0.02)  AS wins_filtered,
            COUNT(*) FILTER (WHERE ms_sc.signal_value IS NULL)                      AS missing_signal
        FROM simulated_bets sb
        LEFT JOIN LATERAL (
            SELECT signal_value FROM match_signals
            WHERE match_id = sb.match_id AND signal_name = 'sharp_consensus_home'
            ORDER BY captured_at DESC LIMIT 1
        ) ms_sc ON true
        WHERE sb.market = '1X2'
          AND sb.selection = 'home'
          AND sb.result != 'pending'
        """,
        [],
    )
    if rows3:
        r = rows3[0]
        total_l = r['total_losses'] or 0
        total_w = r['total_wins'] or 0
        caught = r['losses_caught'] or 0
        filtered = r['wins_filtered'] or 0
        pct_l = f"{caught/total_l*100:.1f}%" if total_l else "n/a"
        pct_w = f"{filtered/total_w*100:.1f}%" if total_w else "n/a"
        print(f"  total_losses:   {total_l}")
        print(f"  total_wins:     {total_w}")
        print(f"  losses_caught:  {caught}  ({pct_l} of losses would be blocked)")
        print(f"  wins_filtered:  {filtered}  ({pct_w} of wins would be lost)")
        print(f"  missing_signal: {r['missing_signal']}  (signal not stored for these bets — gate won't fire)")

    print("\n=== Done ===\n")


if __name__ == "__main__":
    run()
