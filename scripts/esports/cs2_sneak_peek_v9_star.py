"""
CS2 sneak-peek v9-star — v8 + star_player features.

Three new features from cs2_hltv_team_rosters × cs2_hltv_top_players join:
  star_present_diff     — has-top-30 indicator diff (-1, 0, +1)
  top150_count_diff     — (team1 players in top-150) − (team2)
  roster_avg_rating_diff — avg Rating across each team's 5 players

Data already in DB — no new scraping. ~30s to load + score.

Run:
    python3 scripts/esports/cs2_sneak_peek_v9_star.py [--since 2025-06-01]
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
from cs2_sneak_peek_v5 import load_matches_with_features, load_team_map, _logit  # type: ignore
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_team_star_features() -> dict:
    """{team_name: {has_top30, top150_count, avg_rating}}.
    Joins latest roster snapshot × latest top_players period."""
    rows = execute_query("""
        WITH latest_rosters AS (
            SELECT DISTINCT ON (team_name, hltv_player_id)
                team_name, hltv_player_id
            FROM cs2_hltv_team_rosters
            ORDER BY team_name, hltv_player_id, snapshot_date DESC
        ),
        latest_top_players AS (
            SELECT DISTINCT ON (hltv_player_id)
                hltv_player_id, rank, rating
            FROM cs2_hltv_top_players
            WHERE period_end >= NOW() - INTERVAL '400 days'
            ORDER BY hltv_player_id, period_end DESC
        )
        SELECT lr.team_name,
               COUNT(tp.hltv_player_id)                                    AS resolved_players,
               COUNT(tp.hltv_player_id) FILTER (WHERE tp.rank <= 30)        AS in_top_30,
               COUNT(tp.hltv_player_id) FILTER (WHERE tp.rank <= 150)       AS in_top_150,
               AVG(tp.rating)                                               AS avg_rating
        FROM latest_rosters lr
        LEFT JOIN latest_top_players tp ON tp.hltv_player_id = lr.hltv_player_id
        GROUP BY lr.team_name
    """)
    out = {}
    for r in rows:
        out[r["team_name"]] = {
            "has_top30":   1 if (r["in_top_30"] or 0) > 0 else 0,
            "top150":      int(r["in_top_150"] or 0),
            "avg_rating":  float(r["avg_rating"]) if r["avg_rating"] is not None else None,
            "resolved":    int(r["resolved_players"] or 0),
        }
    return out


def build_rows(matches, tm, pistol, tier_map, kd_map, direct, star):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

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

        p1, p2 = pistol.get(m["team1"]), pistol.get(m["team2"])
        pistol_diff = 0.0
        if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0

        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_s = 1.0 if tier == "s" else 0.0
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0
        tier_c = 1.0 if tier == "c" else 0.0
        tier_d = 1.0 if tier == "d" else 0.0

        d1 = direct.get((m["team1"] or "").lower())
        d2 = direct.get((m["team2"] or "").lower())
        t1_kd = kd_map.get(m["team1"]) or (d1["kd"] if d1 and d1.get("maps", 0) >= 30 else None)
        t2_kd = kd_map.get(m["team2"]) or (d2["kd"] if d2 and d2.get("maps", 0) >= 30 else None)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        # NEW v9-star
        s1 = star.get(m["team1"])
        s2 = star.get(m["team2"])
        star_present_diff = 0.0
        top150_count_diff = 0.0
        roster_avg_rating_diff = 0.0
        star_covered = 0
        if s1 and s2:
            star_present_diff = float(s1["has_top30"] - s2["has_top30"])
            top150_count_diff = (s1["top150"] - s2["top150"]) / 5.0  # normalised to [-1, +1]
            if s1["avg_rating"] is not None and s2["avg_rating"] is not None:
                roster_avg_rating_diff = s1["avg_rating"] - s2["avg_rating"]
                star_covered = 1

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            "star_present_diff": star_present_diff,
            "top150_count_diff": top150_count_diff,
            "roster_avg_rating_diff": roster_avg_rating_diff,
            "star_covered": star_covered,
        })
    return out


def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
    }


def evaluate(rows, keys, name):
    cut = int(len(rows) * 0.7)
    if cut < 50:
        return {"skipped": True, "n": len(rows)}
    full_keys = ["logit_saved"] + keys
    X = np.array([[r[k] for k in full_keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=2000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(full_keys, m.coef_[0].tolist())),
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

    print("loading data…")
    tm = load_team_map()
    pistol = load_pistol_map()
    tier_map = load_tier_map()
    kd_map = load_team_kd_map()
    direct = load_team_stats_direct()
    star = load_team_star_features()
    print(f"  team_map: {len(tm)}, kd_map: {len(kd_map)}, "
          f"direct: {len(direct)}, star: {len(star)} teams")

    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct, star)
    print(f"  {len(rows)} matches\n")

    cov_star = sum(1 for r in rows if r["star_covered"])
    cov_any = sum(1 for r in rows if r["star_present_diff"] != 0 or r["top150_count_diff"] != 0)
    print(f"  coverage:")
    print(f"    star_covered (both teams have avg_rating): {cov_star}/{len(rows)} ({cov_star/len(rows):.1%})")
    print(f"    any star signal:                            {cov_any}/{len(rows)} ({cov_any/len(rows):.1%})\n")

    v8_keys = ["form_diff","h2h_diff","tm_diff","rest_diff","rank_diff","bo_centered",
               "pistol_diff","tier_s","tier_a","tier_b","tier_c","tier_d","kd_diff"]

    def run_battery(sample, label):
        if len(sample) < 80:
            print(f"  [skip] {label}: only {len(sample)}")
            return
        cut = int(len(sample) * 0.7)
        y_te = np.array([r["y"] for r in sample[cut:]], dtype=int)
        p_base = np.array([r["saved"] for r in sample[cut:]], dtype=float)
        m_base = _metrics(y_te, p_base)
        print(f"\n--- {label} (n={len(sample)}, test={len(sample)-cut}) ---")
        print(f"{'set':40} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 72)
        print(f"{'baseline (hltv_v1 direct)':40} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
        persist(f"v9s_{label}_baseline", len(sample), m_base, since_d, keys=["win_prob1"], n_train=cut)

        for keys, lbl in [
            (v8_keys, "v8 reference"),
            (v8_keys + ["star_present_diff"], "v9s: v8 + star_present"),
            (v8_keys + ["top150_count_diff"], "v9s: v8 + top150_count"),
            (v8_keys + ["roster_avg_rating_diff"], "v9s: v8 + avg_rating"),
            (v8_keys + ["star_present_diff","top150_count_diff","roster_avg_rating_diff"], "v9s ALL"),
            (["star_present_diff","top150_count_diff","roster_avg_rating_diff"], "star features alone"),
        ]:
            r = evaluate(sample, keys, lbl)
            if r.get("skipped"):
                print(f"{lbl:40}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{lbl:40} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v9s_{label}_{lbl}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    run_battery(rows, "full")
    run_battery([r for r in rows if r["star_covered"]], "star-covered")


if __name__ == "__main__":
    main()
