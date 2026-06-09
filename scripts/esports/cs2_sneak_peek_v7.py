"""
CS2 sneak-peek v7 — pistol round + tournament tier + starting-side.

Builds on v5 best (v4-ALL + bo). Three new feature signals from research:

  pistol_diff       — pistol round win-pct diff between teams (HLTV)
  tier_a / tier_b   — PandaScore-curated tournament tier (a/b/c/d)
  starting_side_diff — which team starts on which side per map (HLTV match details)

Coverage may be partial — each feature null-filled to 0 so the full
3,106-match sample is preserved. Per-feature coverage diagnostics printed.

Run:
    python3 scripts/esports/cs2_sneak_peek_v7.py [--since 2025-06-01]
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
from cs2_sneak_peek_v5 import (  # type: ignore
    load_matches_with_features, load_team_map, _logit,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_pistol_map() -> dict[str, dict]:
    """{team_name: {overall: pct, ct: pct, t: pct}} from latest snapshot per team."""
    rows = execute_query("""
        SELECT DISTINCT ON (team_name)
            team_name, pistol_win_pct, ct_pistol_win_pct, t_pistol_win_pct, pistols_played
        FROM cs2_team_pistol_stats
        ORDER BY team_name, snapshot_date DESC
    """)
    out = {}
    for r in rows:
        if r["pistol_win_pct"] is None: continue
        out[r["team_name"]] = {
            "overall": float(r["pistol_win_pct"]),
            "ct":      float(r["ct_pistol_win_pct"]) if r["ct_pistol_win_pct"] is not None else None,
            "t":       float(r["t_pistol_win_pct"]) if r["t_pistol_win_pct"] is not None else None,
            "n":       int(r["pistols_played"] or 0),
        }
    return out


def load_tier_map() -> dict:
    """{(team1_name, team2_name, kickoff_date): tier_letter}. Fuzzy-match later."""
    rows = execute_query("""
        SELECT team1_name, team2_name, begin_at::date AS kdate, tournament_tier
        FROM cs2_pandascore_matches
        WHERE tournament_tier IS NOT NULL AND status = 'finished'
    """)
    out = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["kdate"])
        out[key] = r["tournament_tier"]
        # Also store reverse pairing
        out[(r["team2_name"], r["team1_name"], r["kdate"])] = r["tournament_tier"]
    return out


def build_rows(matches, tm, pistol, tier_map):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # v5 base features
        t1f = float(m["t1_form"]) if m["t1_form_n"] >= 3 else 0.5
        t2f = float(m["t2_form"]) if m["t2_form_n"] >= 3 else 0.5
        form_diff = t1f - t2f
        h2h_diff = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0
        rest_diff = (min(float(m["t1_days_since"]), 30.0) - min(float(m["t2_days_since"]), 30.0)) / 30.0
        rank_diff = (
            float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0
            if (m["hltv_rank1"] and m["hltv_rank2"]) else 0.0
        )
        t1_tm, t2_tm = tm.get(m["team1"]), tm.get(m["team2"])
        tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0
        bo_centered = float((m["best_of"] or 3) - 3)

        # NEW v7: pistol diff
        p1 = pistol.get(m["team1"])
        p2 = pistol.get(m["team2"])
        pistol_diff = 0.0
        pistol_ct_diff = 0.0
        pistol_t_diff = 0.0
        if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0
            if p1["ct"] is not None and p2["ct"] is not None:
                pistol_ct_diff = (p1["ct"] - p2["ct"]) / 100.0
            if p1["t"] is not None and p2["t"] is not None:
                pistol_t_diff = (p1["t"] - p2["t"]) / 100.0

        # NEW v7: tournament tier (one-hot for a/b)
        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff,
            "h2h_diff": h2h_diff,
            "rest_diff": rest_diff,
            "rank_diff": rank_diff,
            "tm_diff": tm_diff,
            "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "pistol_ct_diff": pistol_ct_diff,
            "pistol_t_diff": pistol_t_diff,
            "tier_a": tier_a,
            "tier_b": tier_b,
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
    cut = int(len(rows) * 0.7)
    if cut < 50:
        return {"skipped": True, "n": len(rows)}
    keys = ["logit_saved"] + extra_keys
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=2000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(keys, m.coef_[0].tolist())),
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

    print("loading pistol stats…")
    pistol = load_pistol_map()
    print(f"  {len(pistol)} teams with pistol data")

    print("loading tournament tier map…")
    tier_map = load_tier_map()
    print(f"  {len(tier_map) // 2} unique matches with tier")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map)
    print(f"  {len(rows)} matches with saved_prob")
    print()

    # Coverage diagnostics
    pistol_covered = sum(1 for r in rows if r["pistol_diff"] != 0.0)
    tier_a_covered = sum(1 for r in rows if r["tier_a"] != 0.0)
    tier_b_covered = sum(1 for r in rows if r["tier_b"] != 0.0)
    print(f"  coverage:")
    print(f"    pistol_diff: {pistol_covered}/{len(rows)} ({pistol_covered/len(rows):.1%})")
    print(f"    tier=a:      {tier_a_covered}/{len(rows)} ({tier_a_covered/len(rows):.1%})")
    print(f"    tier=b:      {tier_b_covered}/{len(rows)} ({tier_b_covered/len(rows):.1%})")
    print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"{'set':38} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 72)
    print(f"{'baseline (hltv_v1 direct)':38} {len(rows):>5} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v7_baseline_hltv_v1", len(rows), m_base, since_d, keys=["win_prob1"], n_train=cut)

    v5_keys = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered"]
    for keys, label in [
        (v5_keys, "v7 v5-best (regression)"),
        (["pistol_diff"], "v7 + pistol alone"),
        (["pistol_diff", "pistol_ct_diff", "pistol_t_diff"], "v7 + pistol (3-way)"),
        (["tier_a", "tier_b"], "v7 + tier"),
        (v5_keys + ["pistol_diff"], "v7 v5-best + pistol"),
        (v5_keys + ["pistol_diff", "tier_a", "tier_b"], "v7 v5-best + pistol + tier"),
        (v5_keys + ["pistol_diff", "pistol_ct_diff", "pistol_t_diff", "tier_a", "tier_b"], "v7 ALL"),
    ]:
        r = evaluate_stack(rows, keys, label)
        if r.get("skipped"):
            print(f"{label:38} {r['n']:>5}  (skipped)")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{label:38} {r['n']:>5} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(label, r["n"], mm, since_d, keys=["logit_saved"] + keys,
                coefs=r["coefs"], n_train=r.get("n_train"))


if __name__ == "__main__":
    main()
