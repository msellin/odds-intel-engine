#!/usr/bin/env python3
"""
HLTV-points-only prediction model — parallel test against ELO+PQ.

For every upcoming match where BOTH teams are in HLTV's top-248, derives a
win probability from the HLTV point ratio with a sigmoid scaling, and writes
a prediction row tagged model_version='hltv_v1'.

Goal: run this alongside the production elo+pq_v1 scanner so we can
compare which model has lower log_loss / better ECE / higher accuracy once
enough matches settle.

Formula:
    point_diff = log(points1 + 1) - log(points2 + 1)
    p1 = sigmoid(K * point_diff)

K is fitted such that two equally-sharp formulas (HLTV ranks #1 vs #15) give
roughly the same gap our ELO gives — calibrated against today's snapshot.

Usage:
    python3 scripts/esports/cs2_hltv_predict.py            # dry run
    python3 scripts/esports/cs2_hltv_predict.py --record   # write cs2_predictions
"""
import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write

MODEL_VERSION = "hltv_v1"
# Scaling constant. Calibrated such that #1 (991) vs #50 (~25) gives ~75% win prob.
HLTV_K = 0.55


def _hltv_prob(points1: int, points2: int) -> float:
    """Sigmoid on log-ratio of HLTV points. Returns prob team1 wins."""
    log_diff = math.log(points1 + 1) - math.log(points2 + 1)
    return 1.0 / (1.0 + math.exp(-HLTV_K * log_diff))


def _load_upcoming() -> list[dict]:
    """Match rows that have HLTV data for BOTH teams."""
    now = datetime.now(timezone.utc)
    return execute_query("""
        SELECT bo3gg_id, kickoff_time, league, best_of, team1, team2,
               hltv_rank1, hltv_rank2, hltv_points1, hltv_points2
        FROM cs2_upcoming_matches
        WHERE kickoff_time >= %s
          AND bo3gg_id IS NOT NULL
          AND hltv_points1 IS NOT NULL
          AND hltv_points2 IS NOT NULL
        ORDER BY kickoff_time
    """, (now.isoformat(),))


def _fair_odds(p: float) -> float:
    return round(1.0 / p, 3) if p > 0 else 999.99


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write rows to cs2_predictions")
    args = p.parse_args()

    print(f"\n=== CS2 HLTV-ONLY PREDICT  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    rows = _load_upcoming()
    print(f"  {len(rows)} upcoming matches with HLTV data on both teams\n")

    now_iso = datetime.now(timezone.utc).isoformat()
    written = 0

    for r in rows:
        p1 = _hltv_prob(r["hltv_points1"], r["hltv_points2"])
        p2 = 1.0 - p1
        f1, f2 = _fair_odds(p1), _fair_odds(p2)
        tag = "  fired" if args.record else "  dry"
        print(f"  {tag}  {r['team1'][:22]:22} (#{r['hltv_rank1']:>3}, {r['hltv_points1']:>4}p) vs "
              f"{r['team2'][:22]:22} (#{r['hltv_rank2']:>3}, {r['hltv_points2']:>4}p)  "
              f"→ p1={p1*100:5.1f}%  fair={f1:.2f}/{f2:.2f}")

        if args.record:
            execute_write("""
                INSERT INTO cs2_predictions
                    (bo3gg_id, scan_time, kickoff_time, league, best_of,
                     team1, team2, win_prob1, win_prob2, fair_odds1, fair_odds2,
                     hltv_rank1, hltv_rank2, hltv_points1, hltv_points2,
                     model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bo3gg_id, scan_time, model_version) DO NOTHING
            """, (
                r["bo3gg_id"], now_iso, r["kickoff_time"], r["league"], r["best_of"],
                r["team1"], r["team2"],
                round(p1, 4), round(p2, 4), f1, f2,
                r["hltv_rank1"], r["hltv_rank2"], r["hltv_points1"], r["hltv_points2"],
                MODEL_VERSION,
            ))
            written += 1

    print(f"\n  wrote {written} predictions (model_version={MODEL_VERSION})\n")


if __name__ == "__main__":
    main()
