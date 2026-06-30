"""
CS2 v9 production scorer.

v9 = v8 features + veto-derived map features:
  decider_winrate_diff    — team1 − team2 win% on the BO3 decider map
  permaban_diff_on_decider — team2 rolling ban-rate on decider − team1 ban-rate
  map_pool_winrate_diff   — mean(team1 − team2 win%) across all maps in the veto

All three default to 0.0 when no match-specific veto data exists (upcoming
matches don't yet have veto scraped). At 0.0, v9 == v8 exactly. When veto
data becomes available for upcoming matches, the features activate.

Run:
    python3 scripts/esports/cs2_v9_predict.py [--record]
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from cs2_v7_predict import (  # type: ignore
    _logit, _sigmoid, load_team_map, load_pistol_map, load_tier_map, load_upcoming,
)
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v8 import (  # type: ignore
    load_team_stats_direct, _kd_with_fallback, _pistol_with_fallback,
)
from cs2_sneak_peek_v9_veto import (  # type: ignore
    load_map_winrate_map, load_veto_history, load_match_veto_summary,
    load_bo3gg_to_hltv_bridge, _veto_features,
)

MODEL_VERSION = "v9"


def load_coefs() -> tuple[float, dict, list[str]]:
    rows = execute_query(
        "SELECT intercept, coefs, feature_keys FROM cs2_model_coefficients WHERE model_version = %s",
        (MODEL_VERSION,),
    )
    if not rows:
        raise RuntimeError(f"No coefficients for {MODEL_VERSION!r}. Run cs2_v9_train.py first.")
    r = rows[0]
    coefs = r["coefs"] if isinstance(r["coefs"], dict) else json.loads(r["coefs"])
    return float(r["intercept"]), coefs, list(r["feature_keys"])


def score_match(m: dict, tm: dict, pistol: dict, tier_map: dict,
                kd_map: dict, direct: dict,
                bridge: dict, veto_summary: dict,
                ban_history: dict, map_winrates: dict,
                intercept: float, coefs: dict) -> dict | None:
    if m.get("win_prob1") is None:
        return None
    saved = float(m["win_prob1"])

    from cs2_v7_predict import _pit_features  # type: ignore
    pit = _pit_features(m["team1"], m["team2"], m["kickoff_time"])

    t1f = float(pit["t1_form"]) if pit["t1_form_n"] >= 3 else 0.5
    t2f = float(pit["t2_form"]) if pit["t2_form_n"] >= 3 else 0.5
    form_diff = t1f - t2f
    h2h_diff  = (float(pit["h2h_t1"]) - 0.5) if (pit["h2h_n"] or 0) >= 2 else 0.0
    rest_diff = (min(float(pit["t1_days_since"]), 30.0) -
                 min(float(pit["t2_days_since"]), 30.0)) / 30.0

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
                        hltv_id, veto_summary, ban_history, map_winrates)

    feat_vals = {
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
    }

    logit     = intercept + sum(coefs[k] * feat_vals.get(k, 0.0) for k in coefs)
    p_team1   = _sigmoid(logit)
    veto_active = any(v != 0.0 for v in vf.values())
    return {
        "p1": round(p_team1, 4),
        "p2": round(1 - p_team1, 4),
        "fair1": round(1 / max(p_team1, 1e-4), 3),
        "fair2": round(1 / max(1 - p_team1, 1e-4), 3),
        "feats": feat_vals,
        "veto_active": veto_active,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    print(f"\n=== CS2 v9 predict  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    intercept, coefs, feature_keys = load_coefs()
    print(f"  loaded v9 coefs (intercept={intercept:+.4f}, {len(coefs)} features)")

    tm          = load_team_map()
    pistol      = load_pistol_map()
    tier_map    = load_tier_map()
    kd_map      = load_team_kd_map()
    direct      = load_team_stats_direct()
    bridge      = load_bo3gg_to_hltv_bridge()
    veto_summary = load_match_veto_summary()
    ban_history  = load_veto_history()
    map_winrates = load_map_winrate_map()

    matches = load_upcoming()
    print(f"  upcoming matches with hltv_v1: {len(matches)}")

    written = veto_active = 0
    scan_time = datetime.now(timezone.utc)
    for m in matches:
        out = score_match(m, tm, pistol, tier_map, kd_map, direct,
                          bridge, veto_summary, ban_history, map_winrates,
                          intercept, coefs)
        if not out:
            continue
        if out["veto_active"]:
            veto_active += 1
        if args.record:
            execute_write("""
                INSERT INTO cs2_predictions
                    (bo3gg_id, scan_time, kickoff_time, league, best_of,
                     team1, team2, win_prob1, win_prob2, fair_odds1, fair_odds2,
                     model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bo3gg_id, scan_time, model_version) DO NOTHING
            """, (m["bo3gg_id"], scan_time, m["kickoff_time"], "",
                  m.get("best_of") or 3,
                  m["team1"], m["team2"],
                  out["p1"], out["p2"], out["fair1"], out["fair2"],
                  MODEL_VERSION))
            written += 1

        print(f"  {m['team1']:<25} vs {m['team2']:<25}  "
              f"p1={out['p1']:.3f}  fair={out['fair1']:.2f}"
              f"{'  [veto active]' if out['veto_active'] else ''}")

    print(f"\n  scored {len(matches)}  veto_active={veto_active}  written={written}")


if __name__ == "__main__":
    main()
