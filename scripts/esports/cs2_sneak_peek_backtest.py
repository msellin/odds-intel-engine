"""
CS2 model sneak-peek backtest.

Goal: see whether the per-team-per-map HLTV stats we've been backfilling
contain extra signal beyond what hltv_v1 (ranking-based) already captures.

Caveat: cs2_hltv_team_map_stats is today's snapshot. Using it to predict
matches from 12 months ago lets the model peek at the future. We mitigate
by using only matches from the most recent N months (where today's snapshot
~= point-in-time). For a real retrain we'd rebuild stats from our own
match log (cs2_hltv_match_maps), but that needs the /results backfill to
finish first.

Features tested (all from cs2_hltv_team_map_stats, aggregated per team):
- avg_win_pct                                — team's mean map win rate
- avg_round_winpct_after_first_kill          — clutch / closing strength
- avg_round_winpct_after_first_death         — comeback ability
- maps_covered                               — how many maps they actually play

For each match we take (team1 - team2) of each feature, then logistic-regress
match outcome. Walk-forward split: train on older 70%, test on newer 30%.

Run:
    python3 scripts/esports/cs2_sneak_peek_backtest.py [--since 2025-01-01]
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query  # noqa: E402

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


def load_team_features() -> dict:
    """Return {team_name: {avg_win_pct, avg_clutch, avg_comeback, maps_covered}}."""
    rows = execute_query("""
        SELECT team_name,
               AVG(win_pct)                              AS avg_win_pct,
               AVG(round_win_pct_after_first_kill)       AS avg_clutch,
               AVG(round_win_pct_after_first_death)      AS avg_comeback,
               COUNT(DISTINCT map_name)                  AS maps_covered,
               SUM(wins) + SUM(losses) + SUM(draws)      AS total_games
        FROM cs2_hltv_team_map_stats
        WHERE win_pct IS NOT NULL
        GROUP BY team_name
    """)
    return {
        r["team_name"]: {
            "avg_win_pct":  float(r["avg_win_pct"]),
            "avg_clutch":   float(r["avg_clutch"])   if r["avg_clutch"]   is not None else None,
            "avg_comeback": float(r["avg_comeback"]) if r["avg_comeback"] is not None else None,
            "maps_covered": int(r["maps_covered"]),
            "total_games":  int(r["total_games"] or 0),
        }
        for r in rows
    }


def load_matches(since: str) -> list[dict]:
    return execute_query(
        """
        SELECT bo3gg_id, kickoff_time, team1, team2, winner
        FROM cs2_results
        WHERE winner IS NOT NULL
          AND winner IN ('team1','team2')
          AND kickoff_time >= %s
        ORDER BY kickoff_time
        """,
        (since,),
    )


def build_dataset(matches: list[dict], tf: dict) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Return (X, y, meta_rows) where each row is one match. Only matches with
    BOTH teams in tf are kept. Sneak-peek features: avg map win-pct + maps-covered
    (proxy for how diverse the team's map pool is). Other columns aren't populated
    yet — would come from a deeper scraper extension."""
    rows = []
    for m in matches:
        t1 = tf.get(m["team1"])
        t2 = tf.get(m["team2"])
        if not t1 or not t2:
            continue
        rows.append({
            "kickoff": m["kickoff_time"],
            "team1":   m["team1"],
            "team2":   m["team2"],
            "y":       1 if m["winner"] == "team1" else 0,
            "wp_diff":   t1["avg_win_pct"] - t2["avg_win_pct"],
            "maps_diff": t1["maps_covered"] - t2["maps_covered"],
            "games_diff": (t1["total_games"] - t2["total_games"]) / 100.0,
        })
    if not rows:
        return np.zeros((0, 0)), np.zeros(0), []
    X = np.array([[r["wp_diff"], r["maps_diff"], r["games_diff"]] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    return X, y, rows


def walk_forward_eval(X: np.ndarray, y: np.ndarray, rows: list[dict], split: float = 0.7) -> dict:
    """Walk-forward: train on oldest split fraction, test on remainder."""
    n = len(rows)
    if n < 50:
        return {"error": f"only {n} matches — not enough for split"}
    cut = int(n * split)
    X_tr, X_te = X[:cut], X[cut:]
    y_tr, y_te = y[:cut], y[cut:]

    # Baseline: just win_pct diff
    baseline = LogisticRegression(max_iter=1000)
    baseline.fit(X_tr[:, :1], y_tr)
    p_base = baseline.predict_proba(X_te[:, :1])[:, 1]

    # Full feature set
    full = LogisticRegression(max_iter=1000)
    full.fit(X_tr, y_tr)
    p_full = full.predict_proba(X_te)[:, 1]

    # Naive coin-flip baseline (always 50/50)
    p_coin = np.full(len(y_te), 0.5)

    return {
        "n_train": cut, "n_test": n - cut,
        "train_start": rows[0]["kickoff"], "train_end": rows[cut - 1]["kickoff"],
        "test_start":  rows[cut]["kickoff"], "test_end":  rows[-1]["kickoff"],
        "coefs_full": full.coef_[0].tolist(),
        "intercept_full": float(full.intercept_[0]),
        "metrics": {
            "coin":    _metrics(y_te, p_coin),
            "wp_only": _metrics(y_te, p_base),
            "full":    _metrics(y_te, p_full),
        },
    }


def _metrics(y_te, p) -> dict:
    return {
        "auc":      float(roc_auc_score(y_te, p)) if len(set(y_te)) > 1 else None,
        "logloss":  float(log_loss(y_te, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":    float(brier_score_loss(y_te, p)),
        "acc":      float(((p >= 0.5).astype(int) == y_te).mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01", help="start date (YYYY-MM-DD)")
    args = ap.parse_args()

    print(f"loading team features…")
    tf = load_team_features()
    print(f"  teams with features: {len(tf)}")

    print(f"loading matches since {args.since}…")
    matches = load_matches(args.since)
    print(f"  total finished matches: {len(matches)}")

    X, y, rows = build_dataset(matches, tf)
    print(f"  matches with both teams covered: {len(rows)}")
    print(f"  team1 win rate: {y.mean():.3f}")
    if len(rows) < 50:
        print("not enough data — backfill more team stats first")
        return

    out = walk_forward_eval(X, y, rows)
    print()
    print(f"train: {out['n_train']} matches ({out['train_start']} → {out['train_end']})")
    print(f"test:  {out['n_test']} matches ({out['test_start']} → {out['test_end']})")
    print()
    print("                AUC      LogLoss   Brier    Acc")
    for label, m in out["metrics"].items():
        auc_s = f"{m['auc']:.3f}" if m["auc"] is not None else "n/a"
        print(f"  {label:8}  {auc_s:8} {m['logloss']:.4f}    {m['brier']:.4f}   {m['acc']:.3f}")

    print()
    print("full-model coefficients:")
    for name, c in zip(["wp_diff", "maps_diff", "games_diff"], out["coefs_full"]):
        print(f"  {name:14} {c:+.5f}")
    print(f"  intercept     {out['intercept_full']:+.5f}")

    # Verdict
    full_auc    = out["metrics"]["full"]["auc"]
    wp_only_auc = out["metrics"]["wp_only"]["auc"]
    if full_auc and wp_only_auc:
        lift = full_auc - wp_only_auc
        print()
        print(f"AUC delta vs wp_only: {lift:+.3f}")
        if full_auc > 0.55:
            print(f"  signal: {full_auc:.3f} AUC — team_map_stats data has predictive power")
        else:
            print(f"  weak: {full_auc:.3f} AUC — may need point-in-time + roster-aware features")


if __name__ == "__main__":
    main()
