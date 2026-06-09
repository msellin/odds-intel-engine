"""
CS2 sneak-peek v8 — v7 features + team_kd_diff.

v7 added pistol + tier (covered most matches). v6 added K/D (covered ~10%
of matches after roster expansion). v8 stacks both: v5_best + pistol +
tier + K/D. Tests two modes:

  (A) Full sample (3,100+ matches) — K/D null-fills to 0 where uncovered,
      measures aggregate AUC including unseen-K/D rows.
  (B) K/D-covered subset only — measures the lift when K/D is actually
      present. This is the upper-bound on what K/D adds.

If (B) >> v7 ALL on the same subset, K/D is worth promoting to v8 production.

Run:
    python3 scripts/esports/cs2_sneak_peek_v8.py [--since 2025-06-01]
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


def load_team_stats_direct() -> dict:
    """{team_name_lower: {kd, rating_3, pistol_pct, ct_pistol_pct, t_pistol_pct}}
    from latest period in cs2_hltv_team_stats. This is the new bulk-page
    data — covers ~150-200 teams directly without roster aggregation.

    Returns empty dict if migration 227 hasn't been applied or table empty —
    sneak peek then degrades to the pure-roster v6/v7 path.
    """
    try:
        rows = execute_query("""
            SELECT DISTINCT ON (team_name)
                team_name, kd, rating_3, pistol_pct, ct_pistol_pct, t_pistol_pct, maps
            FROM cs2_hltv_team_stats
            WHERE period_end >= NOW() - INTERVAL '400 days'
            ORDER BY team_name, period_end DESC
        """)
    except Exception as e:
        print(f"  [direct team stats unavailable: {e}]")
        return {}
    out: dict = {}
    for r in rows:
        if not r["team_name"]:
            continue
        out[r["team_name"].lower()] = {
            "kd": float(r["kd"]) if r["kd"] is not None else None,
            "rating_3": float(r["rating_3"]) if r["rating_3"] is not None else None,
            "pistol_pct": float(r["pistol_pct"]) if r["pistol_pct"] is not None else None,
            "ct_pistol_pct": float(r["ct_pistol_pct"]) if r["ct_pistol_pct"] is not None else None,
            "t_pistol_pct": float(r["t_pistol_pct"]) if r["t_pistol_pct"] is not None else None,
            "maps": int(r["maps"] or 0),
        }
    print(f"  direct team stats loaded: {len(out)} teams")
    return out


def _kd_with_fallback(team: str, kd_map: dict, direct: dict) -> float | None:
    """Roster-aggregated K/D first (more accurate when both 5 players known),
    falling back to direct /stats/teams K/D when roster has <3 resolved players.
    """
    v = kd_map.get(team)
    if v is not None:
        return v
    d = direct.get((team or "").lower())
    if d and d.get("kd") is not None and d.get("maps", 0) >= 30:
        return d["kd"]
    return None


def _pistol_with_fallback(team: str, pistol_map: dict, direct: dict) -> dict | None:
    """Per-team pistol page (26 teams, denser stats) first, falling back to
    bulk /stats/teams/pistols (~150 teams)."""
    v = pistol_map.get(team)
    if v and v.get("n", 0) >= 50:
        return {"overall": v["overall"], "ct": v.get("ct"), "t": v.get("t"), "n": v["n"]}
    d = direct.get((team or "").lower())
    if d and d.get("pistol_pct") is not None and d.get("maps", 0) >= 30:
        return {
            "overall": d["pistol_pct"],
            "ct": d.get("ct_pistol_pct"),
            "t": d.get("t_pistol_pct"),
            "n": d.get("maps", 0) * 2,   # 2 pistols per map
        }
    return None


def build_rows(matches, tm, pistol, tier_map, kd_map, direct=None):
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

        # v7: pistol diff (with v8 direct-team-stats fallback)
        p1 = _pistol_with_fallback(m["team1"], pistol, direct or {})
        p2 = _pistol_with_fallback(m["team2"], pistol, direct or {})
        pistol_diff = 0.0
        if p1 and p2:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0

        # v7: tournament tier
        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_s = 1.0 if tier == "s" else 0.0
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0
        tier_c = 1.0 if tier == "c" else 0.0
        tier_d = 1.0 if tier == "d" else 0.0

        # v8 NEW: team K/D diff (with direct-team-stats fallback)
        t1_kd = _kd_with_fallback(m["team1"], kd_map, direct or {})
        t2_kd = _kd_with_fallback(m["team2"], kd_map, direct or {})
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0
        kd_covered = 1 if (t1_kd is not None and t2_kd is not None) else 0

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
            "kd_covered": kd_covered,
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
    tm = load_team_map(); print(f"  {len(tm)} teams")
    print("loading pistol stats…")
    pistol = load_pistol_map(); print(f"  {len(pistol)} teams with pistol data")
    print("loading tournament tier map…")
    tier_map = load_tier_map(); print(f"  {len(tier_map) // 2} unique matches with tier")
    print("loading team K/D map…")
    kd_map = load_team_kd_map()

    print("loading direct team stats (HLTV bulk page)…")
    direct = load_team_stats_direct()

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct=direct)
    print(f"  {len(rows)} matches with saved_prob\n")

    pistol_n = sum(1 for r in rows if r["pistol_diff"] != 0.0)
    tier_n = sum(1 for r in rows if any(r[k] for k in ("tier_s","tier_a","tier_b","tier_c","tier_d")))
    kd_n = sum(1 for r in rows if r["kd_covered"])
    print("  coverage:")
    print(f"    pistol_diff: {pistol_n}/{len(rows)} ({pistol_n/len(rows):.1%})")
    print(f"    any tier:    {tier_n}/{len(rows)} ({tier_n/len(rows):.1%})")
    print(f"    kd_diff:     {kd_n}/{len(rows)} ({kd_n/len(rows):.1%})\n")

    v5_keys = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered"]
    v7_all_keys = v5_keys + ["pistol_diff", "tier_s", "tier_a", "tier_b", "tier_c", "tier_d"]

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
        persist(f"{label_prefix}_baseline", len(sample_rows), m_base, since_d, keys=["win_prob1"], n_train=cut)

        for keys, label in [
            (v7_all_keys, "v7 ALL (no kd) — reference"),
            (v7_all_keys + ["kd_diff"], "v8 = v7 ALL + kd_diff"),
            (["kd_diff"], "kd_diff alone"),
            (v5_keys + ["kd_diff"], "v5-best + kd (v6-style)"),
        ]:
            r = evaluate_stack(sample_rows, keys, f"{label_prefix} :: {label}")
            if r.get("skipped"):
                print(f"{label:40}  (skipped)")
                continue
            mm = r["metrics"]
            delta = mm["auc"] - m_base["auc"] if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{label:40} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"{label_prefix}_{label}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    # (A) full sample
    run_battery(rows, "full")
    # (B) kd-covered subset
    covered = [r for r in rows if r["kd_covered"]]
    run_battery(covered, "kd-covered")


if __name__ == "__main__":
    main()
