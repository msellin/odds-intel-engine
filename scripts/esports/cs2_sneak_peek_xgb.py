"""
CS2 sneak-peek XGBoost experiment.

Logistic regression captures only LINEAR effects between features and label.
XGBoost catches INTERACTIONS — e.g. "form helps more for top teams" or "rest
matters only for Bo5". If interaction signal exists, XGBoost should beat
logistic at the same feature set.

Uses identical v5 features. Walk-forward 70/30 split. Compares head-to-head
against v5 best (v4-ALL + bo, AUC 0.688).

Run:
    python3 scripts/esports/cs2_sneak_peek_xgb.py [--since 2025-06-01]
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

sys.path.insert(0, str(Path(__file__).parent))
# Reuse v5's row builder by importing the relevant pieces
from cs2_sneak_peek_v5 import (  # type: ignore
    load_team_map, load_matches_with_features, build_rows,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402
import xgboost as xgb  # noqa: E402


RUN_ID = str(uuid.uuid4())


def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
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


def evaluate_logistic(rows, keys, name):
    cut = int(len(rows) * 0.7)
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=2000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return _metrics(y[cut:], p), cut


def evaluate_xgb(rows, keys, name, **xgb_params):
    cut = int(len(rows) * 0.7)
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    defaults = dict(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9,
        eval_metric="logloss", random_state=42,
        objective="binary:logistic", n_jobs=2,
    )
    defaults.update(xgb_params)
    model = xgb.XGBClassifier(**defaults)
    model.fit(X[:cut], y[:cut])
    p = model.predict_proba(X[cut:])[:, 1]
    fi = dict(zip(keys, model.feature_importances_.tolist()))
    return _metrics(y[cut:], p), cut, fi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    args = ap.parse_args()
    since_d = date.fromisoformat(args.since)

    print("loading data…")
    tm = load_team_map()
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm)
    print(f"  {len(rows)} matches")

    # Feature sets to compare
    FEATURE_SETS = [
        (["logit_saved"], "v4 base only"),
        (["logit_saved", "form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff"],
         "v4 ALL"),
        (["logit_saved", "form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered"],
         "v5 v4-ALL + bo"),
        (["logit_saved", "form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
          "bo_centered", "tier", "opp_adj_form"],
         "v5 kitchen sink"),
    ]

    cut = int(len(rows) * 0.7)
    print(f"  train: {cut}  test: {len(rows) - cut}")
    print()
    print(f"{'features':30}  {'model':10}  {'AUC':>6}  {'LogL':>7}  {'Brier':>7}  {'Acc':>6}")
    print("-" * 75)

    for keys, label in FEATURE_SETS:
        # Logistic
        m_lr, _ = evaluate_logistic(rows, keys, label)
        print(f"{label:30}  {'logistic':10}  {m_lr['auc']:>6.3f}  {m_lr['logloss']:>7.4f}  {m_lr['brier']:>7.4f}  {m_lr['acc']:>6.3f}")
        persist(f"xgb_lr_{label}", len(rows), m_lr, since_d, keys=keys, n_train=cut)

        # XGBoost
        m_x, _, fi = evaluate_xgb(rows, keys, label)
        delta = m_x["auc"] - m_lr["auc"]
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{'':30}  {'xgb':10}  {m_x['auc']:>6.3f}{marker} {m_x['logloss']:>7.4f}  {m_x['brier']:>7.4f}  {m_x['acc']:>6.3f}  (Δ AUC {delta:+.3f})")
        persist(f"xgb_xgb_{label}", len(rows), m_x, since_d, keys=keys, coefs=fi, n_train=cut)

        # Top-3 importance for XGB
        top = sorted(fi.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{k}={v:.2f}" for k, v in top)
        print(f"{'':30}  top FI: {top_str}")
        print()


if __name__ == "__main__":
    main()
