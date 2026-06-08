#!/usr/bin/env python3
"""
CS2 combined model backtest — ELO + player quality (HLTV rating).

Uses cs2_newestcombinedmatches.csv (7,033 BO3 matches, May 2024 – Oct 2025).
Processes chronologically to build rolling ELO + point-in-time player quality.

Models compared:
  A. ELO-only (logistic, exact same math as scanner)
  B. Player quality only (team avg HLTV rating diff)
  C. Combined logistic regression (ELO diff + PQ diff + is_lan)
  D. Combined gradient boosting

Usage:
    python3 scripts/esports/cs2_model_backtest.py
    python3 scripts/esports/cs2_model_backtest.py --plot      # calibration plot
    python3 scripts/esports/cs2_model_backtest.py --features  # feature importance
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from math import log, comb
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data/esports/cs2")
PLAYER_CSV = DATA_DIR / "cs2_newestcombinedmatches.csv"

# ── ELO config (matches scanner) ─────────────────────────────────────────────
INITIAL_ELO = 1500.0
K_LAN = 27.2    # K_BASE(32) × BO3_weight(0.85)
K_ONLINE = 19.0 # online matches carry less signal


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


# ── Data loading ──────────────────────────────────────────────────────────────
def load_dataset() -> list[dict]:
    if not PLAYER_CSV.exists():
        print(f"[!] {PLAYER_CSV} not found", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(PLAYER_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t1 = r.get("team1_name", "").strip()
            t2 = r.get("team2_name", "").strip()
            winner = r.get("winner", "").strip()
            if not t1 or not t2 or winner not in ("team1", "team2"):
                continue
            try:
                dt = datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
                pq1 = float(r["team1_avg_RATING"])
                pq2 = float(r["team2_avg_RATING"])
            except (ValueError, KeyError):
                continue

            is_lan = r.get("event_type", "").strip().lower() == "lan"
            rows.append({
                "date": dt,
                "team1": t1,
                "team2": t2,
                "label": 1 if winner == "team1" else 0,
                "pq1": pq1,
                "pq2": pq2,
                "is_lan": is_lan,
            })

    rows.sort(key=lambda x: x["date"])
    return rows


# ── Rolling ELO pass ──────────────────────────────────────────────────────────
def build_rolling_features(rows: list[dict]) -> list[dict]:
    """One chronological pass: record pre-match ELO diff, then update ELO."""
    ratings: dict[str, float] = {}
    features = []

    for r in rows:
        t1, t2 = r["team1"], r["team2"]
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)

        e1 = elo_expected(r1, r2)
        result = float(r["label"])

        k = K_LAN if r["is_lan"] else K_ONLINE
        ratings[t1] = r1 + k * (result - e1)
        ratings[t2] = r2 + k * ((1 - result) - (1 - e1))

        features.append({
            "date": r["date"],
            "team1": t1, "team2": t2,
            "elo_diff": r1 - r2,          # before update
            "elo_prob1": e1,              # ELO-implied win prob team1
            "pq_diff": r["pq1"] - r["pq2"],
            "pq1": r["pq1"], "pq2": r["pq2"],
            "is_lan": int(r["is_lan"]),
            "label": r["label"],
        })

    return features


# ── Metrics ───────────────────────────────────────────────────────────────────
def accuracy(probs: list[float], labels: list[int]) -> float:
    return sum(1 for p, y in zip(probs, labels) if (p >= 0.5) == bool(y)) / len(labels)


def log_loss(probs: list[float], labels: list[int]) -> float:
    eps = 1e-9
    return -sum(y * log(max(p, eps)) + (1 - y) * log(max(1 - p, eps))
                for p, y in zip(probs, labels)) / len(labels)


def brier(probs: list[float], labels: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(labels)


def roi_at_fair(probs: list[float], labels: list[int]) -> float:
    """If we always bet at our fair odds (no edge), what's the average P&L per unit?"""
    total = 0.0
    for p, y in zip(probs, labels):
        if p < 0.001:
            continue
        fair = 1.0 / p
        total += (fair - 1) * y - (1 - y)
    return total / len(labels)


def calibration_bins(probs: list[float], labels: list[int], n_bins: int = 10) -> list[tuple]:
    bins = [[] for _ in range(n_bins)]
    for p, y in zip(probs, labels):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    result = []
    for b in bins:
        if b:
            mean_p = sum(x[0] for x in b) / len(b)
            mean_y = sum(x[1] for x in b) / len(b)
            result.append((mean_p, mean_y, len(b)))
    return result


# ── Logistic regression (pure numpy-free) ────────────────────────────────────
def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + pow(2.718281828, -x))
    e = pow(2.718281828, x)
    return e / (1.0 + e)


def logistic_train(X: list[list[float]], y: list[int], lr: float = 0.01, epochs: int = 500) -> list[float]:
    n_feat = len(X[0])
    w = [0.0] * n_feat
    b = 0.0
    for _ in range(epochs):
        dw = [0.0] * n_feat
        db = 0.0
        for xi, yi in zip(X, y):
            pred = sigmoid(sum(w[j] * xi[j] for j in range(n_feat)) + b)
            err = pred - yi
            for j in range(n_feat):
                dw[j] += err * xi[j]
            db += err
        n = len(X)
        w = [w[j] - lr * dw[j] / n for j in range(n_feat)]
        b -= lr * db / n
    return w, b


def logistic_predict(X: list[list[float]], w: list[float], b: float) -> list[float]:
    return [sigmoid(sum(w[j] * xi[j] for j in range(len(w))) + b) for xi in X]


# ── sklearn models (optional, graceful fallback) ──────────────────────────────
def try_sklearn_models(X_train, y_train, X_test, y_test, feature_names: list[str]):
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        import numpy as np
    except ImportError:
        return {}

    X_tr = np.array(X_train)
    X_te = np.array(X_test)
    y_tr = np.array(y_train)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    results = {}

    lr = LogisticRegression(max_iter=1000)
    lr.fit(X_tr_s, y_tr)
    probs = lr.predict_proba(X_te_s)[:, 1].tolist()
    results["LogisticReg (sklearn)"] = {
        "probs": probs,
        "coefs": list(zip(feature_names, lr.coef_[0].tolist())),
    }

    gb = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    gb.fit(X_tr, y_tr)
    probs_gb = gb.predict_proba(X_te)[:, 1].tolist()
    results["GradientBoosting"] = {
        "probs": probs_gb,
        "importances": list(zip(feature_names, gb.feature_importances_.tolist())),
    }

    return results


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="Show calibration plot")
    parser.add_argument("--features", action="store_true", help="Print feature importances")
    parser.add_argument("--split", default="2025-01-01", help="Train/test split date")
    args = parser.parse_args()

    split_date = datetime.fromisoformat(args.split).replace(tzinfo=timezone.utc)

    print(f"\n{'='*65}")
    print(f"  CS2 COMBINED MODEL BACKTEST")
    print(f"{'='*65}\n")

    print("[1] Loading data...")
    rows = load_dataset()
    print(f"    {len(rows):,} BO3 matches  ({rows[0]['date'].date()} → {rows[-1]['date'].date()})")

    print("[2] Building rolling ELO + feature matrix...")
    features = build_rolling_features(rows)

    train = [f for f in features if f["date"] < split_date]
    test  = [f for f in features if f["date"] >= split_date]
    print(f"    Train: {len(train):,} matches (before {split_date.date()})")
    print(f"    Test:  {len(test):,} matches (from {split_date.date()})")

    y_test = [f["label"] for f in test]

    # ── Model A: ELO only ─────────────────────────────────────────────────────
    elo_probs = [f["elo_prob1"] for f in test]

    # ── Model B: Player quality only ─────────────────────────────────────────
    # Fit logistic regression on pq_diff → label using train set
    pq_X_train = [[f["pq_diff"]] for f in train]
    pq_y_train = [f["label"] for f in train]
    pq_w, pq_b = logistic_train(pq_X_train, pq_y_train, lr=0.05, epochs=800)
    pq_X_test = [[f["pq_diff"]] for f in test]
    pq_probs = logistic_predict(pq_X_test, pq_w, pq_b)

    # ── Model C: Combined logistic (ELO diff + PQ diff + is_lan) ─────────────
    feat_names = ["elo_diff", "pq_diff", "is_lan"]
    X_train = [[f["elo_diff"], f["pq_diff"], f["is_lan"]] for f in train]
    X_test  = [[f["elo_diff"], f["pq_diff"], f["is_lan"]] for f in test]
    y_train = [f["label"] for f in train]
    comb_w, comb_b = logistic_train(X_train, y_train, lr=0.005, epochs=1000)
    comb_probs = logistic_predict(X_test, comb_w, comb_b)

    # ── sklearn models ────────────────────────────────────────────────────────
    sklearn_results = try_sklearn_models(X_train, y_train, X_test, y_test, feat_names)

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  TEST SET RESULTS  ({len(test):,} matches, {split_date.date()} onwards)")
    print(f"{'='*65}")
    print(f"  {'Model':<35} {'Acc':>6}  {'LogLoss':>8}  {'Brier':>7}  {'ROI@fair':>9}")
    print(f"  {'-'*65}")

    models = [
        ("ELO only", elo_probs),
        ("Player quality only", pq_probs),
        ("Combined logistic (manual)", comb_probs),
    ]
    for name, probs in sklearn_results.items():
        models.append((name, probs["probs"]))

    best_acc = 0.0
    for name, probs in models:
        acc = accuracy(probs, y_test)
        ll  = log_loss(probs, y_test)
        bs  = brier(probs, y_test)
        roi = roi_at_fair(probs, y_test)
        best_acc = max(best_acc, acc)
        print(f"  {name:<35} {acc:>6.1%}  {ll:>8.4f}  {bs:>7.4f}  {roi:>+9.3f}")

    # ── Calibration analysis ──────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  CALIBRATION — ELO vs Combined (test set)")
    print(f"{'='*65}")
    print(f"  {'Bin (pred%)':<14} {'ELO actual':>12}  {'Comb actual':>13}  {'n':>5}")
    elo_cal   = calibration_bins(elo_probs, y_test, 10)
    comb_cal  = calibration_bins(comb_probs, y_test, 10)
    for (ep, ey, en), (cp, cy, cn) in zip(elo_cal, comb_cal):
        print(f"  {ep*100:4.0f}–{(ep+0.1)*100:4.0f}%         {ey:>10.1%}    {cy:>12.1%}  {en:>5}")

    # ── Feature weights ───────────────────────────────────────────────────────
    if args.features:
        print(f"\n{'='*65}")
        print(f"  FEATURE WEIGHTS — Combined logistic (manual)")
        print(f"{'='*65}")
        for name, w in zip(feat_names, comb_w):
            print(f"  {name:<20}  {w:+.4f}")

        for model_name, res in sklearn_results.items():
            if "coefs" in res:
                print(f"\n  {model_name} coefficients:")
                for fn, c in res["coefs"]:
                    print(f"    {fn:<20}  {c:+.4f}")
            if "importances" in res:
                print(f"\n  {model_name} feature importances:")
                for fn, imp in sorted(res["importances"], key=lambda x: -x[1]):
                    print(f"    {fn:<20}  {imp:.4f}")

    # ── Roster change signal analysis ─────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  INSIGHT: when does PQ add most over ELO?")
    print(f"{'='*65}")
    disagreements = [
        f for f in test
        if (f["pq_diff"] > 0) != (f["elo_diff"] > 0)   # models disagree on favourite
    ]
    agree = [f for f in test if f not in disagreements]

    def model_acc_on(subset):
        if not subset:
            return 0.0, 0.0
        elo_p = [f["elo_prob1"] for f in subset]
        comb_p = [f["elo_prob1"] * 0.5 + (0.5 + f["pq_diff"] * 2) * 0.5 for f in subset]
        labels = [f["label"] for f in subset]
        return accuracy(elo_p, labels), accuracy([max(0, min(1, p)) for p in comb_p], labels)

    elo_acc_a, comb_acc_a = model_acc_on(agree)
    elo_acc_d, comb_acc_d = model_acc_on(disagreements)
    print(f"  When ELO + PQ agree ({len(agree)} matches):")
    print(f"    ELO acc: {elo_acc_a:.1%}   Combined: {comb_acc_a:.1%}")
    print(f"  When ELO + PQ disagree ({len(disagreements)} matches):")
    print(f"    ELO acc: {elo_acc_d:.1%}   Combined: {comb_acc_d:.1%}")
    print(f"  → Disagreement rate: {len(disagreements)/len(test):.1%} of matches")

    if args.plot:
        try:
            import matplotlib.pyplot as plt
            fig, axes = plt.subplots(1, 2, figsize=(12, 5))
            for ax, (name, probs), cal in [
                (axes[0], ("ELO only", elo_probs), elo_cal),
                (axes[1], list(sklearn_results.items() or [("Combined logistic", comb_probs, None)])[0][:2],
                 calibration_bins(list(sklearn_results.values() or [{"probs": comb_probs}])[0].get("probs", comb_probs), y_test)),
            ]:
                ps = [c[0] for c in cal]
                ys = [c[1] for c in cal]
                ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="Perfect")
                ax.scatter(ps, ys, s=[c[2] * 2 for c in cal], zorder=5)
                ax.plot(ps, ys, alpha=0.6)
                ax.set_title(name)
                ax.set_xlabel("Predicted probability")
                ax.set_ylabel("Actual win rate")
                ax.legend()
            plt.tight_layout()
            plt.savefig("dev/active/cs2_calibration.png", dpi=150)
            print(f"\n  Calibration plot saved to dev/active/cs2_calibration.png")
        except ImportError:
            print("[!] matplotlib not installed — skipping plot")


if __name__ == "__main__":
    main()
