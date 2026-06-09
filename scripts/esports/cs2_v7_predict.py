"""
CS2 v7 production scorer.

Reads upcoming matches that have an hltv_v1 prediction, computes v7
stacking features (form, h2h, rest, rank, tm, bo, pistol, tier),
applies the trained v7 coefficients from cs2_model_coefficients, and
writes one row to cs2_predictions with model_version='v7'. Also UPSERTs
into cs2_upcoming_matches so the admin UI + bot can read v7 fair odds
directly.

Run:
    python3 scripts/esports/cs2_v7_predict.py [--record]
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402


MODEL_VERSION = "v7"


def load_coefs() -> tuple[float, dict, list[str]]:
    rows = execute_query(
        "SELECT intercept, coefs, feature_keys FROM cs2_model_coefficients WHERE model_version = %s",
        (MODEL_VERSION,),
    )
    if not rows:
        raise RuntimeError(f"No coefficients for model_version={MODEL_VERSION!r}. Run cs2_v7_train.py first.")
    r = rows[0]
    coefs = r["coefs"] if isinstance(r["coefs"], dict) else json.loads(r["coefs"])
    return float(r["intercept"]), coefs, list(r["feature_keys"])


def _logit(p: float) -> float:
    p = max(min(p, 1 - 1e-4), 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        ex = math.exp(-x)
        return 1.0 / (1.0 + ex)
    ex = math.exp(x)
    return ex / (1.0 + ex)


def load_team_map() -> dict[str, float]:
    rows = execute_query(
        "SELECT team_name, AVG(win_pct) AS avg_wp FROM cs2_hltv_team_map_stats "
        "WHERE win_pct IS NOT NULL GROUP BY team_name"
    )
    return {r["team_name"]: float(r["avg_wp"]) for r in rows}


def load_pistol_map() -> dict[str, dict]:
    rows = execute_query("""
        SELECT DISTINCT ON (team_name)
            team_name, pistol_win_pct, pistols_played
        FROM cs2_team_pistol_stats
        ORDER BY team_name, snapshot_date DESC
    """)
    out = {}
    for r in rows:
        if r["pistol_win_pct"] is None:
            continue
        out[r["team_name"]] = {
            "overall": float(r["pistol_win_pct"]),
            "n": int(r["pistols_played"] or 0),
        }
    return out


def _pit_features(team1: str, team2: str, kickoff_ts) -> dict:
    """Compute PIT features for one upcoming match from cs2_results."""
    res = execute_query("""
        SELECT
            COALESCE((SELECT AVG(CASE
                WHEN (h.team1=%s AND h.winner='team1')
                  OR (h.team2=%s AND h.winner='team2')
                THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=%s OR h.team2=%s)
                  AND h.kickoff_time >= %s - INTERVAL '30 days'
                  AND h.kickoff_time < %s
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t1_form,
            (SELECT COUNT(*) FROM cs2_results h WHERE (h.team1=%s OR h.team2=%s)
              AND h.kickoff_time >= %s - INTERVAL '30 days'
              AND h.kickoff_time < %s
              AND h.winner IN ('team1','team2')) AS t1_form_n,
            COALESCE((SELECT AVG(CASE
                WHEN (h.team1=%s AND h.winner='team1')
                  OR (h.team2=%s AND h.winner='team2')
                THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=%s OR h.team2=%s)
                  AND h.kickoff_time >= %s - INTERVAL '30 days'
                  AND h.kickoff_time < %s
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t2_form,
            (SELECT COUNT(*) FROM cs2_results h WHERE (h.team1=%s OR h.team2=%s)
              AND h.kickoff_time >= %s - INTERVAL '30 days'
              AND h.kickoff_time < %s
              AND h.winner IN ('team1','team2')) AS t2_form_n,
            COALESCE((SELECT AVG(CASE
                WHEN (h.team1=%s AND h.team2=%s AND h.winner='team1')
                  OR (h.team1=%s AND h.team2=%s AND h.winner='team2')
                THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE ((h.team1=%s AND h.team2=%s) OR (h.team1=%s AND h.team2=%s))
                  AND h.kickoff_time >= %s - INTERVAL '365 days'
                  AND h.kickoff_time < %s
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS h2h_t1,
            (SELECT COUNT(*) FROM cs2_results h
              WHERE ((h.team1=%s AND h.team2=%s) OR (h.team1=%s AND h.team2=%s))
                AND h.kickoff_time >= %s - INTERVAL '365 days'
                AND h.kickoff_time < %s
                AND h.winner IN ('team1','team2')) AS h2h_n,
            COALESCE(EXTRACT(EPOCH FROM (%s -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                  WHERE (h.team1=%s OR h.team2=%s) AND h.kickoff_time < %s)
            )) / 86400, 30) AS t1_days_since,
            COALESCE(EXTRACT(EPOCH FROM (%s -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                  WHERE (h.team1=%s OR h.team2=%s) AND h.kickoff_time < %s)
            )) / 86400, 30) AS t2_days_since
    """, (
        team1, team1, team1, team1, kickoff_ts, kickoff_ts,
        team1, team1, kickoff_ts, kickoff_ts,
        team2, team2, team2, team2, kickoff_ts, kickoff_ts,
        team2, team2, kickoff_ts, kickoff_ts,
        team1, team2, team2, team1,
        team1, team2, team2, team1,
        kickoff_ts, kickoff_ts,
        team1, team2, team2, team1,
        kickoff_ts, kickoff_ts,
        kickoff_ts, team1, team1, kickoff_ts,
        kickoff_ts, team2, team2, kickoff_ts,
    ))
    return res[0]


def score_match(m: dict, tm: dict, pistol: dict, tier_map: dict,
                intercept: float, coefs: dict) -> dict | None:
    """Compute v7 prob for one upcoming match, or None if no hltv_v1 base."""
    if m.get("win_prob1") is None:
        return None
    saved = float(m["win_prob1"])

    pit = _pit_features(m["team1"], m["team2"], m["kickoff_time"])

    t1f = float(pit["t1_form"]) if pit["t1_form_n"] >= 3 else 0.5
    t2f = float(pit["t2_form"]) if pit["t2_form_n"] >= 3 else 0.5
    form_diff = t1f - t2f
    h2h_diff = (float(pit["h2h_t1"]) - 0.5) if (pit["h2h_n"] or 0) >= 2 else 0.0
    rest_diff = (min(float(pit["t1_days_since"]), 30.0) - min(float(pit["t2_days_since"]), 30.0)) / 30.0

    rank_diff = 0.0
    if m.get("hltv_rank1") and m.get("hltv_rank2"):
        rank_diff = float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0

    t1_tm, t2_tm = tm.get(m["team1"]), tm.get(m["team2"])
    tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0

    bo_centered = float((m.get("best_of") or 3) - 3)

    p1 = pistol.get(m["team1"])
    p2 = pistol.get(m["team2"])
    pistol_diff = 0.0
    if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
        pistol_diff = (p1["overall"] - p2["overall"]) / 100.0

    kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
    tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))

    feat_vals = {
        "logit_saved": _logit(saved),
        "form_diff": form_diff,
        "h2h_diff": h2h_diff,
        "tm_diff": tm_diff,
        "rest_diff": rest_diff,
        "rank_diff": rank_diff,
        "bo_centered": bo_centered,
        "pistol_diff": pistol_diff,
        "tier_s": 1.0 if tier == "s" else 0.0,
        "tier_a": 1.0 if tier == "a" else 0.0,
        "tier_b": 1.0 if tier == "b" else 0.0,
        "tier_c": 1.0 if tier == "c" else 0.0,
        "tier_d": 1.0 if tier == "d" else 0.0,
    }
    logit = intercept + sum(coefs[k] * feat_vals[k] for k in coefs.keys())
    p_team1 = _sigmoid(logit)
    return {
        "p1": round(p_team1, 4),
        "p2": round(1 - p_team1, 4),
        "fair1": round(1 / max(p_team1, 1e-4), 3),
        "fair2": round(1 / max(1 - p_team1, 1e-4), 3),
        "feats": feat_vals,
    }


def load_tier_map() -> dict:
    rows = execute_query("""
        SELECT team1_name, team2_name, begin_at::date AS kdate, tournament_tier
        FROM cs2_pandascore_matches WHERE tournament_tier IS NOT NULL
    """)
    out = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["kdate"])
        out[key] = r["tournament_tier"]
        out[(r["team2_name"], r["team1_name"], r["kdate"])] = r["tournament_tier"]
    return out


def load_upcoming() -> list[dict]:
    """Upcoming matches that already have an hltv_v1 base prediction."""
    return execute_query("""
        SELECT u.id, u.bo3gg_id, u.team1, u.team2, u.kickoff_time, u.best_of,
               u.hltv_rank1, u.hltv_rank2,
               h.win_prob1
        FROM cs2_upcoming_matches u
        JOIN LATERAL (
            SELECT win_prob1 FROM cs2_predictions p
            WHERE p.bo3gg_id = u.bo3gg_id AND p.model_version = 'hltv_v1'
            ORDER BY scan_time DESC LIMIT 1
        ) h ON TRUE
        WHERE u.kickoff_time > NOW()
          AND u.bo3gg_id IS NOT NULL
    """)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="Write to DB")
    args = ap.parse_args()

    print(f"\n=== CS2 v7 predict  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    intercept, coefs, feature_keys = load_coefs()
    print(f"  loaded v7 coefs (intercept={intercept:+.4f}, {len(coefs)} features)")

    tm = load_team_map()
    pistol = load_pistol_map()
    tier_map = load_tier_map()
    print(f"  team_map: {len(tm)}, pistol: {len(pistol)}, tier_map: {len(tier_map) // 2}")

    matches = load_upcoming()
    print(f"  upcoming matches with hltv_v1: {len(matches)}")

    written = 0
    scan_time = datetime.now(timezone.utc)
    for m in matches:
        out = score_match(m, tm, pistol, tier_map, intercept, coefs)
        if not out:
            continue
        if args.record:
            # Write cs2_predictions row
            execute_write("""
                INSERT INTO cs2_predictions
                    (bo3gg_id, scan_time, kickoff_time, league, best_of,
                     team1, team2, win_prob1, win_prob2, fair_odds1, fair_odds2,
                     model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bo3gg_id, scan_time, model_version) DO NOTHING
            """, (m["bo3gg_id"], scan_time, m["kickoff_time"], "",
                  m.get("best_of") or 3,
                  m["team1"], m["team2"],
                  out["p1"], out["p2"], out["fair1"], out["fair2"],
                  MODEL_VERSION))
            written += 1
        print(f"  {m['team1'][:18]:18} vs {m['team2'][:18]:18}  "
              f"v7: {out['p1']:.3f}/{out['p2']:.3f}  fair {out['fair1']:.2f}/{out['fair2']:.2f}  "
              f"(hltv_v1 was {float(m['win_prob1']):.3f})")

    print(f"\n  wrote {written} v7 predictions")


if __name__ == "__main__":
    main()
