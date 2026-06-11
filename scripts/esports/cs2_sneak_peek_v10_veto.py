"""
CS2 sneak-peek v10-veto — v8 + 3 features derived from veto pick/ban data.

All from cs2_hltv_match_veto (~9,800 rows across 1,400 matches as of 2026-06-10).
No new scraping needed — pure SQL on data we already have.

New features:
  - permaban_match_diff      : how often does each team ban the OPPONENT's
                                most-banned map vs how often opponent bans theirs?
                                (negative → team1 favored by veto; positive → team2)
  - decider_winrate_diff     : walk veto order → identify the decider map →
                                team1's win % on that map minus team2's
  - forced_off_permaban_flag : 1 if a team's usual top-3 ban was already
                                banned by opponent (signed: team1=−1, team2=+1)

PIT-correct: per-team rolling permaban frequency uses only matches before
the current kickoff. Decider winrate uses only matches before kickoff.

Run:
    python3 scripts/esports/cs2_sneak_peek_v10_veto.py [--since 2025-06-01]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
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


def load_veto_records() -> list[dict]:
    """All veto steps joined to their match's kickoff_time (PIT key)."""
    return execute_query("""
        SELECT v.hltv_match_id, v.step, v.team_name, v.action, v.map_name,
               m.match_date AS kickoff
        FROM cs2_hltv_match_veto v
        JOIN cs2_hltv_matches m ON m.hltv_match_id = v.hltv_match_id
        ORDER BY m.match_date, v.hltv_match_id, v.step
    """)


def load_map_winrates() -> dict:
    """{(team_name, map_name): {wins, losses}}. Aggregated all-time from
    cs2_hltv_match_maps. LEGACY — kept only as the non-PIT comparator. Use
    `build_pit_team_map_streams` + `pit_team_map_winrate` for the strict
    prior-only aggregate that the v10-PIT rerun uses."""
    rows = execute_query("""
        SELECT mm.team1_score AS s1, mm.team2_score AS s2, mm.winner_name,
               mm.map_name, m.team1_name AS t1, m.team2_name AS t2
        FROM cs2_hltv_match_maps mm
        JOIN cs2_hltv_matches m ON m.hltv_match_id = mm.hltv_match_id
        WHERE mm.map_name IS NOT NULL AND mm.winner_name IS NOT NULL
    """, None)
    out: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
    for r in rows:
        for team_name in (r["t1"], r["t2"]):
            if not team_name:
                continue
            key = (team_name, r["map_name"])
            if r["winner_name"] == team_name:
                out[key]["wins"] += 1
            else:
                out[key]["losses"] += 1
    return out


def build_pit_team_map_streams() -> dict:
    """{(team_name, map_name): sorted list of (match_date, won_bool)} —
    strictly-prior aggregate source for the v10-PIT rerun. Built once at
    startup; each lookup is O(log n) via bisect on match_date.

    Without this, the original v10 sneak peek's decider_winrate_diff sees the
    eval match's OWN decider outcome (all-time aggregate from
    cs2_hltv_match_maps), inflating its AUC delta. This stream is the same
    fix already used in `cs2_hltv_native_backtest.py:build_pit_team_map_streams`."""
    rows = execute_query("""
        SELECT m.match_date, m.team1_name AS t1, m.team2_name AS t2,
               mm.map_name, mm.winner_name
        FROM cs2_hltv_match_maps mm
        JOIN cs2_hltv_matches m ON m.hltv_match_id = mm.hltv_match_id
        WHERE mm.map_name IS NOT NULL AND mm.winner_name IS NOT NULL
          AND m.match_date IS NOT NULL
    """, None)
    streams: dict = defaultdict(list)
    for r in rows:
        md = r["match_date"]
        for team_name in (r["t1"], r["t2"]):
            if not team_name:
                continue
            streams[(team_name, r["map_name"])].append(
                (md, r["winner_name"] == team_name)
            )
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


def pit_team_map_winrate(team: str, map_name: str, kickoff,
                          streams: dict) -> dict | None:
    """Strictly-prior {wins, losses} for (team, map) before kickoff.
    Returns None when no priors. O(log n) bisect on match_date."""
    s = streams.get((team, map_name))
    if not s:
        return None
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi) // 2
        if s[mid][0] < kickoff:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    wins = sum(1 for _, w in s[:lo] if w)
    return {"wins": wins, "losses": lo - wins}


def per_team_permaban_freq(vetos: list[dict]) -> dict:
    """{team_name: {map_name: count}} — how often each team has perma-banned
    each map (action='removed' on step 1 or 2 of vote = perma-ban heuristic)."""
    freq: dict = defaultdict(Counter)
    for v in vetos:
        if v["action"] == "removed" and v["step"] in (1, 2):
            freq[v["team_name"]][v["map_name"]] += 1
    return freq


def derive_match_veto_features(match_veto: list[dict], team1: str, team2: str,
                                permaban_freq: dict, map_winrates: dict,
                                *, kickoff=None,
                                pit_map_streams: dict | None = None) -> dict:
    """Compute the 3 v10 features for one match's veto sequence.
    match_veto is the sorted list of veto steps for ONE match.

    When `pit_map_streams` and `kickoff` are supplied, decider_winrate_diff is
    PIT-correct (strictly < kickoff). Otherwise falls back to the legacy
    all-time `map_winrates` dict for the legacy comparator row."""
    if not match_veto:
        return {"permaban_match_diff": 0.0, "decider_winrate_diff": 0.0,
                "forced_off_permaban_flag": 0.0, "covered": 0}

    # Decider = the map not picked or removed.
    seen_maps = {v["map_name"] for v in match_veto}
    # CS2 active duty has 7 maps; if only one isn't accounted for, it's the decider.
    # If all 7 are in seen_maps (BO5 with all maps used), decider is last "picked" step.
    decider = None
    picks_in_order = [v["map_name"] for v in match_veto if v["action"] == "picked"]
    if picks_in_order:
        # Last pick is typically the decider in BO3/BO5
        decider = picks_in_order[-1]

    # decider_winrate_diff
    decider_diff = 0.0
    if decider:
        if pit_map_streams is not None and kickoff is not None:
            t1_stats = pit_team_map_winrate(team1, decider, kickoff, pit_map_streams)
            t2_stats = pit_team_map_winrate(team2, decider, kickoff, pit_map_streams)
        else:
            t1_stats = map_winrates.get((team1, decider))
            t2_stats = map_winrates.get((team2, decider))
        if t1_stats and t2_stats:
            t1_n = t1_stats["wins"] + t1_stats["losses"]
            t2_n = t2_stats["wins"] + t2_stats["losses"]
            if t1_n >= 3 and t2_n >= 3:
                t1_wr = t1_stats["wins"] / t1_n
                t2_wr = t2_stats["wins"] / t2_n
                decider_diff = t1_wr - t2_wr

    # permaban_match_diff: did team1's permabans hit team2's strong maps?
    # Heuristic: for each team, look at their most-frequent permaban map.
    # If the opponent banned it first, that's a "permaban robbery" against them.
    t1_top_bans = [m for m, _ in permaban_freq.get(team1, Counter()).most_common(3)]
    t2_top_bans = [m for m, _ in permaban_freq.get(team2, Counter()).most_common(3)]
    t1_bans = [v["map_name"] for v in match_veto if v["action"] == "removed" and v["team_name"] == team1]
    t2_bans = [v["map_name"] for v in match_veto if v["action"] == "removed" and v["team_name"] == team2]

    # forced_off_permaban: did team have to ban something NOT in their typical top-3?
    # (because opponent already banned their typical first choice)
    forced_off = 0.0
    if t1_bans and t1_top_bans:
        t1_used_their_top = any(m in t1_top_bans for m in t1_bans[:2])
        if not t1_used_their_top:
            forced_off -= 1.0  # team1 was forced — slight negative
    if t2_bans and t2_top_bans:
        t2_used_their_top = any(m in t2_top_bans for m in t2_bans[:2])
        if not t2_used_their_top:
            forced_off += 1.0

    # permaban_match_diff: did team1 ban one of team2's preferred bans (denying them)?
    # Positive if team1 stole a t2 permaban → team1 favored.
    t1_stole = sum(1 for m in t1_bans if m in t2_top_bans)
    t2_stole = sum(1 for m in t2_bans if m in t1_top_bans)
    permaban_diff = float(t1_stole - t2_stole) / 3.0  # normalised

    return {
        "permaban_match_diff": permaban_diff,
        "decider_winrate_diff": decider_diff,
        "forced_off_permaban_flag": forced_off,
        "covered": 1 if decider and decider_diff != 0.0 else 0,
    }


def build_rows(matches, tm, pistol, tier_map, kd_map, direct,
               vetos_by_match, permaban_freq, map_winrates,
               *, pit_map_streams=None):
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

        # v10 NEW: veto features. Look up this match's veto steps by
        # hltv_match_id (resolved via cs2_match_id_bridge). PIT-correct map
        # winrates flow through `pit_map_streams` when supplied.
        veto_feats = derive_match_veto_features(
            vetos_by_match.get(m.get("hltv_match_id"), []),
            m["team1"], m["team2"], permaban_freq, map_winrates,
            kickoff=m.get("kickoff_time"),
            pit_map_streams=pit_map_streams,
        )

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
            **veto_feats,
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
    veto_rows = load_veto_records()
    map_winrates = load_map_winrates()           # legacy all-time aggregate
    pit_map_streams = build_pit_team_map_streams()  # PIT-correct streams
    permaban_freq = per_team_permaban_freq(veto_rows)
    print(f"  {len(veto_rows)} veto rows, "
          f"map_winrates for {len(map_winrates)} (team,map) pairs, "
          f"pit_streams for {len(pit_map_streams)} (team,map) pairs, "
          f"permaban_freq for {len(permaban_freq)} teams")

    # Index vetos by hltv_match_id for fast lookup, sorted by step
    vetos_by_match: dict = defaultdict(list)
    for v in veto_rows:
        vetos_by_match[v["hltv_match_id"]].append(v)
    for mid in vetos_by_match:
        vetos_by_match[mid].sort(key=lambda x: x["step"])

    matches = load_matches_with_features(args.since)
    # Bridge: bo3gg_id -> hltv_match_id via cs2_match_id_bridge (replaces the
    # old (team1, team2, date) exact-string join that capped coverage at <2%).
    print(f"  enriching matches with hltv_match_id via cs2_match_id_bridge…")
    bridge = {r["bo3gg_id"]: r["hltv_match_id"] for r in execute_query(
        "SELECT bo3gg_id, hltv_match_id FROM cs2_match_id_bridge"
    )}
    matched = 0
    for m in matches:
        # cs2_results.bo3gg_id is INTEGER; bridge.bo3gg_id is TEXT — stringify.
        bid = m.get("bo3gg_id")
        m["hltv_match_id"] = bridge.get(str(bid)) if bid is not None else None
        if m["hltv_match_id"] is not None:
            matched += 1
    print(f"  bridge coverage: {matched}/{len(matches)} ({matched/max(len(matches),1):.1%})")

    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                      vetos_by_match, permaban_freq, map_winrates,
                      pit_map_streams=pit_map_streams)
    print(f"  {len(rows)} matches\n")

    cov_decider = sum(1 for r in rows if r["covered"])
    cov_any_veto = sum(1 for r in rows if r["permaban_match_diff"] != 0 or r["forced_off_permaban_flag"] != 0 or r["decider_winrate_diff"] != 0)
    print(f"  coverage:")
    print(f"    decider_winrate_diff (PIT decider on map both have data): {cov_decider}/{len(rows)} ({cov_decider/len(rows):.1%})")
    print(f"    any veto signal:                                          {cov_any_veto}/{len(rows)} ({cov_any_veto/len(rows):.1%})\n")

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
        print(f"{'set':45} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 78)
        print(f"{'baseline (hltv_v1 direct)':45} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
        persist(f"v10-veto-pit_{label}_baseline", len(sample), m_base, since_d, keys=["win_prob1"], n_train=cut)

        for keys, lbl in [
            (v8_keys, "v8 reference"),
            (v8_keys + ["decider_winrate_diff"], "v10v: v8 + decider_winrate"),
            (v8_keys + ["permaban_match_diff"], "v10v: v8 + permaban_match"),
            (v8_keys + ["forced_off_permaban_flag"], "v10v: v8 + forced_off"),
            (v8_keys + ["decider_winrate_diff","permaban_match_diff","forced_off_permaban_flag"],
                "v10v ALL (v8 + 3 veto)"),
            (["decider_winrate_diff","permaban_match_diff","forced_off_permaban_flag"],
                "veto features alone"),
        ]:
            r = evaluate(sample, keys, lbl)
            if r.get("skipped"):
                print(f"{lbl:45}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{lbl:45} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v10-veto-pit_{label}_{lbl}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    run_battery(rows, "full")
    run_battery([r for r in rows if r["covered"]], "veto-covered")


if __name__ == "__main__":
    main()
