"""
Train the v9 stacking model — v8 features + veto-derived map features.

Three new features over v8 (all default 0.0 when coverage missing):
  decider_winrate_diff    — team1 − team2 win% on the BO3 decider (left_over) map
  permaban_diff_on_decider — team2 rolling ban-rate on decider − team1 ban-rate
  map_pool_winrate_diff   — mean(team1 − team2 win%) across all maps in the veto

Coverage on training set (since 2025-06-01):
  veto bridge: 84.1%  map-stats: 18.5%

The 0.0 fallback means v9 is identical to v8 on uncovered matches and better
on covered ones — safe to deploy even at current coverage levels.

Run:
    python3 scripts/esports/cs2_v9_train.py --since 2025-06-01
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from cs2_sneak_peek_v5 import load_matches_with_features, load_team_map  # type: ignore
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore
from cs2_sneak_peek_v8 import load_team_stats_direct, _kd_with_fallback, _pistol_with_fallback  # type: ignore
from cs2_sneak_peek_v9_veto import (  # type: ignore
    load_map_winrate_map, load_veto_history, load_match_veto_summary,
    load_bo3gg_to_hltv_bridge, load_map_pistol_map, load_match_starting_side,
    _veto_features,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score, log_loss  # noqa: E402


FEATURE_KEYS = [
    "logit_saved", "form_diff", "h2h_diff", "tm_diff", "rest_diff",
    "rank_diff", "bo_centered", "pistol_diff",
    "tier_s", "tier_a", "tier_b", "tier_c", "tier_d",
    "kd_diff",
    "decider_winrate_diff", "permaban_diff_on_decider", "map_pool_winrate_diff",
    "pistol_ct_diff", "pistol_t_diff", "map1_side_advantage",
]


def _logit(p):
    p = max(min(p, 1 - 1e-4), 1e-4)
    import math
    return math.log(p / (1 - p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    args = ap.parse_args()

    print(f"=== v9 training  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  --since {args.since}")
    print("  loading data...")

    tm          = load_team_map()
    pistol      = load_pistol_map()
    tier_map    = load_tier_map()
    kd_map      = load_team_kd_map()
    direct      = load_team_stats_direct()
    matches     = load_matches_with_features(args.since)

    bridge         = load_bo3gg_to_hltv_bridge()
    veto_summary   = load_match_veto_summary()
    ban_history    = load_veto_history()
    map_winrates   = load_map_winrate_map()
    map_pistols    = load_map_pistol_map()
    starting_sides = load_match_starting_side()

    rows = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        saved = float(m["win_prob1"])

        t1f = float(m["t1_form"]) if (m["t1_form_n"] or 0) >= 3 else 0.5
        t2f = float(m["t2_form"]) if (m["t2_form_n"] or 0) >= 3 else 0.5
        form_diff = t1f - t2f
        h2h_diff  = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0
        rest_diff = (min(float(m["t1_days_since"]), 30.0) -
                     min(float(m["t2_days_since"]), 30.0)) / 30.0
        rank_diff = 0.0
        if m.get("hltv_rank1") and m.get("hltv_rank2"):
            rank_diff = float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0

        t1_tm = tm.get(m["team1"])
        t2_tm = tm.get(m["team2"])
        tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0
        bo_centered = float((m.get("best_of") or 3) - 3)

        p1 = _pistol_with_fallback(m["team1"], pistol, direct)
        p2 = _pistol_with_fallback(m["team2"], pistol, direct)
        pistol_diff = (p1["overall"] - p2["overall"]) / 100.0 if (p1 and p2) else 0.0

        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier  = (tier_map.get((m["team1"], m["team2"], kdate)) or
                 tier_map.get((m["team2"], m["team1"], kdate)))

        t1_kd = _kd_with_fallback(m["team1"], kd_map, direct)
        t2_kd = _kd_with_fallback(m["team2"], kd_map, direct)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        hltv_id = bridge.get(int(m["bo3gg_id"])) if m.get("bo3gg_id") else None
        vf = _veto_features(m["team1"], m["team2"], m["kickoff_time"],
                            hltv_id, veto_summary, ban_history, map_winrates,
                            map_pistols=map_pistols, starting_sides=starting_sides)

        rows.append({
            "y": 1 if m["winner"] == "team1" else 0,
            "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "tm_diff": tm_diff, "rest_diff": rest_diff,
            "rank_diff": rank_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": 1.0 if tier == "s" else 0.0,
            "tier_a": 1.0 if tier == "a" else 0.0,
            "tier_b": 1.0 if tier == "b" else 0.0,
            "tier_c": 1.0 if tier == "c" else 0.0,
            "tier_d": 1.0 if tier == "d" else 0.0,
            "kd_diff": kd_diff,
            **vf,
        })

    veto_n    = sum(1 for r in rows if r["decider_winrate_diff"] != 0.0
                    or r["permaban_diff_on_decider"] != 0.0
                    or r["map_pool_winrate_diff"] != 0.0)
    pistol_n  = sum(1 for r in rows if r["pistol_ct_diff"] != 0.0 or r["pistol_t_diff"] != 0.0)
    side_n    = sum(1 for r in rows if r["map1_side_advantage"] != 0.0)
    print(f"  rows: {len(rows)}  veto-covered: {veto_n}/{len(rows)}"
          f"  pistol-covered: {pistol_n}/{len(rows)}  side-covered: {side_n}/{len(rows)}")

    X = np.array([[r[k] for k in FEATURE_KEYS] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)

    model = LogisticRegression(max_iter=2000)
    model.fit(X, y)

    intercept = float(model.intercept_[0])
    coefs     = dict(zip(FEATURE_KEYS, [float(c) for c in model.coef_[0]]))

    p      = model.predict_proba(X)[:, 1]
    in_auc = float(roc_auc_score(y, p))
    in_ll  = float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4)))
    print(f"  in-sample AUC {in_auc:.4f}  log_loss {in_ll:.4f}")
    print(f"  intercept {intercept:+.4f}")
    for k, c in coefs.items():
        print(f"    {k:<30} {c:+.4f}")

    execute_write("""
        INSERT INTO cs2_model_coefficients
            (model_version, a, b, intercept, coefs, feature_keys, n, auc,
             log_loss, trained_at, seeded_from)
        VALUES ('v9', 1.0, 0.0, %s, %s::jsonb, %s, %s, %s, %s, NOW(), 'cs2_v9_train.py')
        ON CONFLICT (model_version) DO UPDATE SET
            intercept    = EXCLUDED.intercept,
            coefs        = EXCLUDED.coefs,
            feature_keys = EXCLUDED.feature_keys,
            n            = EXCLUDED.n,
            auc          = EXCLUDED.auc,
            log_loss     = EXCLUDED.log_loss,
            trained_at   = NOW()
    """, (intercept, json.dumps(coefs), FEATURE_KEYS, len(rows), in_auc, in_ll))
    print(f"\n  saved → cs2_model_coefficients[model_version='v9']")


if __name__ == "__main__":
    main()
