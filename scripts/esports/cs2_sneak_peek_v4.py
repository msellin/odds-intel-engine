"""
CS2 sneak-peek v4 — proper feature-add-on-top experimental design.

Difference from v3: instead of training each feature set in isolation (which
constrains the sample to matches where every feature is non-null, losing signal),
v4 keeps the saved hltv_v1 prediction as a BASE and tests whether each new
feature adds lift ON TOP via stacking.

Features tested:
  base               — hltv_v1 saved win_prob1 (no retrain)
  base + form        — add 30d form_diff (null-filled to 0 if no history)
  base + h2h         — add head-to-head winrate (null-filled to 0)
  base + tm_wp       — add team-map career win-pct diff (null-filled to 0)
  base + rest        — add days-since-last-match diff
  base + rank        — add HLTV rank diff
  base + ALL         — stack everything

The stack model takes the logit of the saved_prob plus each extra feature,
fits logistic regression on (logit_saved, form, h2h, tm, rest, rank).
Significant non-zero coefficient on a new feature = it adds info beyond
hltv_v1.

Run:
    python3 scripts/esports/cs2_sneak_peek_v4.py [--since 2025-06-01]
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
        SELECT team_name, AVG(win_pct) AS avg_wp
        FROM cs2_hltv_team_map_stats WHERE win_pct IS NOT NULL GROUP BY team_name
    """)
    return {r["team_name"]: float(r["avg_wp"]) for r in rows}


def load_matches_with_pit_features(since: str) -> list[dict]:
    return execute_query(
        """
        SELECT
            res.kickoff_time, res.team1, res.team2, res.winner,
            p.win_prob1, p.hltv_rank1, p.hltv_rank2,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team1 AND h.winner='team1')
                      OR (h.team2=res.team1 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=res.team1 OR h.team2=res.team1)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t1_form,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1=res.team1 OR h.team2=res.team1)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t1_form_n,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team2 AND h.winner='team1')
                      OR (h.team2=res.team2 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=res.team2 OR h.team2=res.team2)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t2_form,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1=res.team2 OR h.team2=res.team2)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t2_form_n,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team1 AND h.team2=res.team2 AND h.winner='team1')
                      OR (h.team1=res.team2 AND h.team2=res.team1 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE ((h.team1=res.team1 AND h.team2=res.team2)
                       OR (h.team1=res.team2 AND h.team2=res.team1))
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS h2h_t1,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE ((h.team1=res.team1 AND h.team2=res.team2)
                    OR (h.team1=res.team2 AND h.team2=res.team1))
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS h2h_n,

            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1=res.team1 OR h.team2=res.team1)
                   AND h.kickoff_time < res.kickoff_time)
            )) / 86400, 30) AS t1_days_since,
            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1=res.team2 OR h.team2=res.team2)
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


def _logit(p: float) -> float:
    p = max(min(p, 1 - 1e-4), 1e-4)
    return float(np.log(p / (1 - p)))


def build_rows(matches, tm):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])
        # Form diff with null-fill: if either team has <3 matches in the window
        # use 0 (neutral) for that team — keeps row in the sample.
        t1f = float(m["t1_form"]) if m["t1_form_n"] >= 3 else 0.5
        t2f = float(m["t2_form"]) if m["t2_form_n"] >= 3 else 0.5
        form_diff = t1f - t2f

        h2h_diff = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0

        rest_diff = min(float(m["t1_days_since"]), 30.0) - min(float(m["t2_days_since"]), 30.0)
        rest_diff /= 30.0  # normalize

        rank_diff = (
            float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0
            if (m["hltv_rank1"] and m["hltv_rank2"])
            else 0.0
        )

        t1_tm, t2_tm = tm.get(m["team1"]), tm.get(m["team2"])
        tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff,
            "h2h_diff": h2h_diff,
            "rest_diff": rest_diff,
            "rank_diff": rank_diff,
            "tm_diff": tm_diff,
        })
    return out


def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
    }


def evaluate_stack(rows, extra_keys, name):
    """Train logistic on [logit_saved, *extra_keys] -> y. Walk-forward 70/30."""
    cut = int(len(rows) * 0.7)
    if cut < 50: return {"skipped": True, "n": len(rows)}
    keys = ["logit_saved"] + extra_keys
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    model = LogisticRegression(max_iter=2000)
    model.fit(X[:cut], y[:cut])
    p = model.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(keys, model.coef_[0].tolist())),
        "intercept": float(model.intercept_[0]),
        "metrics": _metrics(y[cut:], p),
    }


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
    since_d = date.fromisoformat(args.since)

    print("loading team_map…")
    tm = load_team_map()
    print(f"  {len(tm)} teams")

    print("loading matches + PIT features…")
    matches = load_matches_with_pit_features(args.since)
    rows = build_rows(matches, tm)
    print(f"  {len(rows)} matches with saved_prob (full sample)")
    print(f"  team1 win rate: {sum(r['y'] for r in rows) / len(rows):.3f}")
    print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)

    # Baseline = saved hltv_v1 prob, no retrain
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"{'set':32} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 68)
    print(f"{'baseline (hltv_v1 direct)':32} {len(rows):>5} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v4_baseline_hltv_v1", len(rows), m_base, since_d, keys=["win_prob1"], n_train=cut)

    # Stack experiments
    for keys, label in [
        ([],                                              "v4_logit_saved_only"),  # sanity: should match baseline ~exactly
        (["form_diff"],                                   "v4 + form"),
        (["h2h_diff"],                                    "v4 + h2h"),
        (["tm_diff"],                                     "v4 + team-map wp"),
        (["rest_diff"],                                   "v4 + rest"),
        (["rank_diff"],                                   "v4 + rank"),
        (["form_diff", "h2h_diff", "tm_diff"],            "v4 + form+h2h+tm"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff"], "v4 + ALL"),
    ]:
        r = evaluate_stack(rows, keys, label)
        if r.get("skipped"):
            print(f"{label:32} {r['n']:>5}  (skipped — <50 rows)")
            continue
        mm = r["metrics"]
        coefs_str = ", ".join(f"{k}={v:+.2f}" for k, v in r["coefs"].items() if k != "logit_saved")
        delta_auc = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta_auc) >= 0.005 else " "
        print(f"{label:32} {r['n']:>5} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}   {coefs_str}")
        persist(label, r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    # Compare against last run
    prev = execute_query("""
        SELECT feature_set, auc, accuracy FROM cs2_model_backtest_history
        WHERE run_id = (SELECT run_id FROM cs2_model_backtest_history
                        WHERE run_id != %s ORDER BY run_at DESC LIMIT 1)
    """, (RUN_ID,))
    if prev:
        print()
        print("vs previous run:")
        prev_map = {p["feature_set"]: (float(p["auc"]) if p["auc"] else None,
                                       float(p["accuracy"]) if p["accuracy"] else None) for p in prev}
        cur = execute_query("""
            SELECT feature_set, auc, accuracy FROM cs2_model_backtest_history
            WHERE run_id = %s
        """, (RUN_ID,))
        for r in cur:
            if r["feature_set"] in prev_map:
                pauc, pacc = prev_map[r["feature_set"]]
                dauc = (float(r["auc"]) - pauc) if (r["auc"] and pauc) else None
                if dauc is not None:
                    print(f"  {r['feature_set']:32} ΔAUC {dauc:+.3f}")


if __name__ == "__main__":
    main()
