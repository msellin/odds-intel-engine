"""
CS2 sneak-peek v9-veto — v8 + veto-derived map features.

Three new features from cs2_hltv_match_veto × cs2_hltv_team_map_stats:

  decider_winrate_diff    — team1_win_pct − team2_win_pct on the BO3 left_over map
                            (from team_map_stats; 0.0 when coverage missing)
  permaban_diff_on_decider — team2 rolling ban-rate on decider minus team1 ban-rate
                            in their last 20 vetoes before the match
                            (positive = team2 hates the decider map more → good for team1)
  map_pool_winrate_diff   — mean(team1 win% − team2 win%) across all maps in the veto
                            (bans + picks + left_over; same map_stats source)

Coverage: ~2,683 of 3,186 training rows (since 2025-06-01) have veto data via
cs2_match_id_bridge. map_stats coverage additional gate (~248 teams).

Run:
    python3 scripts/esports/cs2_sneak_peek_v9_veto.py [--since 2025-06-01]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
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
from cs2_sneak_peek_v8 import (  # type: ignore
    load_team_stats_direct, _kd_with_fallback, _pistol_with_fallback,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Veto feature loaders
# ---------------------------------------------------------------------------

def load_map_winrate_map() -> dict[str, dict[str, float]]:
    """{team_name_lower: {map_name: win_pct}} — scraped first, computed fallback.

    CS2-MAP-STATS-EXPAND (2026-06-30): cs2_hltv_team_map_stats has 248
    authenticated-scraped teams; cs2_computed_team_map_stats adds ~2000 more
    from match history. Scraped stats take priority where both exist.
    """
    rows = execute_query("""
        WITH scraped AS (
            SELECT DISTINCT ON (lower(team_name), map_name)
                lower(team_name) AS team_key,
                map_name,
                win_pct
            FROM cs2_hltv_team_map_stats
            WHERE win_pct IS NOT NULL
            ORDER BY lower(team_name), map_name, snapshot_date DESC, fetched_at DESC
        ),
        computed AS (
            SELECT DISTINCT ON (lower(team_name), map_name)
                lower(team_name) AS team_key,
                map_name,
                win_pct
            FROM cs2_computed_team_map_stats
            WHERE win_pct IS NOT NULL
            ORDER BY lower(team_name), map_name, computed_date DESC
        )
        SELECT s.team_key, s.map_name, s.win_pct FROM scraped s
        UNION ALL
        SELECT c.team_key, c.map_name, c.win_pct FROM computed c
        WHERE NOT EXISTS (
            SELECT 1 FROM scraped s
            WHERE s.team_key = c.team_key AND s.map_name = c.map_name
        )
    """)
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["win_pct"] is not None:
            out[r["team_key"]][r["map_name"]] = float(r["win_pct"])
    return dict(out)


def load_veto_history() -> dict[str, list[tuple[datetime, str]]]:
    """{team_name_lower: sorted list of (match_date, map_name)} for 'removed' actions only."""
    rows = execute_query("""
        SELECT lower(v.team_name) AS team_key,
               m.match_date,
               v.map_name
        FROM cs2_hltv_match_veto v
        JOIN cs2_hltv_matches m ON m.hltv_match_id = v.hltv_match_id
        WHERE v.action = 'removed' AND v.team_name != ''
        ORDER BY m.match_date
    """)
    out: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for r in rows:
        out[r["team_key"]].append((r["match_date"], r["map_name"]))
    return dict(out)


def load_match_veto_summary() -> dict[int, dict]:
    """hltv_match_id → {decider_map, all_maps: [str]} from veto records."""
    rows = execute_query("""
        SELECT hltv_match_id, action, map_name
        FROM cs2_hltv_match_veto
        ORDER BY hltv_match_id, step
    """)
    by_match: dict[int, dict] = defaultdict(lambda: {"decider_map": None, "all_maps": []})
    for r in rows:
        mid = r["hltv_match_id"]
        by_match[mid]["all_maps"].append(r["map_name"])
        if r["action"] == "left_over":
            by_match[mid]["decider_map"] = r["map_name"]
    return dict(by_match)


def load_bo3gg_to_hltv_bridge() -> dict[int, int]:
    """bo3gg_id (int) → hltv_match_id (int)."""
    rows = execute_query("""
        SELECT bo3gg_id::integer AS bo3gg_id, hltv_match_id
        FROM cs2_match_id_bridge
        WHERE bo3gg_id ~ '^-?[0-9]+$'
    """)
    return {r["bo3gg_id"]: r["hltv_match_id"] for r in rows}


# ---------------------------------------------------------------------------
# Rolling ban-rate helper
# ---------------------------------------------------------------------------

def _rolling_ban_rate(team_key: str,
                      map_name: str,
                      before_date,
                      ban_history: dict[str, list[tuple]],
                      window: int = 20) -> float:
    """Fraction of team's last `window` vetoes (before before_date) that banned map_name."""
    history = ban_history.get(team_key, [])
    if not history:
        return 0.0
    # binary search for position of before_date
    dates = [h[0] for h in history]
    idx = bisect.bisect_left(dates, before_date)
    recent = history[max(0, idx - window): idx]
    if not recent:
        return 0.0
    return sum(1 for _, m in recent if m == map_name) / len(recent)


# ---------------------------------------------------------------------------
# Feature builder
# ---------------------------------------------------------------------------

VETO_FALLBACK = {"decider_winrate_diff": 0.0,
                 "permaban_diff_on_decider": 0.0,
                 "map_pool_winrate_diff": 0.0}


def _veto_features(team1: str, team2: str, match_date,
                   hltv_match_id: int | None,
                   veto_summary: dict,
                   ban_history: dict,
                   map_winrates: dict) -> dict[str, float]:
    if hltv_match_id is None or hltv_match_id not in veto_summary:
        return VETO_FALLBACK.copy()

    vs = veto_summary[hltv_match_id]
    t1k = team1.lower()
    t2k = team2.lower()
    t1_stats = map_winrates.get(t1k, {})
    t2_stats = map_winrates.get(t2k, {})

    # map_pool_winrate_diff across all maps in the veto
    diffs = []
    for mp in set(vs["all_maps"]):
        w1 = t1_stats.get(mp)
        w2 = t2_stats.get(mp)
        if w1 is not None and w2 is not None:
            diffs.append((w1 - w2) / 100.0)
    map_pool_diff = float(np.mean(diffs)) if diffs else 0.0

    # decider (left_over) specific features
    decider = vs["decider_map"]
    if decider is None:
        return {
            "decider_winrate_diff": 0.0,
            "permaban_diff_on_decider": 0.0,
            "map_pool_winrate_diff": map_pool_diff,
        }

    w1 = t1_stats.get(decider)
    w2 = t2_stats.get(decider)
    decider_diff = (w1 - w2) / 100.0 if (w1 is not None and w2 is not None) else 0.0

    t1_ban = _rolling_ban_rate(t1k, decider, match_date, ban_history)
    t2_ban = _rolling_ban_rate(t2k, decider, match_date, ban_history)
    permaban_diff = t2_ban - t1_ban  # positive = team2 hates decider more = good for team1

    return {
        "decider_winrate_diff": decider_diff,
        "permaban_diff_on_decider": permaban_diff,
        "map_pool_winrate_diff": map_pool_diff,
    }


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

V8_FEATURES = [
    "logit_saved", "form_diff", "h2h_diff", "tm_diff", "rest_diff",
    "rank_diff", "bo_centered", "pistol_diff",
    "tier_s", "tier_a", "tier_b", "tier_c", "tier_d",
    "kd_diff",
]

VETO_FEATURES = ["decider_winrate_diff", "permaban_diff_on_decider", "map_pool_winrate_diff"]


def build_rows(matches, tm, pistol, tier_map, kd_map, direct,
               bridge, veto_summary, ban_history, map_winrates):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        saved = float(m["win_prob1"])

        t1f = float(m["t1_form"]) if (m["t1_form_n"] or 0) >= 3 else 0.5
        t2f = float(m["t2_form"]) if (m["t2_form_n"] or 0) >= 3 else 0.5
        form_diff = t1f - t2f

        h2h_diff = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0
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
        tier = (tier_map.get((m["team1"], m["team2"], kdate)) or
                tier_map.get((m["team2"], m["team1"], kdate)))

        t1_kd = _kd_with_fallback(m["team1"], kd_map, direct)
        t2_kd = _kd_with_fallback(m["team2"], kd_map, direct)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        hltv_id = bridge.get(int(m["bo3gg_id"])) if m.get("bo3gg_id") else None
        vf = _veto_features(
            m["team1"], m["team2"], m["kickoff_time"],
            hltv_id, veto_summary, ban_history, map_winrates,
        )

        out.append({
            "y": 1 if m["winner"] == "team1" else 0,
            "logit_saved": _logit(saved),
            "form_diff": form_diff,
            "h2h_diff": h2h_diff,
            "tm_diff": tm_diff,
            "rest_diff": rest_diff,
            "rank_diff": rank_diff,
            "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": 1.0 if tier == "s" else 0.0,
            "tier_a": 1.0 if tier == "a" else 0.0,
            "tier_b": 1.0 if tier == "b" else 0.0,
            "tier_c": 1.0 if tier == "c" else 0.0,
            "tier_d": 1.0 if tier == "d" else 0.0,
            "kd_diff": kd_diff,
            "veto_covered": hltv_id is not None and hltv_id in veto_summary,
            "mapstats_covered": (vf["decider_winrate_diff"] != 0.0 or
                                 vf["map_pool_winrate_diff"] != 0.0),
            **vf,
        })
    return out


# ---------------------------------------------------------------------------
# Walk-forward eval
# ---------------------------------------------------------------------------

def walk_forward(rows, feature_sets: dict[str, list[str]],
                 train_frac: float = 0.7) -> dict[str, dict]:
    split = int(len(rows) * train_frac)
    train, test = rows[:split], rows[split:]
    if not test:
        return {}

    X_tr = {k: np.array([[r[f] for f in feats] for r in train], dtype=float)
            for k, feats in feature_sets.items()}
    X_te = {k: np.array([[r[f] for f in feats] for r in test], dtype=float)
            for k, feats in feature_sets.items()}
    y_tr = np.array([r["y"] for r in train])
    y_te = np.array([r["y"] for r in test])

    results = {}
    for name, feats in feature_sets.items():
        m = LogisticRegression(max_iter=2000)
        m.fit(X_tr[name], y_tr)
        p = m.predict_proba(X_te[name])[:, 1]
        results[name] = {
            "auc": roc_auc_score(y_te, p),
            "ll":  log_loss(y_te, np.clip(p, 1e-4, 1 - 1e-4)),
            "brier": brier_score_loss(y_te, p),
            "acc": float((p >= 0.5) == y_te).mean() if False else
                   float(np.mean((p >= 0.5) == y_te)),
        }
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    ap.add_argument("--train-frac", type=float, default=0.70)
    args = ap.parse_args()

    print(f"=== CS2 sneak-peek v9-veto  "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  --since {args.since}")

    print("  loading base data...")
    tm          = load_team_map()
    pistol      = load_pistol_map()
    tier_map    = load_tier_map()
    kd_map      = load_team_kd_map()
    direct      = load_team_stats_direct()
    matches     = load_matches_with_features(args.since)

    print("  loading veto data...")
    bridge       = load_bo3gg_to_hltv_bridge()
    veto_summary = load_match_veto_summary()
    ban_history  = load_veto_history()
    map_winrates = load_map_winrate_map()

    print(f"  bridge entries: {len(bridge)}  "
          f"veto matches: {len(veto_summary)}  "
          f"teams with map stats: {len(map_winrates)}")

    print(f"  building rows...")
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                      bridge, veto_summary, ban_history, map_winrates)

    veto_n     = sum(1 for r in rows if r["veto_covered"])
    mapstats_n = sum(1 for r in rows if r["mapstats_covered"])
    print(f"  total rows: {len(rows)}")
    print(f"  veto coverage:      {veto_n}/{len(rows)} ({100*veto_n/len(rows):.1f}%)")
    print(f"  map-stats coverage: {mapstats_n}/{len(rows)} ({100*mapstats_n/len(rows):.1f}%)")

    feature_sets = {
        "baseline (hltv_v1 direct)": ["logit_saved"],
        "v8 reference (kd_diff)":    V8_FEATURES,
        "v9v: + decider_wr_diff":    V8_FEATURES + ["decider_winrate_diff"],
        "v9v: + permaban_diff":       V8_FEATURES + ["permaban_diff_on_decider"],
        "v9v: + map_pool_wr_diff":    V8_FEATURES + ["map_pool_winrate_diff"],
        "v9v: ALL 3 veto features":   V8_FEATURES + VETO_FEATURES,
    }

    print(f"\n--- full (n={len(rows)}, test={int(len(rows)*(1-args.train_frac))}) ---")
    print(f"{'set':<35} {'AUC':>7} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 65)
    results_full = walk_forward(rows, feature_sets, args.train_frac)
    for name, r in results_full.items():
        star = "*" if r["auc"] > results_full.get("v8 reference (kd_diff)", {}).get("auc", 0) else " "
        print(f"  {name:<33}{star}{r['auc']:.3f} {r['ll']:.4f} {r['brier']:.4f} {r['acc']:.3f}")

    # Veto-covered subset
    rows_vc = [r for r in rows if r["veto_covered"]]
    if rows_vc:
        print(f"\n--- veto-covered (n={len(rows_vc)}, "
              f"test={int(len(rows_vc)*(1-args.train_frac))}) ---")
        print(f"{'set':<35} {'AUC':>7} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 65)
        results_vc = walk_forward(rows_vc, feature_sets, args.train_frac)
        for name, r in results_vc.items():
            star = "*" if r["auc"] > results_vc.get("v8 reference (kd_diff)", {}).get("auc", 0) else " "
            print(f"  {name:<33}{star}{r['auc']:.3f} {r['ll']:.4f} {r['brier']:.4f} {r['acc']:.3f}")

    # Map-stats covered subset
    rows_ms = [r for r in rows if r["mapstats_covered"]]
    if rows_ms:
        print(f"\n--- map-stats covered (n={len(rows_ms)}, "
              f"test={int(len(rows_ms)*(1-args.train_frac))}) ---")
        print(f"{'set':<35} {'AUC':>7} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 65)
        results_ms = walk_forward(rows_ms, feature_sets, args.train_frac)
        for name, r in results_ms.items():
            star = "*" if r["auc"] > results_ms.get("v8 reference (kd_diff)", {}).get("auc", 0) else " "
            print(f"  {name:<33}{star}{r['auc']:.3f} {r['ll']:.4f} {r['brier']:.4f} {r['acc']:.3f}")

    # Feature coefficients from full-data train
    print("\n--- v9v ALL 3: logistic regression coefficients (full training set) ---")
    all_feats = V8_FEATURES + VETO_FEATURES
    X_all = np.array([[r[f] for f in all_feats] for r in rows], dtype=float)
    y_all = np.array([r["y"] for r in rows])
    m_all = LogisticRegression(max_iter=2000)
    m_all.fit(X_all, y_all)
    for feat, coef in sorted(zip(all_feats, m_all.coef_[0]),
                              key=lambda x: abs(x[1]), reverse=True):
        print(f"  {feat:<30} {coef:+.4f}")


if __name__ == "__main__":
    main()
