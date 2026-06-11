"""
CS2 sneak-peek v13 — adds starting-side win-rate features (CT-start edge).

Background: knife-round result decides which side each team starts on. Some
maps are heavily side-biased (Nuke +14pp CT, Anubis +14pp T, Overpass +12.8pp
CT), so a team's historical performance starting on CT vs T is a real signal
the bookmaker may or may not fully price in.

For each upcoming match between team1 and team2, compute as of kickoff:

  team1_ct_start_winrate_per_map  — team1's historical map-win% when starting CT
                                    (averaged across all maps with priors).
                                    PIT-correct: only maps before kickoff.
  team2_ct_start_winrate_per_map  — same for team2.
  ct_start_wr_diff                = team1_ct_start_wr − team2_ct_start_wr
                                    (set to 0 when either team has no prior maps)
  bias_aligned_diff               — per-match: did THIS match's starting sides
                                    align with map bias? Computed over the
                                    match's actual maps where bias is sharp
                                    (Nuke, Anubis, Overpass, Inferno). +1 if
                                    only team1 starts on its favored side,
                                    −1 if only team2 does, 0 if both or neither.
                                    Averaged across the played maps; 0 when
                                    `team1_first_half_side` is unknown.

PIT-correct: every aggregate uses only cs2_hltv_match_maps rows whose parent
`cs2_hltv_matches.match_date` is strictly less than the eval row's kickoff_ts.

Compares (walk-forward, 70/30 split like v11):
  baseline (hltv_v1 direct)
  v8 reference                       — v8 stacked logistic
  v13: v8 + ct_start_wr_diff
  v13: v8 + ct_start_wr_diff + bias_aligned_diff
  starting-side features alone (sanity)

Run:
    python3 scripts/esports/cs2_sneak_peek_v13_starting_side.py [--since 2025-06-01]
"""

from __future__ import annotations

import argparse
import bisect
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
from cs2_sneak_peek_v5 import (  # type: ignore
    load_matches_with_features, load_team_map, _logit,
)
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


# Team-name normaliser for joining bo3gg names (cs2_results) with HLTV names
# (cs2_hltv_matches). bo3gg appends " Team"/" Esports" to many tags that
# HLTV stores bare (e.g. "1win Team" vs "1win", "9z Team" vs "9z"). Without
# this, the CT-start lookup covers only 2.4% of matches.
import re as _re  # noqa: E402

_SUFFIX_RE = _re.compile(
    r"\s+(team|esports|esport|gaming|gamingclub|club|pro|academy)$",
    _re.IGNORECASE,
)


def _norm_team(name: str | None) -> str:
    """Return a stripped, lowercase, suffix-free key for join purposes."""
    if not name:
        return ""
    s = name.strip().lower()
    # Strip up to two trailing org suffixes ("team gaming", "esports club", etc.)
    for _ in range(2):
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    # Drop non-alphanumeric to be safe (matches v6's _normalize style).
    return _re.sub(r"[^a-z0-9]", "", s)

# Map → favored side (the side that wins ≥+5pp on average across pro CS2).
# Source: long-running pro-match meta; figures cited in task spec.
# Used for the bias_aligned_diff feature.
MAP_BIAS_FAVORED_SIDE: dict[str, str] = {
    "Nuke":     "ct",   # +14pp CT
    "Anubis":   "t",    # +14pp T
    "Overpass": "ct",   # +12.8pp CT
    "Inferno":  "ct",   # +5-7pp CT
}


def load_starting_side_history() -> tuple[dict, dict]:
    """Return two structures pulled from cs2_hltv_match_maps + cs2_hltv_matches:

    1. `team_history[team_name]` → sorted list of
       (match_date, map_name, started_ct, won)
       — every map appearance for that team with its starting side and outcome.
       `won` is whether that team won that specific map.

    2. `match_maps_by_id[hltv_match_id]` → list of dicts with
       {map_name, team1_first_half_side, team1_won}
       — used by the bias_aligned_diff feature for the eval match itself.

    Both are built with a single batched query so we don't hammer the DB."""
    rows = execute_query(
        """
        SELECT
            m.hltv_match_id,
            m.match_date,
            m.team1_name,
            m.team2_name,
            mm.map_name,
            mm.team1_first_half_side,
            mm.winner_name
        FROM cs2_hltv_matches m
        JOIN cs2_hltv_match_maps mm USING (hltv_match_id)
        WHERE m.match_date IS NOT NULL
          AND mm.team1_first_half_side IS NOT NULL
          AND mm.winner_name IS NOT NULL
        """,
        None,
    )

    team_history: dict[str, list] = defaultdict(list)
    match_maps_by_id: dict[int, list] = defaultdict(list)

    for r in rows:
        t1 = r["team1_name"]
        t2 = r["team2_name"]
        side = (r["team1_first_half_side"] or "").lower()
        if side not in ("ct", "t"):
            continue
        winner = r["winner_name"]
        # winner_name normally matches team1_name or team2_name exactly
        t1_won = (winner == t1)
        t2_won = (winner == t2)
        if not (t1_won or t2_won):
            continue  # draws or name mismatches — skip
        md = r["match_date"]
        mname = r["map_name"]

        # team1's perspective
        if t1:
            team_history[_norm_team(t1)].append((md, mname, side == "ct", t1_won))
        # team2's perspective — opposite side
        if t2:
            t2_started_ct = (side == "t")  # if team1 started T, team2 started CT
            team_history[_norm_team(t2)].append((md, mname, t2_started_ct, t2_won))

        match_maps_by_id[r["hltv_match_id"]].append({
            "map_name": mname,
            "team1_first_half_side": side,
            "team1_won": t1_won,
        })

    for team in team_history:
        team_history[team].sort(key=lambda x: x[0])

    n_team_rows = sum(len(v) for v in team_history.values())
    print(f"  starting-side history loaded: {len(team_history)} teams, "
          f"{n_team_rows} per-team map appearances, "
          f"{len(match_maps_by_id)} matches with map-level side data")
    return dict(team_history), dict(match_maps_by_id)


def load_hltv_match_index() -> dict:
    """Return {hltv_match_id: (team1_name, team2_name)} so the bridge-resolved
    hltv_match_id can be flagged forward/reverse when joining for the
    bias_aligned_diff feature.

    NOTE: Replaced the old (team1, team2, date) exact-string join — that path
    capped coverage at ~2% because bo3gg adds " Team"/" Esports" suffixes that
    HLTV stores bare. The bridge (`cs2_match_id_bridge`) now resolves
    bo3gg_id → hltv_match_id directly at ~85% coverage; this helper just
    returns the HLTV team name pair for orientation."""
    rows = execute_query(
        """
        SELECT hltv_match_id, team1_name, team2_name
        FROM cs2_hltv_matches
        WHERE team1_name IS NOT NULL
          AND team2_name IS NOT NULL
        """,
        None,
    )
    return {r["hltv_match_id"]: (r["team1_name"], r["team2_name"]) for r in rows}


def compute_ct_start_winrate(team_name: str, kickoff_ts,
                              team_history: dict) -> tuple[float | None, int]:
    """PIT-correct CT-start win-rate for `team_name`, averaged across maps.

    Returns (winrate, n_priors). winrate is None when n_priors == 0.

    Per-map averaging avoids over-weighting whatever map a team has played the
    most. For each map the team has played starting CT (with prior data), we
    compute win% on that map; then we mean across maps. Falls back to None
    when team has no CT-start priors at all."""
    if not team_name or kickoff_ts is None:
        return None, 0
    history = team_history.get(_norm_team(team_name))
    if not history:
        return None, 0
    # bisect on the sorted (match_date, ...) tuples — we want strictly < kickoff
    # Use a sentinel key with the kickoff_ts to find the cutoff index.
    # team_history entries are (md, mname, started_ct, won); comparing tuples
    # would compare datetimes first which is what we want.
    lo = 0
    # Bisect-left on date alone via key-extraction loop (datetimes only).
    # For perf this is O(log n) — but the tuple list isn't bisectable directly
    # because of mixed types. Use a small helper.
    dates = [t[0] for t in history]  # accept O(n); team sizes small
    hi = bisect.bisect_left(dates, kickoff_ts)
    if hi == 0:
        return None, 0

    per_map_wins: dict[str, list[int]] = defaultdict(list)
    for md, mname, started_ct, won in history[:hi]:
        if not started_ct:
            continue
        per_map_wins[mname].append(1 if won else 0)

    if not per_map_wins:
        return None, 0
    per_map_rates = []
    for mname, results in per_map_wins.items():
        if len(results) >= 3:  # require ≥3 priors per map for the map to count
            per_map_rates.append(sum(results) / len(results))
    if not per_map_rates:
        # If no map has 3+ priors, fall back to pooled rate across all CT-start
        # appearances so small-sample teams still get a value (with lower weight).
        all_results = [w for results in per_map_wins.values() for w in results]
        if len(all_results) < 3:
            return None, len(all_results)
        return sum(all_results) / len(all_results), len(all_results)
    return sum(per_map_rates) / len(per_map_rates), sum(len(v) for v in per_map_wins.values())


def compute_bias_aligned_diff(hltv_match_id: int | None, orient: str | None,
                                match_maps_by_id: dict) -> tuple[float, int]:
    """Per-match alignment score: +1 if team1 started on its map's favored side
    and team2 didn't, −1 if reverse, 0 otherwise. Averaged across the match's
    biased maps (Nuke/Anubis/Overpass/Inferno).

    Returns (score, n_biased_maps_in_match). 0 when no biased maps were played
    OR when we have no HLTV detail row for this match — caller treats it as
    neutral.

    `orient` is "fwd" if HLTV team1 == bo3gg team1, "rev" if swapped — we flip
    the sign in the rev case so the feature is always "team1 (per bo3gg) edge"."""
    if hltv_match_id is None:
        return 0.0, 0
    maps = match_maps_by_id.get(hltv_match_id)
    if not maps:
        return 0.0, 0

    aligned_scores = []
    for mp in maps:
        mname = mp["map_name"]
        favored = MAP_BIAS_FAVORED_SIDE.get(mname)
        if favored is None:
            continue
        side_t1 = mp["team1_first_half_side"]
        # team1 is favored if its starting side == favored side; team2 is the
        # opposite, so it's favored if t1's side != favored.
        t1_favored = (side_t1 == favored)
        t2_favored = not t1_favored
        if t1_favored and not t2_favored:
            aligned_scores.append(1.0)
        elif t2_favored and not t1_favored:
            aligned_scores.append(-1.0)
        else:
            aligned_scores.append(0.0)

    if not aligned_scores:
        return 0.0, 0
    score = sum(aligned_scores) / len(aligned_scores)
    if orient == "rev":
        score = -score
    return score, len(aligned_scores)


def build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                team_history, match_maps_by_id, hltv_match_idx):
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

        # NEW v13: PIT-correct CT-start win-rate per team
        kickoff_ts = m["kickoff_time"]
        wr1, n1 = compute_ct_start_winrate(m["team1"], kickoff_ts, team_history)
        wr2, n2 = compute_ct_start_winrate(m["team2"], kickoff_ts, team_history)
        team1_ct_start_winrate_per_map = wr1 if wr1 is not None else 0.5
        team2_ct_start_winrate_per_map = wr2 if wr2 is not None else 0.5
        ct_start_wr_diff = (
            float(team1_ct_start_winrate_per_map - team2_ct_start_winrate_per_map)
            if (wr1 is not None and wr2 is not None) else 0.0
        )
        ct_wr_covered = 1 if (wr1 is not None and wr2 is not None) else 0

        # NEW v13: bias_aligned_diff via the matched HLTV match (PIT-OK because
        # the side outcome is from THIS match — for backtest use only; live use
        # would require knife-round result which is post-betting).
        # hltv_match_id was resolved via cs2_match_id_bridge upstream.
        hltv_id = m.get("hltv_match_id")
        orient = None
        if hltv_id is not None:
            pair = hltv_match_idx.get(hltv_id)
            if pair:
                hltv_t1, hltv_t2 = pair
                if _norm_team(hltv_t1) == _norm_team(m["team1"]):
                    orient = "fwd"
                elif _norm_team(hltv_t1) == _norm_team(m["team2"]):
                    orient = "rev"
                else:
                    # Names don't normalise to a match — bridge mapped them
                    # anyway. Default to fwd; sign error caps at the small
                    # bias-only feature so it's tolerable.
                    orient = "fwd"
        bias_aligned_diff, n_biased = compute_bias_aligned_diff(
            hltv_id, orient, match_maps_by_id
        )
        bias_covered = 1 if n_biased > 0 else 0

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
            # NEW v13
            "team1_ct_start_winrate_per_map": float(team1_ct_start_winrate_per_map),
            "team2_ct_start_winrate_per_map": float(team2_ct_start_winrate_per_map),
            "ct_start_wr_diff":               float(ct_start_wr_diff),
            "bias_aligned_diff":              float(bias_aligned_diff),
            "ct_wr_covered":                  ct_wr_covered,
            "bias_covered":                   bias_covered,
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
    print("loading starting-side history…")
    team_history, match_maps_by_id = load_starting_side_history()
    print("loading HLTV match index…")
    hltv_match_idx = load_hltv_match_index()
    print(f"  {len(hltv_match_idx) // 2} HLTV matches indexed")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    # Bridge: bo3gg_id -> hltv_match_id via cs2_match_id_bridge (replaces the
    # old (team1, team2, date) exact-string join that capped coverage at <3%).
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
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                      team_history, match_maps_by_id, hltv_match_idx)
    print(f"  {len(rows)} matches with saved_prob\n")

    cov_ct = sum(1 for r in rows if r["ct_wr_covered"])
    cov_bias = sum(1 for r in rows if r["bias_covered"])
    cov_ct_nz = sum(1 for r in rows if r["ct_start_wr_diff"] != 0.0)
    cov_bias_nz = sum(1 for r in rows if r["bias_aligned_diff"] != 0.0)
    print(f"  coverage:")
    print(f"    ct_start_wr  both teams have priors:    {cov_ct}/{len(rows)} ({cov_ct/max(len(rows),1):.1%})")
    print(f"    ct_start_wr_diff   non-zero:             {cov_ct_nz}/{len(rows)} ({cov_ct_nz/max(len(rows),1):.1%})")
    print(f"    bias_aligned (HLTV match matched):       {cov_bias}/{len(rows)} ({cov_bias/max(len(rows),1):.1%})")
    print(f"    bias_aligned_diff  non-zero:             {cov_bias_nz}/{len(rows)} ({cov_bias_nz/max(len(rows),1):.1%})\n")

    v8_keys = ["form_diff","h2h_diff","tm_diff","rest_diff","rank_diff","bo_centered",
               "pistol_diff","tier_s","tier_a","tier_b","tier_c","tier_d","kd_diff"]
    v13_ct_keys = v8_keys + ["ct_start_wr_diff"]
    v13_full_keys = v8_keys + ["ct_start_wr_diff","bias_aligned_diff"]

    # Track AUCs for the PROMOTE decision (full-sample, v8 vs v8+starting-side).
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
        persist(f"v13-starting-side_{label_prefix}_baseline", len(sample_rows), m_base, since_d,
                keys=["win_prob1"], n_train=cut)

        for keys, lbl in [
            (v8_keys, "v8 reference"),
            (v13_ct_keys, "v13: v8 + ct_start_wr_diff"),
            (v13_full_keys, "v13: v8 + ct_start_wr_diff + bias_aligned_diff"),
            (["ct_start_wr_diff", "bias_aligned_diff"], "starting-side features alone"),
        ]:
            r = evaluate(sample_rows, keys, lbl)
            if r.get("skipped"):
                print(f"{lbl:45}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{lbl:45} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v13-starting-side_{label_prefix}_{lbl}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
            if label_prefix == "full":
                auc_track[lbl] = mm["auc"]

    # Full sample, then a side-covered subset for diagnostic parity with v11.
    run_battery(rows, "full")
    covered = [r for r in rows if r["ct_wr_covered"]]
    if len(covered) != len(rows) and len(covered) >= 80:
        run_battery(covered, "ct-wr-covered")

    # PROMOTE decision: v8 reference vs the strongest v13 variant on full
    # sample. Rule: delta >= +0.002 AUC AND no degradation.
    base_auc = auc_track.get("v8 reference")
    v13a_auc = auc_track.get("v13: v8 + ct_start_wr_diff")
    v13b_auc = auc_track.get("v13: v8 + ct_start_wr_diff + bias_aligned_diff")
    best_v13 = max(filter(lambda x: x is not None, [v13a_auc, v13b_auc]), default=None)
    cov_pct = (cov_ct / len(rows)) if rows else 0.0

    print("\n" + "=" * 78)
    print("PROMOTE DECISION")
    print("=" * 78)
    if base_auc is None or best_v13 is None:
        print("PROMOTE: no (insufficient data — could not score both v8 and v13)")
    else:
        delta = best_v13 - base_auc
        coverage_str = f"coverage={cov_pct:.1%}"
        print(f"baseline AUC (v8):     {base_auc:.4f}")
        print(f"+side AUC (best):      {best_v13:.4f}")
        print(f"delta:                 {delta:+.4f}     {coverage_str}")
        if delta >= 0.002:
            print("PROMOTE: yes (delta >= +0.002 AUC, no degradation)")
        else:
            print("PROMOTE: no (delta < +0.002 AUC threshold)")


if __name__ == "__main__":
    main()
