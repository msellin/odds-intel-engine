"""
CS2 sneak-peek v16-map-pool — v8 + 3 features derived from team map-pool history.

Background: Valve periodically rotates the CS2 active duty map pool (adds new
maps, removes old). Teams with broader recent map play are better prepared
for whatever the current pool looks like; teams whose pool-age is "stale"
(haven't touched some active map in months) carry an information disadvantage.

New features (PIT-correct, strict < kickoff_time):

  - team_pool_familiarity_diff : how many DISTINCT maps each team has played
                                  in the last 90 days. Diff = team1 - team2.
                                  Positive = team1 has a broader recent pool.
  - pool_age_diff              : per team, median days since the team last
                                  played EACH map in the active pool. Lower
                                  = fresher. Diff = team1_median - team2_median.
                                  Negative = team1 is fresher overall.
  - new_map_played_diff        : has each team played the SPECIFIC first map
                                  of the upcoming match before? (map_order=1
                                  via cs2_hltv_match_maps for the eval match,
                                  team-prior matches on that map only count
                                  if match_date < kickoff). Diff = t1 - t2.
                                  Skipped from PROMOTE if coverage < 40%.

PIT discipline: all priors come from cs2_hltv_match_maps rows whose parent
match's match_date is strictly before the eval match's kickoff_time. The
bridge (`cs2_match_id_bridge`) joins bo3gg cs2_results → hltv_match_id so we
can look up the eval match's actual map list.

Run:
    python3 scripts/esports/cs2_sneak_peek_v16_mappool.py [--since 2025-06-01]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from cs2_sneak_peek_v5 import load_matches_with_features, load_team_map, _logit  # type: ignore  # noqa: E402
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore  # noqa: E402

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())

# Current CS2 active-duty pool — used by pool_age_diff.
# Source: HLTV active map ranking (top 7 by recent map play, see distribution
# query in build script). Default/Cache/Cobblestone/Tuscan filtered out.
ACTIVE_POOL = ("Ancient", "Mirage", "Nuke", "Inferno", "Dust2", "Anubis", "Overpass")

V8_KEYS = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
           "bo_centered", "pistol_diff",
           "tier_s", "tier_a", "tier_b", "tier_c", "tier_d", "kd_diff"]

V16_FAM_KEY = ["team_pool_familiarity_diff"]
V16_AGE_KEY = ["pool_age_diff"]
V16_NEW_KEY = ["new_map_played_diff"]


# ── Loaders ─────────────────────────────────────────────────────────
def load_team_map_history() -> dict:
    """{team_name: sorted list of (match_date, map_name)} — every map any
    team has played, from cs2_hltv_match_maps joined to cs2_hltv_matches.

    Each (team, map) appears once per match it featured in. List is sorted
    by match_date so bisect can find "matches strictly before kickoff" in
    O(log n)."""
    rows = execute_query("""
        SELECT m.match_date, m.team1_name AS t1, m.team2_name AS t2,
               mm.map_name
        FROM cs2_hltv_match_maps mm
        JOIN cs2_hltv_matches m ON m.hltv_match_id = mm.hltv_match_id
        WHERE mm.map_name IS NOT NULL
          AND m.match_date IS NOT NULL
    """)
    streams: dict = defaultdict(list)
    for r in rows:
        md = r["match_date"]
        mp = r["map_name"]
        for team_name in (r["t1"], r["t2"]):
            if team_name:
                streams[team_name].append((md, mp))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


def load_first_map_by_match() -> dict:
    """{hltv_match_id: map_name_of_first_map_played} — the map_order=1 row.
    Used by new_map_played_diff so we can ask "has each team played THIS
    specific upcoming map before?"."""
    rows = execute_query("""
        SELECT hltv_match_id, map_name
        FROM cs2_hltv_match_maps
        WHERE map_order = 1 AND map_name IS NOT NULL
    """)
    return {r["hltv_match_id"]: r["map_name"] for r in rows}


def load_hltv_team_pair_index() -> dict:
    """{hltv_match_id: (team1_name, team2_name)} from cs2_hltv_matches —
    needed because bo3gg team strings ('FaZe Clan', 'paiN Gaming') don't
    match the HLTV strings ('FaZe', 'paiN') stored in cs2_hltv_match_maps.
    Per-eval-match we resolve the HLTV name via this index + a fuzzy match."""
    rows = execute_query("""
        SELECT hltv_match_id, team1_name, team2_name
        FROM cs2_hltv_matches
        WHERE team1_name IS NOT NULL AND team2_name IS NOT NULL
    """)
    return {r["hltv_match_id"]: (r["team1_name"], r["team2_name"]) for r in rows}


def _norm_team(name: str) -> str:
    """Loose normalisation: lowercase, drop common suffixes, drop spaces."""
    if not name:
        return ""
    n = name.lower().strip()
    for suffix in (" esports", " esport", " gaming", " team", " clan", " club"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n.replace(" ", "")


def resolve_hltv_name(bo3gg_name: str, hltv_pair: tuple) -> str | None:
    """Pick whichever of hltv_pair matches bo3gg_name under loose
    normalisation. Returns None on no match (very rare — caller falls back
    to the raw bo3gg name)."""
    if not hltv_pair or not bo3gg_name:
        return None
    target = _norm_team(bo3gg_name)
    h1, h2 = hltv_pair
    if _norm_team(h1) == target:
        return h1
    if _norm_team(h2) == target:
        return h2
    # Substring fallback for "Natus Vincere" ↔ "NaVi" etc.
    if target and (_norm_team(h1).startswith(target) or target.startswith(_norm_team(h1))):
        return h1
    if target and (_norm_team(h2).startswith(target) or target.startswith(_norm_team(h2))):
        return h2
    return None


# ── Per-team PIT helpers ────────────────────────────────────────────
def _prior_index(stream: list, kickoff) -> int:
    """Return how many entries of stream have match_date < kickoff (bisect)."""
    if not stream:
        return 0
    lo, hi = 0, len(stream)
    while lo < hi:
        mid = (lo + hi) // 2
        if stream[mid][0] < kickoff:
            lo = mid + 1
        else:
            hi = mid
    return lo


def team_pool_familiarity(team: str, kickoff, streams: dict,
                          window_days: int = 90) -> int | None:
    """Distinct maps played by `team` in the [kickoff-90d, kickoff) window.
    Returns None if no priors at all."""
    s = streams.get(team)
    if not s:
        return None
    end = _prior_index(s, kickoff)
    if end == 0:
        return None
    start_threshold = kickoff - timedelta(days=window_days)
    # Walk left from end-1 while date >= threshold. Stream sorted ascending.
    distinct: set = set()
    for i in range(end - 1, -1, -1):
        if s[i][0] < start_threshold:
            break
        distinct.add(s[i][1])
    return len(distinct) if distinct else None


def team_pool_age_median(team: str, kickoff, streams: dict,
                        pool=ACTIVE_POOL) -> float | None:
    """Median days since `team` last played each map in `pool`. Maps the team
    has never played count as `cap_days` (set to window cap, here 365)."""
    s = streams.get(team)
    if not s:
        return None
    end = _prior_index(s, kickoff)
    if end == 0:
        return None
    last_seen: dict = {}
    # Scan once from the most recent prior backward, recording the first
    # (=newest) hit per map. Stop after every map in the pool found OR the
    # whole prior history exhausted.
    for i in range(end - 1, -1, -1):
        mp = s[i][1]
        if mp in pool and mp not in last_seen:
            last_seen[mp] = s[i][0]
        if len(last_seen) == len(pool):
            break
    cap_days = 365.0
    ages: list[float] = []
    for mp in pool:
        if mp in last_seen:
            delta = (kickoff - last_seen[mp]).total_seconds() / 86400.0
            ages.append(min(delta, cap_days))
        else:
            ages.append(cap_days)
    # Require at least 3 maps in pool seen to call it covered.
    seen_in_pool = sum(1 for mp in pool if mp in last_seen)
    if seen_in_pool < 3:
        return None
    return float(statistics.median(ages))


def team_has_played_map(team: str, map_name: str, kickoff,
                        streams: dict) -> int | None:
    """1 if `team` has played `map_name` strictly before `kickoff`, else 0.
    Returns None if team has no prior matches at all (so the row isn't
    spuriously counted as 'team never played the map' when really we have
    no signal)."""
    s = streams.get(team)
    if not s:
        return None
    end = _prior_index(s, kickoff)
    if end == 0:
        return None
    for i in range(end):
        if s[i][1] == map_name:
            return 1
    return 0


# ── Row builder ─────────────────────────────────────────────────────
def build_rows(matches, *, tm, pistol, tier_map, kd_map, direct,
               team_map_history, first_map_by_match, hltv_pairs):
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
        rest_diff = (min(float(m["t1_days_since"]), 30.0)
                     - min(float(m["t2_days_since"]), 30.0)) / 30.0
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

        # ── v16 map-pool features
        kickoff = m["kickoff_time"]
        # Resolve HLTV-side names via bridge so we can look up team_map_history
        # (which is keyed by HLTV team_name from cs2_hltv_matches). The fallback
        # is the raw bo3gg name — works for teams whose HLTV name = bo3gg name.
        hltv_id = m.get("hltv_match_id")
        pair = hltv_pairs.get(hltv_id) if hltv_id is not None else None
        t1 = resolve_hltv_name(m["team1"], pair) or m["team1"]
        t2 = resolve_hltv_name(m["team2"], pair) or m["team2"]

        # 1) familiarity (last 90d distinct maps)
        fam1 = team_pool_familiarity(t1, kickoff, team_map_history)
        fam2 = team_pool_familiarity(t2, kickoff, team_map_history)
        if fam1 is not None and fam2 is not None:
            team_pool_familiarity_diff = float(fam1 - fam2)
            fam_covered = 1
        else:
            team_pool_familiarity_diff = 0.0
            fam_covered = 0

        # 2) pool-age median (days since last played each pool map)
        age1 = team_pool_age_median(t1, kickoff, team_map_history)
        age2 = team_pool_age_median(t2, kickoff, team_map_history)
        if age1 is not None and age2 is not None:
            # normalise — divide by 90 so coefficients land in a sane range
            pool_age_diff = float(age1 - age2) / 90.0
            age_covered = 1
        else:
            pool_age_diff = 0.0
            age_covered = 0

        # 3) new_map_played — only available when we have the eval match's
        #    bridge-resolved hltv_match_id AND a map_order=1 row for it.
        new_map_played_diff = 0.0
        new_covered = 0
        if hltv_id is not None:
            first_map = first_map_by_match.get(hltv_id)
            if first_map is not None:
                p1m = team_has_played_map(t1, first_map, kickoff, team_map_history)
                p2m = team_has_played_map(t2, first_map, kickoff, team_map_history)
                if p1m is not None and p2m is not None:
                    new_map_played_diff = float(p1m - p2m)
                    new_covered = 1

        out.append({
            "kickoff": kickoff, "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            # v16
            "team_pool_familiarity_diff": team_pool_familiarity_diff,
            "pool_age_diff":              pool_age_diff,
            "new_map_played_diff":        new_map_played_diff,
            "fam_covered": fam_covered,
            "age_covered": age_covered,
            "new_covered": new_covered,
        })
    return out


# ── Eval / persist ──────────────────────────────────────────────────
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
    print("loading team map history…");  team_map_history = load_team_map_history()
    print(f"  {len(team_map_history)} teams with map history")
    print("loading first-map index…");  first_map_by_match = load_first_map_by_match()
    print(f"  {len(first_map_by_match)} matches with map_order=1")
    print("loading hltv team-pair index…");  hltv_pairs = load_hltv_team_pair_index()
    print(f"  {len(hltv_pairs)} HLTV matches indexed")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    print(f"  {len(matches)} candidate matches")
    print(f"  enriching matches with hltv_match_id via cs2_match_id_bridge…")
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

    rows = build_rows(matches,
                      tm=tm, pistol=pistol, tier_map=tier_map,
                      kd_map=kd_map, direct=direct,
                      team_map_history=team_map_history,
                      first_map_by_match=first_map_by_match,
                      hltv_pairs=hltv_pairs)
    print(f"  {len(rows)} matches with saved_prob\n")

    n = max(len(rows), 1)
    fam_cov = sum(1 for r in rows if r["fam_covered"])
    age_cov = sum(1 for r in rows if r["age_covered"])
    new_cov = sum(1 for r in rows if r["new_covered"])
    print(f"  coverage:")
    print(f"    team_pool_familiarity_diff: {fam_cov}/{n} ({fam_cov/n:.1%})")
    print(f"    pool_age_diff:              {age_cov}/{n} ({age_cov/n:.1%})")
    print(f"    new_map_played_diff:        {new_cov}/{n} ({new_cov/n:.1%})\n")

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v16-map-pool (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 83)
    print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v16-map-pool_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    blocks = [
        (V8_KEYS, "v8 reference"),
        (V8_KEYS + V16_FAM_KEY, "v16: v8 + team_pool_familiarity"),
        (V8_KEYS + V16_AGE_KEY, "v16: v8 + pool_age"),
        (V8_KEYS + V16_NEW_KEY, "v16: v8 + new_map_played"),
        (V8_KEYS + V16_FAM_KEY + V16_AGE_KEY, "v16: v8 + familiarity + age"),
        (V8_KEYS + V16_FAM_KEY + V16_AGE_KEY + V16_NEW_KEY,
         "v16-map-pool ALL (v8 + 3 map-pool)"),
    ]
    for keys, lbl in blocks:
        r = evaluate(rows, keys, lbl)
        if r.get("skipped"):
            print(f"{lbl:50}  (skipped, n={r['n']})")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{lbl:50} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(f"v16-map-pool_{lbl}", r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
        auc_track[lbl] = mm["auc"]

    # PROMOTE — per-block recommendation against v8 reference.
    base_auc = auc_track.get("v8 reference")
    print("\n" + "=" * 83)
    print("PROMOTE DECISION (per feature block, vs v8 reference)")
    print("=" * 83)
    if base_auc is None:
        print("PROMOTE: cannot evaluate — v8 reference run was skipped")
        return

    print(f"v8 reference AUC: {base_auc:.4f}")
    for lbl, cov, key_name in [
        ("v16: v8 + team_pool_familiarity",        fam_cov, "team_pool_familiarity_diff"),
        ("v16: v8 + pool_age",                     age_cov, "pool_age_diff"),
        ("v16: v8 + new_map_played",               new_cov, "new_map_played_diff"),
        ("v16-map-pool ALL (v8 + 3 map-pool)",     min(fam_cov, age_cov), "ALL"),
    ]:
        block_auc = auc_track.get(lbl)
        if block_auc is None:
            print(f"  {key_name:30}  SKIPPED")
            continue
        delta = block_auc - base_auc
        cov_pct = cov / n
        # Per-task spec: skip new_map_played from PROMOTE if cov < 40%.
        if key_name == "new_map_played_diff" and cov_pct < 0.40:
            print(f"  {key_name:30}  AUC={block_auc:.4f} (Δ {delta:+.4f})  "
                  f"cov={cov_pct:.1%} — SKIP (coverage < 40%)")
            continue
        verdict = "PROMOTE" if delta >= 0.002 else "no"
        print(f"  {key_name:30}  AUC={block_auc:.4f} (Δ {delta:+.4f})  "
              f"cov={cov_pct:.1%}  → {verdict}")


if __name__ == "__main__":
    main()
