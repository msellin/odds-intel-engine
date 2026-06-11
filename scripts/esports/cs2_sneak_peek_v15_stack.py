"""
CS2 sneak-peek v15-stack — stack ALL v10-PIT + v13 + v14 features on top of v8.

Background: v10/v13/v14 each add 2–3 features and post small individual AUC
deltas. If the features are orthogonal (veto/decider, side, LAN/region all
measure independent phenomena), stacking them should add roughly. If they're
collinear, the joint delta will be smaller than the sum.

This script reuses the feature loaders + builders from each individual sneak
peek so the math stays consistent — only the harness combines them. The bo3gg
↔ HLTV join is resolved once via `cs2_match_id_bridge` (the brittle
exact-string join that capped older sneak peeks at <3% coverage is gone).

Persists to cs2_model_backtest_history under feature_set='v15-stack-all_*'.

Run:
    python3 scripts/esports/cs2_sneak_peek_v15_stack.py [--since 2025-06-01]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
# Reuse loaders from v5/v6/v7/v8 base
from cs2_sneak_peek_v5 import load_matches_with_features, load_team_map, _logit  # type: ignore  # noqa: E402
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore  # noqa: E402

# Reuse the strict PIT-correct v10 feature builders + map streams
from cs2_sneak_peek_v10_veto import (  # type: ignore  # noqa: E402
    build_pit_team_map_streams,
    derive_match_veto_features,
    load_map_winrates,
    load_veto_records,
    per_team_permaban_freq,
)

# v13: starting-side history + match index + feature helpers
from cs2_sneak_peek_v13_starting_side import (  # type: ignore  # noqa: E402
    _norm_team as _norm_v13,
    compute_bias_aligned_diff,
    compute_ct_start_winrate,
    load_hltv_match_index,
    load_starting_side_history,
)

# v14: LAN streams + region detector
from cs2_sneak_peek_v14_lan_region import (  # type: ignore  # noqa: E402
    build_team_streams,
    compute_lan_winrate,
    compute_team_home_region,
    load_hltv_event_history,
    _norm_team as _norm_v14,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


V8_KEYS = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
           "bo_centered", "pistol_diff",
           "tier_s", "tier_a", "tier_b", "tier_c", "tier_d", "kd_diff"]
V10_KEYS = ["decider_winrate_diff", "permaban_match_diff", "forced_off_permaban_flag"]
V13_KEYS = ["ct_start_wr_diff", "bias_aligned_diff"]
V14_KEYS = ["is_lan_event", "team1_lan_winrate_diff", "region_advantage_diff"]


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


def build_rows(matches, *, tm, pistol, tier_map, kd_map, direct,
               vetos_by_match, permaban_freq, map_winrates, pit_map_streams,
               team_history, match_maps_by_id,
               hltv_team_index, events_idx, streams_v14):
    """Single pass building v8 + v10 + v13 + v14 features per row."""
    # Map biases used in compute_bias_aligned_diff are imported with the v13
    # helper. Likewise, region/LAN winrate helpers come from v14. Each
    # feature returns 0 / neutral when its per-feature coverage gate fails,
    # so the regression still sees the row.
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # ── v5 base
        t1f = float(m["t1_form"]) if m["t1_form_n"] >= 3 else 0.5
        t2f = float(m["t2_form"]) if m["t2_form_n"] >= 3 else 0.5
        form_diff = t1f - t2f
        h2h_diff = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0
        rest_diff = (min(float(m["t1_days_since"]), 30.0)
                     - min(float(m["t2_days_since"]), 30.0)) / 30.0
        rank_diff = (
            float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0
            if (m["hltv_rank1"] and m["hltv_rank2"]) else 0.0
        )
        t1_tm, t2_tm = tm.get(m["team1"]), tm.get(m["team2"])
        tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0
        bo_centered = float((m["best_of"] or 3) - 3)

        # ── v7 pistol + tier
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

        # ── v8 K/D with direct fallback
        d1 = direct.get((m["team1"] or "").lower())
        d2 = direct.get((m["team2"] or "").lower())
        t1_kd = kd_map.get(m["team1"]) or (d1["kd"] if d1 and d1.get("maps", 0) >= 30 else None)
        t2_kd = kd_map.get(m["team2"]) or (d2["kd"] if d2 and d2.get("maps", 0) >= 30 else None)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        hltv_id = m.get("hltv_match_id")
        kickoff_ts = m["kickoff_time"]

        # ── v10-PIT veto features
        veto_feats = derive_match_veto_features(
            vetos_by_match.get(hltv_id, []),
            m["team1"], m["team2"], permaban_freq, map_winrates,
            kickoff=kickoff_ts, pit_map_streams=pit_map_streams,
        )
        v10_covered = veto_feats.pop("covered", 0)

        # ── v13 CT-start win-rate + bias_aligned_diff
        wr1, _ = compute_ct_start_winrate(m["team1"], kickoff_ts, team_history)
        wr2, _ = compute_ct_start_winrate(m["team2"], kickoff_ts, team_history)
        ct_start_wr_diff = (
            float(wr1 - wr2) if (wr1 is not None and wr2 is not None) else 0.0
        )
        ct_wr_covered = 1 if (wr1 is not None and wr2 is not None) else 0

        # orient determined by which HLTV team aligns with the bo3gg team1
        orient = None
        if hltv_id is not None:
            pair = hltv_team_index.get(hltv_id)
            if pair:
                hltv_t1, _hltv_t2 = pair
                if _norm_v13(hltv_t1) == _norm_v13(m["team1"]):
                    orient = "fwd"
                elif _norm_v13(hltv_t1) == _norm_v13(m["team2"]):
                    orient = "rev"
                else:
                    orient = "fwd"
        bias_aligned_diff, n_biased = compute_bias_aligned_diff(
            hltv_id, orient, match_maps_by_id
        )
        bias_covered = 1 if n_biased > 0 else 0

        # ── v14 LAN + region
        k1, k2 = _norm_v14(m["team1"]), _norm_v14(m["team2"])
        is_lan_event = 0
        event_region = "UNKNOWN"
        hltv_covered = 0
        if hltv_id is not None:
            ev = events_idx.get(hltv_id)
            if ev is not None:
                is_lan_event = 1 if ev["is_lan"] else 0
                event_region = ev["region"]
                hltv_covered = 1

        lan_wr1, _ = compute_lan_winrate(k1, kickoff_ts, streams_v14)
        lan_wr2, _ = compute_lan_winrate(k2, kickoff_ts, streams_v14)
        team1_lan_winrate_diff = (
            float(lan_wr1 - lan_wr2)
            if (lan_wr1 is not None and lan_wr2 is not None) else 0.0
        )
        lan_wr_covered = 1 if (lan_wr1 is not None and lan_wr2 is not None) else 0

        team1_home = compute_team_home_region(k1, kickoff_ts, streams_v14)
        team2_home = compute_team_home_region(k2, kickoff_ts, streams_v14)
        region_match_team1 = (
            1 if (team1_home is not None
                  and event_region not in ("ONLINE", "UNKNOWN")
                  and team1_home == event_region)
            else 0
        )
        region_match_team2 = (
            1 if (team2_home is not None
                  and event_region not in ("ONLINE", "UNKNOWN")
                  and team2_home == event_region)
            else 0
        )
        region_advantage_diff = float(region_match_team1 - region_match_team2)
        region_covered = 1 if (
            team1_home is not None and team2_home is not None
            and event_region not in ("ONLINE", "UNKNOWN")
        ) else 0

        out.append({
            "kickoff": kickoff_ts, "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            # v8
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            # v10-PIT
            **veto_feats,
            "v10_covered": v10_covered,
            # v13
            "ct_start_wr_diff":   ct_start_wr_diff,
            "bias_aligned_diff":  bias_aligned_diff,
            "ct_wr_covered":      ct_wr_covered,
            "bias_covered":       bias_covered,
            # v14
            "is_lan_event":            float(is_lan_event),
            "team1_lan_winrate_diff":  float(team1_lan_winrate_diff),
            "region_advantage_diff":   float(region_advantage_diff),
            "hltv_covered":            hltv_covered,
            "lan_wr_covered":          lan_wr_covered,
            "region_covered":          region_covered,
        })
    return out


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

    # v10 sources
    print("loading veto records + map winrates…")
    veto_rows = load_veto_records()
    map_winrates = load_map_winrates()           # legacy aggregate (unused but cheap)
    pit_map_streams = build_pit_team_map_streams()
    permaban_freq = per_team_permaban_freq(veto_rows)
    vetos_by_match: dict = defaultdict(list)
    for v in veto_rows:
        vetos_by_match[v["hltv_match_id"]].append(v)
    for mid in vetos_by_match:
        vetos_by_match[mid].sort(key=lambda x: x["step"])

    # v13 sources
    print("loading starting-side history…")
    team_history, match_maps_by_id = load_starting_side_history()
    hltv_team_index = load_hltv_match_index()

    # v14 sources
    print("loading HLTV event history…")
    events_v14, _, events_idx = load_hltv_event_history()
    streams_v14 = build_team_streams(events_v14)
    print(f"  {len(streams_v14)} teams streamed (v14)")

    # Matches + bridge
    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    print("  enriching matches with hltv_match_id via cs2_match_id_bridge…")
    bridge = {r["bo3gg_id"]: r["hltv_match_id"] for r in execute_query(
        "SELECT bo3gg_id, hltv_match_id FROM cs2_match_id_bridge"
    )}
    matched = 0
    for m in matches:
        bid = m.get("bo3gg_id")
        m["hltv_match_id"] = bridge.get(str(bid)) if bid is not None else None
        if m["hltv_match_id"] is not None:
            matched += 1
    print(f"  bridge coverage: {matched}/{len(matches)} ({matched/max(len(matches),1):.1%})")

    rows = build_rows(
        matches,
        tm=tm, pistol=pistol, tier_map=tier_map, kd_map=kd_map, direct=direct,
        vetos_by_match=vetos_by_match, permaban_freq=permaban_freq,
        map_winrates=map_winrates, pit_map_streams=pit_map_streams,
        team_history=team_history, match_maps_by_id=match_maps_by_id,
        hltv_team_index=hltv_team_index,
        events_idx=events_idx, streams_v14=streams_v14,
    )
    print(f"  {len(rows)} matches with saved_prob\n")

    # Coverage roll-up
    cov_v10 = sum(1 for r in rows if r["v10_covered"])
    cov_v13 = sum(1 for r in rows if r["ct_wr_covered"] or r["bias_covered"])
    cov_v14 = sum(1 for r in rows if r["hltv_covered"])
    n = max(len(rows), 1)
    print(f"  coverage:")
    print(f"    v10 (any veto signal):     {cov_v10}/{n} ({cov_v10/n:.1%})")
    print(f"    v13 (ct_wr OR bias):       {cov_v13}/{n} ({cov_v13/n:.1%})")
    print(f"    v14 (HLTV matched):        {cov_v14}/{n} ({cov_v14/n:.1%})\n")

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v15-stack (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 83)
    print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v15-stack-all_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    for keys, lbl in [
        (V8_KEYS, "v8 reference"),
        (V8_KEYS + V10_KEYS, "v15-stack: v8 + v10-PIT"),
        (V8_KEYS + V13_KEYS, "v15-stack: v8 + v13"),
        (V8_KEYS + V14_KEYS, "v15-stack: v8 + v14"),
        (V8_KEYS + V10_KEYS + V13_KEYS, "v15-stack: v8 + v10 + v13"),
        (V8_KEYS + V10_KEYS + V14_KEYS, "v15-stack: v8 + v10 + v14"),
        (V8_KEYS + V13_KEYS + V14_KEYS, "v15-stack: v8 + v13 + v14"),
        (V8_KEYS + V10_KEYS + V13_KEYS + V14_KEYS, "v15-stack-all (v8+v10+v13+v14)"),
    ]:
        r = evaluate(rows, keys, lbl)
        if r.get("skipped"):
            print(f"{lbl:50}  (skipped, n={r['n']})")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{lbl:50} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(f"v15-stack-all_{lbl}", r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
        auc_track[lbl] = mm["auc"]

    # PROMOTE decision: v8 vs v15-stack-all
    base_auc = auc_track.get("v8 reference")
    stack_auc = auc_track.get("v15-stack-all (v8+v10+v13+v14)")
    v10_only = auc_track.get("v15-stack: v8 + v10-PIT")
    v13_only = auc_track.get("v15-stack: v8 + v13")
    v14_only = auc_track.get("v15-stack: v8 + v14")

    print("\n" + "=" * 83)
    print("PROMOTE DECISION")
    print("=" * 83)
    if base_auc is None or stack_auc is None:
        print("PROMOTE: no (insufficient data — could not score v8 or v15-stack)")
    else:
        delta = stack_auc - base_auc
        print(f"baseline AUC (v8):     {base_auc:.4f}")
        print(f"v15-stack-all AUC:     {stack_auc:.4f}")
        print(f"delta:                 {delta:+.4f}")
        if v10_only is not None and v13_only is not None and v14_only is not None:
            sum_indiv = ((v10_only - base_auc)
                         + (v13_only - base_auc)
                         + (v14_only - base_auc))
            print(f"sum of individual block deltas: {sum_indiv:+.4f}")
            print(f"stacked − sum(indiv):           {(delta - sum_indiv):+.4f} "
                  f"({'additive/orthogonal' if abs(delta - sum_indiv) < 0.002 else 'interaction effect'})")
        if delta >= 0.002:
            print("PROMOTE: yes (delta >= +0.002 AUC, no degradation)")
        else:
            print("PROMOTE: no (delta < +0.002 AUC threshold)")


if __name__ == "__main__":
    main()
