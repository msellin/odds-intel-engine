"""
CS2 sneak-peek v3 — adds derived features from cs2_results (no scraping needed).

NEW features over v2:
  recent_form_diff       — last 30-day win rate per team (point-in-time)
  recent_form_n_diff     — sample size difference (a stable signal even on its own)
  h2h_diff               — head-to-head win-rate diff (capped at 5 most recent meets)
  days_since_match_diff  — rest advantage (positive = team1 had more rest)

All computed in SQL using each match's kickoff_time as the cutoff — proper
point-in-time correctness, no leakage from future matches.

Run:
    python3 scripts/esports/cs2_sneak_peek_v3.py [--since 2025-06-01]
"""

import argparse
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_team_map() -> dict:
    rows = execute_query("""
        SELECT team_name, AVG(win_pct) AS avg_wp, COUNT(DISTINCT map_name) AS maps_covered
        FROM cs2_hltv_team_map_stats WHERE win_pct IS NOT NULL GROUP BY team_name
    """)
    return {r["team_name"]: (float(r["avg_wp"]), int(r["maps_covered"])) for r in rows}


def load_matches_pit(since: str) -> list[dict]:
    """Pull matches + cs2_predictions + computed point-in-time form/h2h/rest in one
    query. cs2_results is small (~9k rows) so the LATERAL subqueries are fine."""
    return execute_query(
        """
        SELECT
            res.bo3gg_id, res.kickoff_time, res.team1, res.team2, res.winner,
            p.win_prob1, p.hltv_rank1, p.hltv_rank2, p.hltv_points1, p.hltv_points2,

            -- Recent form: last 30 days before this match, all matches involving team N
            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1 = res.team1 AND h.winner = 'team1')
                      OR (h.team2 = res.team1 AND h.winner = 'team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1 = res.team1 OR h.team2 = res.team1)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t1_form_30d,

            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1 = res.team1 OR h.team2 = res.team1)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t1_form_n,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1 = res.team2 AND h.winner = 'team1')
                      OR (h.team2 = res.team2 AND h.winner = 'team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1 = res.team2 OR h.team2 = res.team2)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t2_form_30d,

            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1 = res.team2 OR h.team2 = res.team2)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t2_form_n,

            -- H2H: capped to last 365 days, look at team1's win rate vs team2
            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1 = res.team1 AND h.team2 = res.team2 AND h.winner = 'team1')
                      OR (h.team1 = res.team2 AND h.team2 = res.team1 AND h.winner = 'team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE ((h.team1 = res.team1 AND h.team2 = res.team2)
                       OR (h.team1 = res.team2 AND h.team2 = res.team1))
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS h2h_t1_winpct,

            (SELECT COUNT(*) FROM cs2_results h
             WHERE ((h.team1 = res.team1 AND h.team2 = res.team2)
                    OR (h.team1 = res.team2 AND h.team2 = res.team1))
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS h2h_n,

            -- Days since last match (rest)
            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1 = res.team1 OR h.team2 = res.team1)
                   AND h.kickoff_time < res.kickoff_time)
            )) / 86400, 30) AS t1_days_since,

            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1 = res.team2 OR h.team2 = res.team2)
                   AND h.kickoff_time < res.kickoff_time)
            )) / 86400, 30) AS t2_days_since

        FROM cs2_results res
        LEFT JOIN LATERAL (
            SELECT * FROM cs2_predictions p2
            WHERE p2.bo3gg_id = res.bo3gg_id
            ORDER BY scan_time DESC LIMIT 1
        ) p ON TRUE
        WHERE res.winner IN ('team1','team2')
          AND res.kickoff_time >= %s
        ORDER BY res.kickoff_time
        """,
        (since,),
    )


def build_feats(matches, tm):
    out = []
    for m in matches:
        y = 1 if m["winner"] == "team1" else 0
        feat = {"kickoff": m["kickoff_time"], "y": y}

        feat["saved_prob"] = float(m["win_prob1"]) if m["win_prob1"] is not None else None
        feat["rank_diff"] = float(m["hltv_rank2"] - m["hltv_rank1"]) if (m["hltv_rank1"] and m["hltv_rank2"]) else None
        feat["points_diff"] = float(m["hltv_points1"] - m["hltv_points2"]) if (m["hltv_points1"] is not None and m["hltv_points2"] is not None) else None

        t1m, t2m = tm.get(m["team1"]), tm.get(m["team2"])
        feat["tm_wp_diff"] = (t1m[0] - t2m[0]) if (t1m and t2m) else None

        # Recent form: only meaningful if BOTH teams have ≥3 matches in the window
        if m["t1_form_n"] >= 3 and m["t2_form_n"] >= 3:
            feat["form_diff"] = float(m["t1_form_30d"]) - float(m["t2_form_30d"])
            feat["form_n_diff"] = float(m["t1_form_n"] - m["t2_form_n"])
        else:
            feat["form_diff"] = None
            feat["form_n_diff"] = None

        # H2H: only if they've actually played each other
        if m["h2h_n"] and m["h2h_n"] >= 2:
            feat["h2h_diff"] = float(m["h2h_t1_winpct"]) - 0.5  # centered
            feat["h2h_n"] = float(m["h2h_n"])
        else:
            feat["h2h_diff"] = None
            feat["h2h_n"] = None

        # Rest: clamp to [0, 30] so >30d gaps don't dominate
        t1d = min(float(m["t1_days_since"]), 30.0)
        t2d = min(float(m["t2_days_since"]), 30.0)
        feat["rest_diff"] = t1d - t2d

        out.append(feat)
    return out


def _metrics(y_te, p):
    return {
        "auc":     float(roc_auc_score(y_te, p)) if len(set(y_te)) > 1 else None,
        "logloss": float(log_loss(y_te, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y_te, p)),
        "acc":     float(((p >= 0.5).astype(int) == y_te).mean()),
    }


def evaluate(feats, keys, name):
    rows = [f for f in feats if all(f.get(k) is not None for k in keys)]
    if len(rows) < 50:
        return {"name": name, "n": len(rows), "skipped": True}
    cut = int(len(rows) * 0.7)
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=1000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return {"name": name, "n": len(rows), "n_train": cut,
            "coefs": dict(zip(keys, m.coef_[0].tolist())),
            "metrics": _metrics(y[cut:], p)}


def evaluate_saved(feats):
    rows = [f for f in feats if f.get("saved_prob") is not None]
    if not rows:
        return {"skipped": True, "n": 0}
    cut = int(len(rows) * 0.7)
    y = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p = np.array([r["saved_prob"] for r in rows[cut:]], dtype=float)
    return {"name": "saved_model_prob", "n": len(rows), "n_train": cut,
            "metrics": _metrics(y, p)}


def persist(name, n, m, since: date, keys=None, coefs=None, n_train=None):
    try:
        execute_write(
            """INSERT INTO cs2_model_backtest_history
                (run_id, feature_set, n_matches, n_train, n_test,
                 auc, logloss, brier, accuracy, since_date, feature_keys, coefs)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (RUN_ID, name, n, n_train, (n - (n_train or 0)) or None,
             m.get("auc"), m["logloss"], m["brier"], m["acc"], since,
             keys, json.dumps(coefs) if coefs else None),
        )
    except Exception as e:
        print(f"  [warn] persist failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    args = ap.parse_args()

    print("loading team-map features…")
    tm = load_team_map()
    print(f"  teams: {len(tm)}")

    print("loading matches + PIT features (this takes ~10-20s)…")
    matches = load_matches_pit(args.since)
    print(f"  matches: {len(matches)}")

    feats = build_feats(matches, tm)
    print(f"  team1 win rate: {sum(f['y'] for f in feats) / len(feats):.3f}")
    print()
    print("coverage by feature:")
    for k in ["saved_prob", "rank_diff", "tm_wp_diff", "form_diff", "h2h_diff", "rest_diff"]:
        n = sum(1 for f in feats if f.get(k) is not None)
        print(f"  {k:18} {n:5}/{len(feats)}")

    print()
    print(f"{'set':32} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 68)

    since_d = date.fromisoformat(args.since)

    # Baselines
    rows = feats
    y = np.array([r["y"] for r in rows])
    p_naive = np.full(len(rows), 0.5)
    m = _metrics(y, p_naive)
    print(f"{'coin_flip':32} {len(rows):>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
    persist("coin_flip", len(rows), m, since_d)

    # Saved model
    r = evaluate_saved(feats)
    if not r.get("skipped"):
        m = r["metrics"]
        print(f"{'saved_model_prob (hltv_v1)':32} {r['n']:>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
        persist("saved_model_prob", r["n"], m, since_d,
                keys=["win_prob1"], n_train=r.get("n_train"))

    # Single feature checks (does each one have signal on its own?)
    for keys, label in [
        (["rank_diff"],   "rank_diff_only"),
        (["form_diff"],   "form_diff_only"),
        (["h2h_diff"],    "h2h_diff_only"),
        (["rest_diff"],   "rest_diff_only"),
        (["tm_wp_diff"],  "tm_wp_diff_only"),
    ]:
        r = evaluate(feats, keys, label)
        if r.get("skipped"):
            print(f"{label:32} {r['n']:>5}  (skipped — <50 rows)")
            continue
        m = r["metrics"]
        print(f"{label:32} {r['n']:>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
        persist(label, r["n"], m, since_d, keys=keys, coefs=r.get("coefs"), n_train=r.get("n_train"))

    # Progressive stacks
    for keys, label in [
        (["rank_diff", "form_diff"],                       "rank + form"),
        (["rank_diff", "form_diff", "tm_wp_diff"],         "rank + form + tm_wp"),
        (["rank_diff", "form_diff", "tm_wp_diff", "rest_diff"],         "+ rest"),
        (["rank_diff", "form_diff", "tm_wp_diff", "rest_diff", "h2h_diff"], "ALL features"),
    ]:
        r = evaluate(feats, keys, label)
        if r.get("skipped"):
            print(f"{label:32} {r['n']:>5}  (skipped — <50 rows)")
            continue
        m = r["metrics"]
        print(f"{label:32} {r['n']:>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
        persist(label, r["n"], m, since_d, keys=keys, coefs=r.get("coefs"), n_train=r.get("n_train"))


if __name__ == "__main__":
    main()
