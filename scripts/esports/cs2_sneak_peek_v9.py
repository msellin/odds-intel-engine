"""
CS2 sneak-peek v9 — adds side-split pistol + R2 economy features to v8.

v9 features over v8:
  pistol_ct_diff   — CT-side pistol win % diff
  pistol_t_diff    — T-side pistol win % diff
  r2_conv_diff     — Round-2 conversion % diff (won the pistol → won R2)
  r2_break_diff    — Round-2 break % diff (lost the pistol → broke R2)

All four come from /stats/teams/pistols bulk page (already scraped into
cs2_hltv_team_stats). No new scraping required — pure feature engineering
on data we already have.

Run:
    python3 scripts/esports/cs2_sneak_peek_v9.py [--since 2025-06-01]
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
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_team_stats_direct_v9() -> dict:
    """{team_name_lower: {kd, ct_pct, t_pct, r2_conv, r2_break, maps}}
    Extended from v8's loader — also pulls r2_conv_pct + r2_break_pct."""
    try:
        rows = execute_query("""
            SELECT DISTINCT ON (team_name)
                team_name, kd, ct_pistol_pct, t_pistol_pct,
                r2_conv_pct, r2_break_pct, pistol_pct, maps
            FROM cs2_hltv_team_stats
            WHERE period_end >= NOW() - INTERVAL '400 days'
            ORDER BY team_name, period_end DESC
        """)
    except Exception as e:
        print(f"  [direct team stats unavailable: {e}]")
        return {}
    out = {}
    for r in rows:
        if not r["team_name"]:
            continue
        out[r["team_name"].lower()] = {
            "kd": float(r["kd"]) if r["kd"] is not None else None,
            "pistol_pct": float(r["pistol_pct"]) if r["pistol_pct"] is not None else None,
            "ct_pct": float(r["ct_pistol_pct"]) if r["ct_pistol_pct"] is not None else None,
            "t_pct":  float(r["t_pistol_pct"])  if r["t_pistol_pct"]  is not None else None,
            "r2_conv": float(r["r2_conv_pct"])  if r["r2_conv_pct"]   is not None else None,
            "r2_break": float(r["r2_break_pct"]) if r["r2_break_pct"] is not None else None,
            "maps": int(r["maps"] or 0),
        }
    print(f"  direct team stats loaded: {len(out)} teams")
    return out


def build_rows(matches, tm, pistol_map, tier_map, kd_map, direct):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # v5 base
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

        # v7: pistol overall with v8 fallback to direct
        d1 = direct.get((m["team1"] or "").lower())
        d2 = direct.get((m["team2"] or "").lower())
        p1, p2 = pistol_map.get(m["team1"]), pistol_map.get(m["team2"])

        pistol_diff = 0.0
        if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0
        elif d1 and d2 and d1.get("pistol_pct") and d2.get("pistol_pct"):
            pistol_diff = (d1["pistol_pct"] - d2["pistol_pct"]) / 100.0

        # v7: tournament tier
        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_s = 1.0 if tier == "s" else 0.0
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0
        tier_c = 1.0 if tier == "c" else 0.0
        tier_d = 1.0 if tier == "d" else 0.0

        # v8: kd_diff with direct fallback
        t1_kd = kd_map.get(m["team1"])
        if t1_kd is None and d1 and d1.get("kd") is not None and d1.get("maps", 0) >= 30:
            t1_kd = d1["kd"]
        t2_kd = kd_map.get(m["team2"])
        if t2_kd is None and d2 and d2.get("kd") is not None and d2.get("maps", 0) >= 30:
            t2_kd = d2["kd"]
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0
        kd_covered = 1 if (t1_kd is not None and t2_kd is not None) else 0

        # NEW v9 features
        def _dd(a, b, key):
            if d1 and d2 and d1.get(key) is not None and d2.get(key) is not None:
                return (d1[key] - d2[key]) / 100.0
            return 0.0

        pistol_ct_diff = _dd(d1, d2, "ct_pct")
        pistol_t_diff  = _dd(d1, d2, "t_pct")
        r2_conv_diff   = _dd(d1, d2, "r2_conv")
        r2_break_diff  = _dd(d1, d2, "r2_break")
        v9_covered = 1 if (d1 and d2 and d1.get("ct_pct") is not None and d2.get("ct_pct") is not None) else 0

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff, "kd_covered": kd_covered,
            # NEW v9
            "pistol_ct_diff": pistol_ct_diff,
            "pistol_t_diff":  pistol_t_diff,
            "r2_conv_diff":   r2_conv_diff,
            "r2_break_diff":  r2_break_diff,
            "v9_covered":     v9_covered,
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

    print("loading team_map…");   tm = load_team_map();             print(f"  {len(tm)} teams")
    print("loading pistol stats…"); pistol = load_pistol_map();      print(f"  {len(pistol)} teams")
    print("loading tier map…");   tier_map = load_tier_map();        print(f"  {len(tier_map) // 2} matches")
    print("loading kd_map…");     kd_map = load_team_kd_map()
    print("loading direct stats…"); direct = load_team_stats_direct_v9()

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct)
    print(f"  {len(rows)} matches with saved_prob\n")

    cov_kd = sum(1 for r in rows if r["kd_covered"])
    cov_v9 = sum(1 for r in rows if r["v9_covered"])
    cov_pistol = sum(1 for r in rows if r["pistol_diff"] != 0.0)
    print(f"  coverage:")
    print(f"    pistol_diff:                {cov_pistol}/{len(rows)} ({cov_pistol/len(rows):.1%})")
    print(f"    kd_diff:                    {cov_kd}/{len(rows)} ({cov_kd/len(rows):.1%})")
    print(f"    v9 NEW (ct/t/r2 covered):   {cov_v9}/{len(rows)} ({cov_v9/len(rows):.1%})\n")

    v8_keys = ["form_diff","h2h_diff","tm_diff","rest_diff","rank_diff","bo_centered",
               "pistol_diff","tier_s","tier_a","tier_b","tier_c","tier_d","kd_diff"]
    v9_keys = v8_keys + ["pistol_ct_diff","pistol_t_diff","r2_conv_diff","r2_break_diff"]

    def run_battery(sample_rows, label_prefix):
        if len(sample_rows) < 80:
            print(f"  [skip] {label_prefix}: only {len(sample_rows)} rows")
            return
        cut = int(len(sample_rows) * 0.7)
        y_te = np.array([r["y"] for r in sample_rows[cut:]], dtype=int)
        p_base = np.array([r["saved"] for r in sample_rows[cut:]], dtype=float)
        m_base = _metrics(y_te, p_base)
        print(f"\n--- {label_prefix} (n={len(sample_rows)}, test={len(sample_rows)-cut}) ---")
        print(f"{'set':40} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 72)
        print(f"{'baseline (hltv_v1 direct)':40} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
        persist(f"v9_{label_prefix}_baseline", len(sample_rows), m_base, since_d, keys=["win_prob1"], n_train=cut)

        for keys, label in [
            (v8_keys, "v8 reference (kd_diff)"),
            (v8_keys + ["pistol_ct_diff","pistol_t_diff"], "v9a: + CT/T splits"),
            (v8_keys + ["r2_conv_diff","r2_break_diff"], "v9b: + R2 conv/break"),
            (v9_keys, "v9 ALL (ct+t+r2)"),
        ]:
            r = evaluate(sample_rows, keys, label)
            if r.get("skipped"):
                print(f"{label:40}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{label:40} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v9_{label_prefix}_{label}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    # Full sample
    run_battery(rows, "full")
    # v9-covered subset (both teams have CT/T/R2 splits)
    covered = [r for r in rows if r["v9_covered"]]
    run_battery(covered, "v9-covered")


if __name__ == "__main__":
    main()
