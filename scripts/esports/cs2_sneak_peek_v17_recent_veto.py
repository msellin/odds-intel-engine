"""
CS2 sneak-peek v17-recent-veto — v8 + 3 RECENT veto-pattern features.

Background: v10 added veto features using all-time permaban frequency and
posted small AUC deltas. A follow-up sneak peek that tried all-time
permaban-frequency concentration failed (−0.0099 AUC) — the all-time average
washes out current meta. v17 asks the opposite question: does the LAST
~30 veto sessions / 90 days of a team carry signal that the all-time aggregate
loses? Teams adapt their veto strategy as the meta shifts (new map drops,
new player joins, opponent scout reports).

New features (PIT-correct, strict match_date < kickoff_time):

  - recent_permaban_concentration_diff
        For each team, scan their last 30 veto sessions in the last 90 days,
        count permabans (action='removed') per map, take the share of the #1
        most-banned map over total recent permabans. Higher = team is more
        predictable in the current meta. Diff = team1 − team2.
        Positive = team1 more predictable than team2.

  - recent_decider_winrate_recent30_diff
        For each team, look at their last 30 *deciders* in the last 90 days
        (= the last map of a series where map_order = total maps). Compute
        win-rate on those deciders only. Diff = team1 − team2.

  - recent_perm_overlap_diff
        Jaccard overlap (intersection / union) between team1's recent top-3
        permabans and team2's recent top-3 permabans, derived from the same
        last-30/last-90d veto stream. Single feature for the PAIR (not a
        diff). Lower overlap = teams force each other off their comfort maps
        → veto fight is "loud". Higher overlap = veto fight is mostly
        cooperative (both want to ban Anubis), no real information.

Recency rule: per team we walk their veto stream backward and take up to 30
veto sessions whose match_date >= kickoff − 90d. Whichever bound hits first
(30-session cap OR 90-day cutoff) wins. This is the "recent" hypothesis.

PIT: each team's veto stream is sorted by match_date and bisected; only
sessions with match_date < kickoff are eligible. Recent_decider uses the
same bisect.

Run:
    python3 scripts/esports/cs2_sneak_peek_v17_recent_veto.py [--since 2025-06-01]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
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
# Reuse v16's HLTV team-name resolver — without it, v17's veto/decider
# streams (keyed by HLTV team_name from cs2_hltv_match_veto) silently miss
# on bo3gg/HLTV name diffs ('FaZe Clan' vs 'FaZe') and coverage collapses
# to <2% even though the bridge resolves ~86% of matches.
from cs2_sneak_peek_v16_mappool import (  # type: ignore  # noqa: E402
    load_hltv_team_pair_index,
    resolve_hltv_name,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())

# Recency window — both bounds enforced; whichever bites first wins.
RECENT_SESSIONS_MAX = 30   # last N veto sessions per team
RECENT_DAYS = 90           # OR within last 90 days, whichever is smaller


V8_KEYS = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
           "bo_centered", "pistol_diff",
           "tier_s", "tier_a", "tier_b", "tier_c", "tier_d", "kd_diff"]

V17_CONC_KEY = ["recent_permaban_concentration_diff"]
V17_DEC_KEY  = ["recent_decider_winrate_recent30_diff"]
V17_OVL_KEY  = ["recent_perm_overlap_diff"]


# ── Loaders ─────────────────────────────────────────────────────────
def load_recent_veto_streams() -> dict:
    """{team_name: sorted list of (match_date, hltv_match_id, [permaban maps])}
    — every veto session a team participated in, in date order.

    Each entry collapses the multi-row veto for one match into the list of
    that team's permabans (action='removed', step in (1,2)). Date is the
    parent match's match_date so PIT bisect works the same way as v10/v16."""
    rows = execute_query("""
        SELECT v.hltv_match_id, v.step, v.team_name, v.action, v.map_name,
               m.match_date
        FROM cs2_hltv_match_veto v
        JOIN cs2_hltv_matches m ON m.hltv_match_id = v.hltv_match_id
        WHERE v.team_name IS NOT NULL AND v.map_name IS NOT NULL
          AND m.match_date IS NOT NULL
        ORDER BY m.match_date, v.hltv_match_id, v.step
    """)
    # Group rows by (team, match_id), collect each team's permabans per match.
    by_match: dict = defaultdict(lambda: defaultdict(list))  # md_id -> team -> [maps]
    md_by_match: dict = {}
    for r in rows:
        mid = r["hltv_match_id"]
        md_by_match[mid] = r["match_date"]
        if r["action"] == "removed" and r["step"] in (1, 2):
            by_match[mid][r["team_name"]].append(r["map_name"])
    # Flatten to per-team session list, sorted by date.
    streams: dict = defaultdict(list)
    for mid, team_maps in by_match.items():
        md = md_by_match[mid]
        for team_name, perm_maps in team_maps.items():
            streams[team_name].append((md, mid, perm_maps))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


def load_recent_decider_streams() -> dict:
    """{team_name: sorted list of (match_date, won_bool)} — deciders only.

    Decider = the last map of a series where map_order == total maps in the
    series. Detected via window over cs2_hltv_match_maps grouped by
    hltv_match_id. Used by recent_decider_winrate_recent30_diff."""
    rows = execute_query("""
        WITH series_len AS (
            SELECT hltv_match_id, MAX(map_order) AS total_maps
            FROM cs2_hltv_match_maps
            WHERE map_order IS NOT NULL
            GROUP BY hltv_match_id
        )
        SELECT m.match_date, m.team1_name AS t1, m.team2_name AS t2,
               mm.map_order, sl.total_maps, mm.winner_name
        FROM cs2_hltv_match_maps mm
        JOIN cs2_hltv_matches m  ON m.hltv_match_id = mm.hltv_match_id
        JOIN series_len      sl ON sl.hltv_match_id = mm.hltv_match_id
        WHERE mm.map_order IS NOT NULL
          AND mm.map_order = sl.total_maps
          AND sl.total_maps >= 2
          AND mm.winner_name IS NOT NULL
          AND m.match_date  IS NOT NULL
    """)
    streams: dict = defaultdict(list)
    for r in rows:
        md = r["match_date"]
        for team_name in (r["t1"], r["t2"]):
            if not team_name:
                continue
            streams[team_name].append((md, r["winner_name"] == team_name))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


# ── PIT helpers ─────────────────────────────────────────────────────
def _prior_index(stream: list, kickoff) -> int:
    """How many entries of stream have match_date < kickoff (bisect)."""
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


def _recent_window(stream: list, kickoff,
                   sessions_max: int = RECENT_SESSIONS_MAX,
                   days: int = RECENT_DAYS) -> list:
    """Return up to last `sessions_max` entries of stream with
    match_date in [kickoff − days, kickoff). PIT-safe (strict <)."""
    end = _prior_index(stream, kickoff)
    if end == 0:
        return []
    cutoff = kickoff - timedelta(days=days)
    start_by_count = max(0, end - sessions_max)
    # Find the smallest i such that stream[i][0] >= cutoff. Use bisect_left
    # on a synthetic list of dates — but our entries are tuples so we walk.
    lo, hi = 0, end
    while lo < hi:
        mid = (lo + hi) // 2
        if stream[mid][0] < cutoff:
            lo = mid + 1
        else:
            hi = mid
    start_by_date = lo  # index of first entry >= cutoff
    start = max(start_by_count, start_by_date)
    return stream[start:end]


# ── Feature functions ───────────────────────────────────────────────
def recent_permaban_concentration(team: str, kickoff,
                                  veto_streams: dict) -> float | None:
    """Share of total recent permabans that fall on the team's #1 banned map.

    Higher = team always bans the same map → more predictable. Returns None
    when fewer than 3 recent permabans (signal too noisy)."""
    s = veto_streams.get(team)
    if not s:
        return None
    window = _recent_window(s, kickoff)
    if not window:
        return None
    cnt: Counter = Counter()
    for _md, _mid, perm_maps in window:
        for mp in perm_maps:
            cnt[mp] += 1
    total = sum(cnt.values())
    if total < 3:
        return None
    top_count = cnt.most_common(1)[0][1]
    return top_count / total


def recent_top_n_permabans(team: str, kickoff, veto_streams: dict,
                            n: int = 3) -> list[str] | None:
    """Team's recent top-n permaban maps (last 30 sessions / last 90 days).
    Returns None when team has no recent veto signal."""
    s = veto_streams.get(team)
    if not s:
        return None
    window = _recent_window(s, kickoff)
    if not window:
        return None
    cnt: Counter = Counter()
    for _md, _mid, perm_maps in window:
        for mp in perm_maps:
            cnt[mp] += 1
    if not cnt:
        return None
    return [mp for mp, _ in cnt.most_common(n)]


def recent_decider_winrate(team: str, kickoff, decider_streams: dict) -> float | None:
    """Win-rate on team's recent deciders (last 30 / last 90d). Returns None
    if fewer than 3 deciders in the window."""
    s = decider_streams.get(team)
    if not s:
        return None
    window = _recent_window(s, kickoff)
    if len(window) < 3:
        return None
    wins = sum(1 for _md, w in window if w)
    return wins / len(window)


def jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard = |A ∩ B| / |A ∪ B|. Returns 0.0 if both empty (no signal)."""
    sa, sb = set(a or []), set(b or [])
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ── Row builder ─────────────────────────────────────────────────────
def build_rows(matches, *, tm, pistol, tier_map, kd_map, direct,
               veto_streams, decider_streams, hltv_pairs):
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

        kickoff = m["kickoff_time"]

        # ── v17 recent veto features ─────────────────────────────────
        # Veto/decider streams are keyed by HLTV team_name (from
        # cs2_hltv_match_veto / cs2_hltv_match_maps). bo3gg names often
        # differ ('FaZe Clan' vs 'FaZe', 'paiN Gaming' vs 'paiN'), so we
        # resolve through the HLTV match-pair index using the same helper
        # as v16. Without this resolver coverage collapses to <2%.
        hltv_id = m.get("hltv_match_id")
        pair = hltv_pairs.get(hltv_id) if hltv_id is not None else None
        t1 = resolve_hltv_name(m["team1"], pair) or m["team1"]
        t2 = resolve_hltv_name(m["team2"], pair) or m["team2"]

        c1 = recent_permaban_concentration(t1, kickoff, veto_streams)
        c2 = recent_permaban_concentration(t2, kickoff, veto_streams)
        if c1 is not None and c2 is not None:
            conc_diff = float(c1 - c2)
            conc_covered = 1
        else:
            conc_diff = 0.0
            conc_covered = 0

        d1w = recent_decider_winrate(t1, kickoff, decider_streams)
        d2w = recent_decider_winrate(t2, kickoff, decider_streams)
        if d1w is not None and d2w is not None:
            dec_diff = float(d1w - d2w)
            dec_covered = 1
        else:
            dec_diff = 0.0
            dec_covered = 0

        top1 = recent_top_n_permabans(t1, kickoff, veto_streams)
        top2 = recent_top_n_permabans(t2, kickoff, veto_streams)
        if top1 is not None and top2 is not None:
            ovl = jaccard(top1, top2)
            ovl_covered = 1
        else:
            ovl = 0.0
            ovl_covered = 0

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
            # v17 features
            "recent_permaban_concentration_diff":   conc_diff,
            "recent_decider_winrate_recent30_diff": dec_diff,
            "recent_perm_overlap_diff":             ovl,
            "conc_covered": conc_covered,
            "dec_covered":  dec_covered,
            "ovl_covered":  ovl_covered,
            # Diagnostic — keep top-3 for the notable-finding stat
            "top1_perms": top1, "top2_perms": top2,
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

    print("loading recent veto streams…")
    veto_streams = load_recent_veto_streams()
    print(f"  {len(veto_streams)} teams with veto history")
    print("loading recent decider streams…")
    decider_streams = load_recent_decider_streams()
    print(f"  {len(decider_streams)} teams with decider history")
    print("loading hltv team-pair index…")
    hltv_pairs = load_hltv_team_pair_index()
    print(f"  {len(hltv_pairs)} HLTV matches indexed")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    print(f"  {len(matches)} candidate matches")
    # Bridge is loaded so the smoke test pins the standard pattern and so
    # future joiners can hop to HLTV match-side data if needed. It is not
    # required for v17's three core features (they all key off team_name,
    # not hltv_match_id), but keeping the pattern consistent prevents
    # drift from v15/v16.
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
                      veto_streams=veto_streams,
                      decider_streams=decider_streams,
                      hltv_pairs=hltv_pairs)
    print(f"  {len(rows)} matches with saved_prob\n")

    n = max(len(rows), 1)
    conc_cov = sum(1 for r in rows if r["conc_covered"])
    dec_cov  = sum(1 for r in rows if r["dec_covered"])
    ovl_cov  = sum(1 for r in rows if r["ovl_covered"])
    print(f"  coverage:")
    print(f"    recent_permaban_concentration_diff:   {conc_cov}/{n} ({conc_cov/n:.1%})")
    print(f"    recent_decider_winrate_recent30_diff: {dec_cov}/{n} ({dec_cov/n:.1%})")
    print(f"    recent_perm_overlap_diff:             {ovl_cov}/{n} ({ovl_cov/n:.1%})\n")

    # ── Notable finding: how often does a team's top-3 permabans change ──
    # Compute per-team consistency by walking each team's veto stream in two
    # 90-day windows ~30 days apart and reporting Jaccard between top-3
    # sets. Diagnostic only — does not feed model.
    diag_jaccards = []
    for team, s in veto_streams.items():
        if len(s) < 10:
            continue
        # Use last record as a synthetic "kickoff" for window A,
        # and 30 days before that for window B.
        ref = s[-1][0]
        windowA = _recent_window(s, ref)
        if not windowA:
            continue
        # Window B = 30 days earlier — use a fake kickoff = ref − 30d.
        # That same _recent_window helper already enforces 90-day window
        # and 30-session cap from THAT pseudo-kickoff backward.
        prior_ref = ref - timedelta(days=30)
        windowB = _recent_window(s, prior_ref)
        if not windowB:
            continue
        a_cnt: Counter = Counter()
        for _md, _mid, perm_maps in windowA:
            for mp in perm_maps:
                a_cnt[mp] += 1
        b_cnt: Counter = Counter()
        for _md, _mid, perm_maps in windowB:
            for mp in perm_maps:
                b_cnt[mp] += 1
        topA = [mp for mp, _ in a_cnt.most_common(3)]
        topB = [mp for mp, _ in b_cnt.most_common(3)]
        if topA and topB:
            diag_jaccards.append(jaccard(topA, topB))
    if diag_jaccards:
        avg_j = float(np.mean(diag_jaccards))
        share_flipped = float(np.mean([j < 1.0 for j in diag_jaccards]))
        print(f"  [diag] team top-3 permaban stability over a ~30d shift in "
              f"the 90d window:")
        print(f"    teams sampled:       {len(diag_jaccards)}")
        print(f"    avg Jaccard:         {avg_j:.3f} "
              f"(1.0 = unchanged, 0.0 = completely different)")
        print(f"    share with any flip: {share_flipped:.1%}\n")

    # ── Walk-forward eval ──
    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v17-recent-veto (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 83)
    print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v17-recent-veto_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    blocks = [
        (V8_KEYS, "v8 reference"),
        (V8_KEYS + V17_CONC_KEY, "v17: v8 + recent_permaban_concentration"),
        (V8_KEYS + V17_DEC_KEY,  "v17: v8 + recent_decider_winrate_recent30"),
        (V8_KEYS + V17_OVL_KEY,  "v17: v8 + recent_perm_overlap"),
        (V8_KEYS + V17_CONC_KEY + V17_DEC_KEY + V17_OVL_KEY,
         "v17-recent-veto ALL (v8 + 3 recent)"),
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
        persist(f"v17-recent-veto_{lbl}", r["n"], mm, since_d,
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
        ("v17: v8 + recent_permaban_concentration",   conc_cov, "recent_permaban_concentration_diff"),
        ("v17: v8 + recent_decider_winrate_recent30", dec_cov,  "recent_decider_winrate_recent30_diff"),
        ("v17: v8 + recent_perm_overlap",             ovl_cov,  "recent_perm_overlap_diff"),
        ("v17-recent-veto ALL (v8 + 3 recent)",       min(conc_cov, dec_cov, ovl_cov), "ALL"),
    ]:
        block_auc = auc_track.get(lbl)
        if block_auc is None:
            print(f"  {key_name:40}  SKIPPED")
            continue
        delta = block_auc - base_auc
        cov_pct = cov / n
        verdict = "PROMOTE" if delta >= 0.002 else "no"
        print(f"  {key_name:40}  AUC={block_auc:.4f} (Δ {delta:+.4f})  "
              f"cov={cov_pct:.1%}  → {verdict}")


if __name__ == "__main__":
    main()
