#!/usr/bin/env python3
"""
Backfill cs2_predictions + cs2_results from the historical CSV.

Walks 9,200 series chronologically. For each match:
- BEFORE updating ELO, computes predicted_prob using only data from prior matches
  (walk-forward; no lookahead leakage).
- Writes to cs2_predictions(model_version=..., bo3gg_id=-match_id).
- Writes actual outcome to cs2_results.

Two model variants:
  --model elo            ELO-only (model_version="elo_v1_backfill_v2")
  --model elo+pq         ELO+player-quality combined logistic (model_version="elo+pq_v1_backfill")

For elo+pq, time-aware PQ is sourced from cs2_newestcombinedmatches.csv —
each team's PQ at match T = avg HLTV rating of their lineup in the most
recent match STRICTLY BEFORE T (so no lookahead).

Idempotent via ON CONFLICT.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.esports.cs2_elo_scanner import (
    INITIAL_ELO, K_BASE, BO_WEIGHTS,
    elo_expected, combined_win_prob, fair_odds, tournament_tier,
    PRIMARY_CSV, PLAYER_RATING_CSV,
)
from workers.api_clients.db import execute_write, execute_query

MODEL_LABELS = {
    "elo":    "elo_v1_backfill_v2",
    "elo+pq": "elo+pq_v1_backfill",
}


def _bo_weight(bo: int) -> float:
    return BO_WEIGHTS.get(bo, 0.85)


def _load_rows() -> list[dict]:
    """Load match_id + match info, sorted chronologically.

    Winner derived from score1_match vs score2_match — `team1_win` in this CSV
    is unreliable on is_total=True rows (97.9% zeros despite slot-1 winning 55%).
    """
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
                s1 = int(float(r.get("score1_match") or 0))
                s2 = int(float(r.get("score2_match") or 0))
            except (ValueError, KeyError, TypeError):
                continue

            if s1 == s2:
                continue  # data anomaly — no draws in CS
            result = 1 if s1 > s2 else 0

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


def _load_pq_events() -> list[tuple[datetime, str, float]]:
    """Build sorted (date, team_name_lower, avg_rating) events from the player CSV.

    avg_rating is mean of team_{slot}_player_{1..5}_RATING for that match.
    These ratings are post-game stats, so they must be applied STRICTLY AFTER
    their match date during walk-forward backfill (use < not <=).
    """
    events: list[tuple[datetime, str, float]] = []
    if not PLAYER_RATING_CSV.exists():
        return events

    with open(PLAYER_RATING_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            for slot in ("team1", "team2"):
                name = (r.get(f"{slot}_name") or "").strip()
                if not name:
                    continue
                ratings = []
                for i in range(1, 6):
                    val = r.get(f"{slot}_player_{i}_RATING", "")
                    try:
                        ratings.append(float(val))
                    except (ValueError, TypeError):
                        pass
                if len(ratings) >= 3:
                    events.append((dt, name.lower(), sum(ratings) / len(ratings)))
    events.sort(key=lambda x: x[0])
    return events


def _exists(table: str) -> bool:
    out = execute_query("SELECT to_regclass(%s) AS r", (table,))
    return bool(out and out[0].get("r"))


def backfill(model: str, limit: int | None = None, batch_size: int = 500) -> None:
    if not (_exists("cs2_predictions") and _exists("cs2_results")):
        print("[!] cs2_predictions/cs2_results not found — push migrations first", file=sys.stderr)
        sys.exit(1)

    if model not in MODEL_LABELS:
        print(f"[!] unknown --model {model!r}, expected one of {list(MODEL_LABELS)}", file=sys.stderr)
        sys.exit(2)

    model_version = MODEL_LABELS[model]
    use_pq = (model == "elo+pq")

    print(f"\n=== CS2 BACKFILL  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  model = {model_version}   (PQ {'enabled' if use_pq else 'disabled'})")
    print(f"  Loading {PRIMARY_CSV.name}...")
    matches = _load_rows()
    if limit:
        matches = matches[:limit]
    print(f"  {len(matches):,} matches to backfill")

    pq_events: list[tuple[datetime, str, float]] = []
    if use_pq:
        print(f"  Loading PQ events from {PLAYER_RATING_CSV.name}...")
        pq_events = _load_pq_events()
        print(f"  {len(pq_events):,} PQ events")

    ratings: dict[str, float] = {}
    team_pq: dict[str, float] = {}
    pq_idx = 0
    pred_written = 0
    res_written = 0
    pq_hits = 0  # how many matches had PQ for both teams

    for i, m in enumerate(matches):
        # Advance PQ pointer to apply all events STRICTLY BEFORE this match's date.
        if use_pq:
            while pq_idx < len(pq_events) and pq_events[pq_idx][0] < m["date"]:
                _, name_lower, pq_val = pq_events[pq_idx]
                team_pq[name_lower] = pq_val
                pq_idx += 1

        t1, t2 = m["team1"], m["team2"]
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)

        pq1 = team_pq.get(t1.lower()) if use_pq else None
        pq2 = team_pq.get(t2.lower()) if use_pq else None
        pq_diff = (pq1 - pq2) if (pq1 is not None and pq2 is not None) else None
        if use_pq and pq_diff is not None:
            pq_hits += 1

        # Walk-forward prediction
        if use_pq:
            prob1 = combined_win_prob(r1, r2, pq_diff)
        else:
            prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1

        bo3gg_id = -m["match_id"]

        execute_write("""
            INSERT INTO cs2_predictions
                (bo3gg_id, scan_time, kickoff_time, league, best_of,
                 team1, team2, elo1, elo2, pq1, pq2,
                 win_prob1, win_prob2, fair_odds1, fair_odds2,
                 bookie_odds1, bookie_odds2,
                 roster_change1, roster_change2, model_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, NULL, NULL, FALSE, FALSE, %s)
            ON CONFLICT (bo3gg_id, scan_time) DO NOTHING
        """, (
            bo3gg_id,
            m["date"].isoformat(),
            m["date"].isoformat(),
            m["tournament"], m["best_of"], t1, t2,
            round(r1, 1), round(r2, 1),
            pq1, pq2,
            round(prob1, 4), round(prob2, 4),
            round(fair_odds(prob1), 3), round(fair_odds(prob2), 3),
            model_version,
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

        # Update ELO with the actual outcome (state for the next iteration)
        # Note: we update ELO using the SAME prob1 we predicted with, so the
        # update is self-consistent (and Platt-corrected calibration applies).
        k = K_BASE * tournament_tier(m["tournament"]) * _bo_weight(m["best_of"])
        e1 = elo_expected(r1, r2)  # ELO update uses pure ELO expectation
        ratings[t1] = r1 + k * (m["result"] - e1)
        ratings[t2] = r2 + k * ((1 - m["result"]) - (1 - e1))

        if (i + 1) % batch_size == 0:
            extra = f"  pq_hits={pq_hits}" if use_pq else ""
            print(f"  [{i+1:>5}/{len(matches)}] preds={pred_written}  results={res_written}{extra}")

    extra = f"  PQ hits: {pq_hits:,} / {len(matches):,} matches" if use_pq else ""
    print(f"\n  ✓ Done. {pred_written:,} predictions, {res_written:,} results inserted (or skipped if existed).{extra}\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=list(MODEL_LABELS), default="elo")
    p.add_argument("--limit", type=int, help="Only backfill first N matches (debug)")
    args = p.parse_args()
    backfill(model=args.model, limit=args.limit)


if __name__ == "__main__":
    main()
