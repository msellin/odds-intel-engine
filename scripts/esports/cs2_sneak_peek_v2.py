"""
CS2 sneak-peek v2 — comprehensive.

Joins ALL feature sources we already have:
  cs2_predictions      — model's saved features (ELO, PQ, win_prob, HLTV rank)
  cs2_hltv_team_map_stats — team-map career win %
  cs2_hltv_player_stats — per-player career JSONB (Rating, K/D, ADR, ...)
  cs2_hltv_rankings    — current HLTV top-30 rank
  cs2_results          — ground-truth winners

Then evaluates progressively richer feature sets vs actual outcomes:
  baseline       — naive home (always pick team1)
  saved_model    — cs2_predictions.win_prob1 (what the bot actually used)
  + ranking_diff — add HLTV rank diff
  + teammap_diff — add per-team-per-map win-pct diff
  + player_diff  — add avg player rating diff

Walk-forward: train on oldest 70%, test on newest 30%.

Run:
    python3 scripts/esports/cs2_sneak_peek_v2.py [--since 2025-01-01]
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, date
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


# Set per-invocation so every row written this run shares a run_id (for grouping).
RUN_ID = str(uuid.uuid4())


def load_team_map() -> dict:
    rows = execute_query("""
        SELECT team_name, AVG(win_pct) AS avg_wp, COUNT(DISTINCT map_name) AS maps_covered
        FROM cs2_hltv_team_map_stats WHERE win_pct IS NOT NULL GROUP BY team_name
    """)
    return {r["team_name"]: (float(r["avg_wp"]), int(r["maps_covered"])) for r in rows}


def load_player_avg_by_team() -> dict:
    """avg player rating (Rating 2.0) per team from current top-30 rosters."""
    rows = execute_query("""
        SELECT r.team_name,
               r.players,
               (SELECT AVG((ps.stats->>'rating_2.0')::float)
                FROM cs2_hltv_player_stats ps
                WHERE ps.nickname = ANY(r.players)
                  AND ps.stats ? 'rating_2.0') AS team_avg_rating
        FROM cs2_hltv_rankings r
        WHERE r.snapshot_date = (SELECT MAX(snapshot_date) FROM cs2_hltv_rankings)
    """)
    out = {}
    for r in rows:
        if r["team_avg_rating"] is not None:
            out[r["team_name"]] = float(r["team_avg_rating"])
    return out


def load_matches_with_predictions(since: str) -> list[dict]:
    return execute_query(
        """
        SELECT
            res.bo3gg_id, res.kickoff_time, res.team1, res.team2, res.winner,
            p.win_prob1, p.elo1, p.elo2, p.pq1, p.pq2,
            p.hltv_rank1, p.hltv_rank2, p.hltv_points1, p.hltv_points2,
            p.model_version
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


def build_features(matches, tm, pr) -> list[dict]:
    out = []
    for m in matches:
        y = 1 if m["winner"] == "team1" else 0
        feat = {"kickoff": m["kickoff_time"], "y": y, "team1": m["team1"], "team2": m["team2"]}

        feat["saved_prob"] = float(m["win_prob1"]) if m["win_prob1"] is not None else None

        if m["hltv_rank1"] and m["hltv_rank2"]:
            feat["rank_diff"] = float(m["hltv_rank2"] - m["hltv_rank1"])  # +ve = team1 better
        else:
            feat["rank_diff"] = None
        if m["hltv_points1"] is not None and m["hltv_points2"] is not None:
            feat["points_diff"] = float(m["hltv_points1"] - m["hltv_points2"])
        else:
            feat["points_diff"] = None

        t1, t2 = tm.get(m["team1"]), tm.get(m["team2"])
        feat["tm_wp_diff"] = (t1[0] - t2[0]) if (t1 and t2) else None

        p1, p2 = pr.get(m["team1"]), pr.get(m["team2"])
        feat["player_rating_diff"] = (p1 - p2) if (p1 and p2) else None

        out.append(feat)
    return out


def _metrics(y_te, p) -> dict:
    return {
        "auc":      float(roc_auc_score(y_te, p)) if len(set(y_te)) > 1 else None,
        "logloss":  float(log_loss(y_te, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":    float(brier_score_loss(y_te, p)),
        "acc":      float(((p >= 0.5).astype(int) == y_te).mean()),
    }


def persist(name: str, n: int, m: dict, since: date,
            feature_keys: list[str] | None = None,
            coefs: dict | None = None, n_train: int | None = None) -> None:
    """Write one feature-set row to cs2_model_backtest_history."""
    try:
        execute_write(
            """INSERT INTO cs2_model_backtest_history
                (run_id, feature_set, n_matches, n_train, n_test,
                 auc, logloss, brier, accuracy, since_date, feature_keys, coefs)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (RUN_ID, name, n, n_train, (n - (n_train or 0)) or None,
             m.get("auc"), m["logloss"], m["brier"], m["acc"], since,
             feature_keys, json.dumps(coefs) if coefs else None),
        )
    except Exception as e:
        # Don't crash the backtest if history table isn't migrated yet.
        print(f"  [warn] persist failed: {e}")


def evaluate(feats: list[dict], feature_keys: list[str], name: str) -> dict:
    """Train logistic on feature_keys, walk-forward 70/30."""
    rows = [f for f in feats if all(f.get(k) is not None for k in feature_keys)]
    if len(rows) < 50:
        return {"name": name, "n": len(rows), "skipped": True}
    cut = int(len(rows) * 0.7)
    X = np.array([[r[k] for k in feature_keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    X_tr, y_tr, X_te, y_te = X[:cut], y[:cut], X[cut:], y[cut:]
    m = LogisticRegression(max_iter=1000)
    m.fit(X_tr, y_tr)
    p = m.predict_proba(X_te)[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(feature_keys, m.coef_[0].tolist())),
        "metrics": _metrics(y_te, p),
    }


def evaluate_saved(feats):
    """Direct use of the model's saved probability — no training, just scoring."""
    rows = [f for f in feats if f.get("saved_prob") is not None]
    if not rows:
        return {"name": "saved_model", "n": 0, "skipped": True}
    cut = int(len(rows) * 0.7)
    y = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p = np.array([r["saved_prob"] for r in rows[cut:]], dtype=float)
    return {"name": "saved_model_direct", "n": len(rows), "n_test": len(rows) - cut, "metrics": _metrics(y, p)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    args = ap.parse_args()

    print(f"loading team-map features…")
    tm = load_team_map()
    print(f"  teams with team-map data: {len(tm)}")

    print("loading player-rating features…")
    pr = load_player_avg_by_team()
    print(f"  teams with player-rating data: {len(pr)}")

    print(f"loading matches+predictions since {args.since}…")
    matches = load_matches_with_predictions(args.since)
    print(f"  matches total: {len(matches)}")

    feats = build_features(matches, tm, pr)
    print(f"  team1 win rate: {sum(f['y'] for f in feats) / len(feats):.3f}")
    print()
    print("coverage by feature set:")
    for k in ["saved_prob", "rank_diff", "points_diff", "tm_wp_diff", "player_rating_diff"]:
        n = sum(1 for f in feats if f.get(k) is not None)
        print(f"  {k:22} {n:5} / {len(feats)}")

    print()
    print(f"{'set':28} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 64)

    since_d = date.fromisoformat(args.since)

    # 1) Naive baselines
    rows = feats
    y = np.array([r["y"] for r in rows])
    p_naive = np.full(len(rows), 0.5)
    m = _metrics(y, p_naive)
    print(f"{'coin_flip':28} {len(rows):>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
    persist("coin_flip", len(rows), m, since_d)

    p_home = np.full(len(rows), float(np.mean(y)))
    m = _metrics(y, p_home)
    print(f"{'home_team_base':28} {len(rows):>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
    persist("home_team_base", len(rows), m, since_d)

    # 2) Saved model probability (no retrain — just score it)
    r = evaluate_saved(feats)
    if not r.get("skipped"):
        m = r["metrics"]
        print(f"{'saved_model_prob (direct)':28} {r['n']:>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
        persist("saved_model_prob", r["n"], m, since_d, feature_keys=["win_prob1"])

    # 3) Progressively richer feature sets, walk-forward retrained
    for keys, label in [
        (["rank_diff"], "rank_diff_only"),
        (["rank_diff", "points_diff"], "+ points_diff"),
        (["rank_diff", "tm_wp_diff"], "+ team-map wp"),
        (["tm_wp_diff"], "team_map_only"),
        (["rank_diff", "tm_wp_diff", "player_rating_diff"], "+ player rating"),
        (["rank_diff", "points_diff", "tm_wp_diff", "player_rating_diff"], "ALL"),
    ]:
        r = evaluate(feats, keys, label)
        if r.get("skipped"):
            print(f"{label:28} {r['n']:>5}  (skipped — <50 rows)")
            continue
        m = r["metrics"]
        print(f"{label:28} {r['n']:>5} {m['auc'] or 0:>6.3f} {m['logloss']:>7.4f} {m['brier']:>7.4f} {m['acc']:>6.3f}")
        persist(label, r["n"], m, since_d, feature_keys=keys, coefs=r.get("coefs"),
                n_train=r.get("n_train"))

    # Compare against last run (if there's one).
    prev = execute_query("""
        SELECT feature_set, AVG(auc) AS auc, AVG(accuracy) AS acc
        FROM cs2_model_backtest_history
        WHERE run_id = (
            SELECT run_id FROM cs2_model_backtest_history
            WHERE run_id != %s
            ORDER BY run_at DESC LIMIT 1
        )
        GROUP BY feature_set
    """, (RUN_ID,))
    if prev:
        print()
        print("delta vs last run:")
        prev_map = {p["feature_set"]: (float(p["auc"]) if p["auc"] else None, float(p["acc"]) if p["acc"] else None) for p in prev}
        cur_rows = execute_query("""
            SELECT feature_set, auc, accuracy FROM cs2_model_backtest_history
            WHERE run_id = %s
        """, (RUN_ID,))
        for r in cur_rows:
            fs = r["feature_set"]
            if fs in prev_map:
                p_auc, p_acc = prev_map[fs]
                d_auc = (float(r["auc"]) - p_auc) if (r["auc"] and p_auc) else None
                d_acc = (float(r["accuracy"]) - p_acc) if (r["accuracy"] and p_acc) else None
                if d_auc is not None:
                    print(f"  {fs:24} AUC {float(r['auc']):.3f} ({d_auc:+.3f})   Acc {float(r['accuracy']):.3f} ({d_acc:+.3f})")


if __name__ == "__main__":
    main()
