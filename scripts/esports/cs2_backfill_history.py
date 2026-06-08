#!/usr/bin/env python3
"""
Backfill cs2_predictions + cs2_results from the historical CSV.

Walks 9,200 series chronologically. For each match:
- BEFORE updating ELO, computes predicted_prob using only data from prior matches
  (walk-forward; no lookahead leakage).
- Writes to cs2_predictions(model_version='elo_v1_backfill', bo3gg_id=-match_id).
- Writes actual outcome to cs2_results.

Idempotent via ON CONFLICT. Run once after applying migrations 199/200.

Negative bo3gg_id is used to keep historical rows disjoint from live bo3.gg IDs.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.esports.cs2_elo_scanner import (
    INITIAL_ELO, K_BASE, BO_WEIGHTS,
    elo_expected, fair_odds, tournament_tier,
    PRIMARY_CSV,
)
from workers.api_clients.db import execute_write, execute_query

BACKFILL_MODEL_VERSION = "elo_v1_backfill"


def _bo_weight(bo: int) -> float:
    return BO_WEIGHTS.get(bo, 0.85)


def _load_rows() -> list[dict]:
    """Load match_id + match info, sorted chronologically."""
    rows: list[dict] = []
    with open(PRIMARY_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("is_total") not in ("1", "1.0", "True", "true"):
                continue
            try:
                mid = int(r["match_id"])
                bo = int(r.get("bestOf") or 3)
                if bo not in (1, 3, 5):
                    continue
                dt = datetime.fromisoformat(r["datetime"].replace(" ", "T"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                tw = r.get("team1_win", "")
                if tw in ("1", "1.0", "True", "true"):
                    result = 1
                elif tw in ("0", "0.0", "False", "false"):
                    result = 0
                else:
                    continue
                s1 = int(float(r.get("score1_match") or 0))
                s2 = int(float(r.get("score2_match") or 0))
            except (ValueError, KeyError, TypeError):
                continue

            rows.append({
                "match_id": mid,
                "date": dt,
                "team1": r["team1"].strip(),
                "team2": r["team2"].strip(),
                "result": result,
                "score1": s1,
                "score2": s2,
                "best_of": bo,
                "tournament": (r.get("tournament") or "").strip(),
            })

    rows.sort(key=lambda x: x["date"])
    return rows


def _exists(table: str) -> bool:
    out = execute_query(
        "SELECT to_regclass(%s) AS r", (table,),
    )
    return bool(out and out[0].get("r"))


def backfill(limit: int | None = None, batch_size: int = 500) -> None:
    if not (_exists("cs2_predictions") and _exists("cs2_results")):
        print("[!] cs2_predictions/cs2_results not found — push migrations first", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== CS2 BACKFILL  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  Loading {PRIMARY_CSV.name}...")
    matches = _load_rows()
    if limit:
        matches = matches[:limit]
    print(f"  {len(matches):,} matches to backfill")

    ratings: dict[str, float] = {}
    pred_written = 0
    res_written = 0

    for i, m in enumerate(matches):
        t1, t2 = m["team1"], m["team2"]
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)

        # Walk-forward prediction: ELO state BEFORE this match
        prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1

        bo3gg_id = -m["match_id"]  # negative to avoid collision with live IDs

        execute_write("""
            INSERT INTO cs2_predictions
                (bo3gg_id, scan_time, kickoff_time, league, best_of,
                 team1, team2, elo1, elo2, pq1, pq2,
                 win_prob1, win_prob2, fair_odds1, fair_odds2,
                 bookie_odds1, bookie_odds2,
                 roster_change1, roster_change2, model_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL,
                    %s, %s, %s, %s, NULL, NULL, FALSE, FALSE, %s)
            ON CONFLICT (bo3gg_id, scan_time) DO NOTHING
        """, (
            bo3gg_id,
            m["date"].isoformat(),                # scan_time = match date for backfill
            m["date"].isoformat(),
            m["tournament"], m["best_of"], t1, t2,
            round(r1, 1), round(r2, 1),
            round(prob1, 4), round(prob2, 4),
            round(fair_odds(prob1), 3), round(fair_odds(prob2), 3),
            BACKFILL_MODEL_VERSION,
        ))
        pred_written += 1

        execute_write("""
            INSERT INTO cs2_results
                (bo3gg_id, team1, team2, kickoff_time, best_of, winner, score1, score2, raw_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'backfill')
            ON CONFLICT (bo3gg_id) DO NOTHING
        """, (
            bo3gg_id, t1, t2, m["date"].isoformat(), m["best_of"],
            "team1" if m["result"] == 1 else "team2",
            m["score1"], m["score2"],
        ))
        res_written += 1

        # Update ELO with the match outcome (state for the next iteration)
        k = K_BASE * tournament_tier(m["tournament"]) * _bo_weight(m["best_of"])
        ratings[t1] = r1 + k * (m["result"] - prob1)
        ratings[t2] = r2 + k * ((1 - m["result"]) - prob2)

        if (i + 1) % batch_size == 0:
            print(f"  [{i+1:>5}/{len(matches)}] preds={pred_written}  results={res_written}")

    print(f"\n  ✓ Done. {pred_written:,} predictions, {res_written:,} results inserted (or skipped if existed).\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="Only backfill first N matches (debug)")
    args = p.parse_args()
    backfill(limit=args.limit)


if __name__ == "__main__":
    main()
