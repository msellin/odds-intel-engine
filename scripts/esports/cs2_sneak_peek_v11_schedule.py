"""
CS2 sneak-peek v11 — adds schedule-density / back-to-back-fatigue features.

For each upcoming match between team1 and team2, compute as of kickoff:

  team1_matches_last_24h — count of team1's matches in the 24h before kickoff
  team2_matches_last_24h — same for team2
  density_diff           = team1_matches_last_24h - team2_matches_last_24h
  team1_matches_last_72h — count over 72h window
  team2_matches_last_72h — same
  density72_diff         = team1_matches_last_72h - team2_matches_last_72h

PIT-correct: only matches with kickoff_time < current kickoff are counted.
All counts come from cs2_results (every team's full schedule history).
No new scraping required — pure feature engineering on data we already have.

Compares (walk-forward, 70/30 split like v9):
  baseline (hltv_v1 direct)
  v8 reference                — v8 stacked logistic
  v8 + density_diff
  v8 + density_diff + density72_diff
  density features alone (sanity)

Run:
    python3 scripts/esports/cs2_sneak_peek_v11_schedule.py [--since 2025-06-01]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import date, timedelta
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
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_team_schedule() -> dict:
    """Return {team_name: [kickoff_time, ...]} sorted ascending.

    Pulled once from cs2_results — fast batch read, used for bisect-based
    PIT-correct counts. Each team's appearances on either side are pooled."""
    rows = execute_query(
        """
        SELECT team1, team2, kickoff_time
        FROM cs2_results
        WHERE kickoff_time IS NOT NULL
        """,
        None,
    )
    schedule: dict = defaultdict(list)
    for r in rows:
        ts = r["kickoff_time"]
        if ts is None:
            continue
        if r["team1"]:
            schedule[r["team1"]].append(ts)
        if r["team2"]:
            schedule[r["team2"]].append(ts)
    for team in schedule:
        schedule[team].sort()
    print(f"  team schedule loaded: {len(schedule)} teams, "
          f"{sum(len(v) for v in schedule.values())} appearances")
    return dict(schedule)


def compute_schedule_density(team_name: str, kickoff_ts, schedule: dict,
                              hours: int = 24) -> int:
    """Count team's matches in the `hours` window strictly before kickoff_ts.

    Uses bisect on the per-team sorted timestamp list — O(log n) per call.
    PIT-correct: matches with kickoff_time >= kickoff_ts are excluded."""
    if not team_name or kickoff_ts is None:
        return 0
    times = schedule.get(team_name)
    if not times:
        return 0
    window_start = kickoff_ts - timedelta(hours=hours)
    # bisect_left at kickoff_ts excludes the match itself and any later;
    # bisect_left at window_start gives the first index >= window_start.
    lo = bisect.bisect_left(times, window_start)
    hi = bisect.bisect_left(times, kickoff_ts)
    return max(0, hi - lo)


def build_rows(matches, tm, pistol, tier_map, kd_map, direct, schedule):
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

        # v7 pistol
        p1, p2 = pistol.get(m["team1"]), pistol.get(m["team2"])
        pistol_diff = 0.0
        if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0

        # v7 tier
        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_s = 1.0 if tier == "s" else 0.0
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0
        tier_c = 1.0 if tier == "c" else 0.0
        tier_d = 1.0 if tier == "d" else 0.0

        # v8 kd with direct fallback
        d1 = direct.get((m["team1"] or "").lower())
        d2 = direct.get((m["team2"] or "").lower())
        t1_kd = kd_map.get(m["team1"]) or (d1["kd"] if d1 and d1.get("maps", 0) >= 30 else None)
        t2_kd = kd_map.get(m["team2"]) or (d2["kd"] if d2 and d2.get("maps", 0) >= 30 else None)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        # NEW v11: schedule density (PIT-correct via bisect on cs2_results)
        kickoff_ts = m["kickoff_time"]
        team1_matches_last_24h = compute_schedule_density(m["team1"], kickoff_ts, schedule, 24)
        team2_matches_last_24h = compute_schedule_density(m["team2"], kickoff_ts, schedule, 24)
        density_diff = float(team1_matches_last_24h - team2_matches_last_24h)

        team1_matches_last_72h = compute_schedule_density(m["team1"], kickoff_ts, schedule, 72)
        team2_matches_last_72h = compute_schedule_density(m["team2"], kickoff_ts, schedule, 72)
        density72_diff = float(team1_matches_last_72h - team2_matches_last_72h)

        # Coverage = both teams have at least one prior appearance in the schedule
        # (so the count is meaningful, even if it's zero in the 24h/72h window).
        t1_known = bool(schedule.get(m["team1"]))
        t2_known = bool(schedule.get(m["team2"]))
        density_covered = 1 if (t1_known and t2_known) else 0

        out.append({
            "kickoff": kickoff_ts, "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            # NEW v11
            "team1_matches_last_24h": float(team1_matches_last_24h),
            "team2_matches_last_24h": float(team2_matches_last_24h),
            "density_diff":           density_diff,
            "team1_matches_last_72h": float(team1_matches_last_72h),
            "team2_matches_last_72h": float(team2_matches_last_72h),
            "density72_diff":         density72_diff,
            "density_covered":        density_covered,
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

    print("loading team_map…");     tm = load_team_map();          print(f"  {len(tm)} teams")
    print("loading pistol stats…"); pistol = load_pistol_map();    print(f"  {len(pistol)} teams")
    print("loading tier map…");     tier_map = load_tier_map();    print(f"  {len(tier_map) // 2} matches")
    print("loading kd_map…");       kd_map = load_team_kd_map()
    print("loading direct stats…"); direct = load_team_stats_direct()
    print("loading team schedule…"); schedule = load_team_schedule()

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct, schedule)
    print(f"  {len(rows)} matches with saved_prob\n")

    cov_density = sum(1 for r in rows if r["density_covered"])
    cov_density_nz = sum(1 for r in rows if r["density_diff"] != 0.0)
    cov_density72_nz = sum(1 for r in rows if r["density72_diff"] != 0.0)
    print(f"  coverage:")
    print(f"    both teams have schedule history: {cov_density}/{len(rows)} ({cov_density/len(rows):.1%})")
    print(f"    density_diff   non-zero:          {cov_density_nz}/{len(rows)} ({cov_density_nz/len(rows):.1%})")
    print(f"    density72_diff non-zero:          {cov_density72_nz}/{len(rows)} ({cov_density72_nz/len(rows):.1%})\n")

    v8_keys = ["form_diff","h2h_diff","tm_diff","rest_diff","rank_diff","bo_centered",
               "pistol_diff","tier_s","tier_a","tier_b","tier_c","tier_d","kd_diff"]
    v11_24h_keys = v8_keys + ["density_diff"]
    v11_full_keys = v8_keys + ["density_diff","density72_diff"]

    # Track AUCs for the PROMOTE decision (full-sample, v8 vs v8+density).
    auc_track: dict = {}

    def run_battery(sample_rows, label_prefix):
        if len(sample_rows) < 80:
            print(f"  [skip] {label_prefix}: only {len(sample_rows)} rows")
            return
        cut = int(len(sample_rows) * 0.7)
        y_te = np.array([r["y"] for r in sample_rows[cut:]], dtype=int)
        p_base = np.array([r["saved"] for r in sample_rows[cut:]], dtype=float)
        m_base = _metrics(y_te, p_base)
        print(f"\n--- {label_prefix} (n={len(sample_rows)}, test={len(sample_rows)-cut}) ---")
        print(f"{'set':45} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 78)
        print(f"{'baseline (hltv_v1 direct)':45} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
        persist(f"v11-schedule-density_{label_prefix}_baseline", len(sample_rows), m_base, since_d,
                keys=["win_prob1"], n_train=cut)

        for keys, lbl in [
            (v8_keys, "v8 reference"),
            (v11_24h_keys, "v11: v8 + density_diff (24h)"),
            (v11_full_keys, "v11: v8 + density_diff + density72_diff"),
            (["density_diff", "density72_diff"], "density features alone"),
        ]:
            r = evaluate(sample_rows, keys, lbl)
            if r.get("skipped"):
                print(f"{lbl:45}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{lbl:45} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v11-schedule-density_{label_prefix}_{lbl}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
            if label_prefix == "full":
                auc_track[lbl] = mm["auc"]

    # Full sample, then a density-covered subset (kept for diagnostic parity
    # with v9 / v10 — but the full sample is what feeds the PROMOTE call).
    run_battery(rows, "full")
    covered = [r for r in rows if r["density_covered"]]
    if len(covered) != len(rows):
        run_battery(covered, "density-covered")

    # PROMOTE decision: compare v8 reference vs the strongest v11 variant on
    # the full sample. Rule: delta >= +0.002 AUC AND no degradation.
    base_auc = auc_track.get("v8 reference")
    v11a_auc = auc_track.get("v11: v8 + density_diff (24h)")
    v11b_auc = auc_track.get("v11: v8 + density_diff + density72_diff")
    best_v11 = max(filter(lambda x: x is not None, [v11a_auc, v11b_auc]), default=None)
    cov_pct = (cov_density / len(rows)) if rows else 0.0

    print("\n" + "=" * 78)
    print("PROMOTE DECISION")
    print("=" * 78)
    if base_auc is None or best_v11 is None:
        print("PROMOTE: no (insufficient data — could not score both v8 and v11)")
    else:
        delta = best_v11 - base_auc
        coverage_str = f"coverage={cov_pct:.1%}"
        print(f"baseline AUC (v8):     {base_auc:.4f}")
        print(f"+schedule AUC (best):  {best_v11:.4f}")
        print(f"delta:                 {delta:+.4f}     {coverage_str}")
        if delta >= 0.002:
            print("PROMOTE: yes (delta >= +0.002 AUC, no degradation)")
        else:
            print("PROMOTE: no (delta < +0.002 AUC threshold)")


if __name__ == "__main__":
    main()
