"""
CS2 sneak-peek v18-pistols — v8 + per-team-per-map pistol-round splits.

Background: v8 already has `pistol_diff` from cs2_team_pistol_stats, which is
the team-level pistol win-pct BLENDED across every map the team played. v18
asks whether the per-map breakdown adds signal that the aggregate washes out:

  - Vitality might be 70% on Mirage CT pistols but 40% on Nuke CT pistols.
    If the upcoming match is on Mirage, the Mirage-specific number is sharper
    than the blended 55%.
  - A team that's broadly weak in CT pistols but elite on T pistols carries
    different round-1 expectations depending on which side they're on for
    each map — the aggregate hides this.

Three new features (PIT-correct, only periods with period_end < kickoff_date):

  team1_ct_pistol_diff_thismap — team1 CT pistol % minus team2 CT pistol %
                                  on the upcoming match's MAIN map (map_order=1
                                  from cs2_hltv_match_maps, joined via the
                                  cs2_match_id_bridge).
  team1_t_pistol_diff_thismap  — same, T pistol %.
  pistol_per_map_avg_diff      — averaged across every map in the team's
                                  pool (using both CT and T per-map pistol),
                                  team1 minus team2. Captures broad per-map
                                  pistol skill the blended aggregate misses.

Coverage gate: per-feature, we set to 0.0 and `covered=0` when either team
lacks per-map data — the regression still sees the row.

PIT: pull the most recent cs2_hltv_team_pistols period_end strictly before
each match's kickoff_date. (Right now we only have one period stored
(2025-01-01 → today) so the PIT filter is a trivial pass; the code is written
to honour multiple periods once we backfill quarterly windows.)

Persists to cs2_model_backtest_history with feature_set='v18-pistols_*'.

Run:
    python3 scripts/esports/cs2_sneak_peek_v18_pistols.py [--since 2025-06-01]
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
from cs2_sneak_peek_v5 import load_matches_with_features, load_team_map, _logit  # type: ignore  # noqa: E402
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore  # noqa: E402
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore  # noqa: E402

# Team-name resolver from v16 — bo3gg "FaZe Clan" vs HLTV "FaZe", etc.
from cs2_sneak_peek_v16_mappool import (  # type: ignore  # noqa: E402
    load_hltv_team_pair_index,
    resolve_hltv_name,
    _norm_team,
)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


V8_KEYS = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
           "bo_centered", "pistol_diff",
           "tier_s", "tier_a", "tier_b", "tier_c", "tier_d", "kd_diff"]

V18_THISMAP_CT_KEY = ["team1_ct_pistol_diff_thismap"]
V18_THISMAP_T_KEY  = ["team1_t_pistol_diff_thismap"]
V18_THISMAP_BOTH   = V18_THISMAP_CT_KEY + V18_THISMAP_T_KEY
V18_AVG_KEY        = ["pistol_per_map_avg_diff"]
V18_ALL_KEYS       = V18_THISMAP_BOTH + V18_AVG_KEY


# ── Loaders ────────────────────────────────────────────────────────────
def load_team_pistol_per_map() -> dict:
    """{norm_team_name: [(period_end, period_start, map_name,
                          ct_pct, t_pct, maps_played), ...]} sorted by
    period_end ascending so bisect can find "most recent period strictly
    before kickoff".

    We key on a normalised team name (lowercase, strip-spaces-and-suffixes)
    so the bo3gg → HLTV name drift doesn't lose rows. cs2_hltv_team_pistols
    stores the HLTV display name (e.g. "Spirit"), bo3gg often passes
    "Team Spirit" — _norm_team collapses both to "spirit".
    """
    rows = execute_query("""
        SELECT team_name, map_name, period_start, period_end,
               ct_pistol_pct, t_pistol_pct, maps_played
        FROM cs2_hltv_team_pistols
        WHERE map_name IS NOT NULL
    """)
    streams: dict = defaultdict(list)
    for r in rows:
        key = _norm_team(r["team_name"])
        if not key:
            continue
        streams[key].append((
            r["period_end"], r["period_start"], r["map_name"],
            float(r["ct_pistol_pct"]) if r["ct_pistol_pct"] is not None else None,
            float(r["t_pistol_pct"])  if r["t_pistol_pct"]  is not None else None,
            int(r["maps_played"]) if r["maps_played"] is not None else 0,
        ))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


def load_first_map_by_match() -> dict[int, str]:
    """{hltv_match_id: first_map_display_name} from cs2_hltv_match_maps."""
    rows = execute_query("""
        SELECT hltv_match_id, map_name
        FROM cs2_hltv_match_maps
        WHERE map_order = 1 AND map_name IS NOT NULL
    """)
    return {int(r["hltv_match_id"]): r["map_name"] for r in rows}


# ── Per-team PIT helpers ───────────────────────────────────────────────
def _pit_select(stream: list, kickoff_date: date) -> list:
    """Return only stream rows whose period_end < kickoff_date.
    Stream is sorted ascending by period_end."""
    if not stream:
        return []
    end_keys = [row[0] for row in stream]
    cut = bisect.bisect_left(end_keys, kickoff_date)
    return stream[:cut]


def per_map_pistol_for_team(team: str, kickoff_date: date, streams: dict,
                            map_name: str) -> tuple[float | None, float | None, int]:
    """Most recent (ct_pct, t_pct, maps_played) for team on `map_name`
    among periods whose period_end < kickoff_date."""
    s = streams.get(_norm_team(team))
    if not s:
        return None, None, 0
    eligible = _pit_select(s, kickoff_date)
    # Walk back to find most recent matching map.
    for row in reversed(eligible):
        _pe, _ps, mp, ct, t, n = row
        if mp == map_name:
            return ct, t, n
    return None, None, 0


def avg_per_map_pistol(team: str, kickoff_date: date, streams: dict
                       ) -> tuple[float | None, int]:
    """Average (ct + t)/2 across all maps in the team's most-recent eligible
    period. Returns (mean_pistol_pct, n_maps)."""
    s = streams.get(_norm_team(team))
    if not s:
        return None, 0
    eligible = _pit_select(s, kickoff_date)
    if not eligible:
        return None, 0
    # Take the most recent period_end and grab all map rows with that period_end
    # (so we average across whichever maps were sampled for the team in the
    # latest eligible window).
    latest_pe = eligible[-1][0]
    same_period = [row for row in eligible if row[0] == latest_pe]
    vals: list[float] = []
    for _pe, _ps, _mp, ct, t, _n in same_period:
        if ct is not None and t is not None:
            vals.append((ct + t) / 2.0)
        elif ct is not None:
            vals.append(ct)
        elif t is not None:
            vals.append(t)
    if not vals:
        return None, 0
    return float(np.mean(vals)), len(vals)


# ── Eval harness ───────────────────────────────────────────────────────
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
               pistol_per_map_streams, first_map_by_match,
               hltv_pairs):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # ── v8 base features (identical to v15/v17 build_rows) ──
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

        # ── v18 per-map pistol features ──
        thismap_ct_diff = 0.0
        thismap_t_diff = 0.0
        thismap_covered = 0
        avg_diff = 0.0
        avg_covered = 0

        if kdate is not None:
            # Resolve HLTV team names via the bridge.
            hltv_id = m.get("hltv_match_id")
            hltv_pair = hltv_pairs.get(hltv_id) if hltv_id else None
            t1_hltv = resolve_hltv_name(m["team1"], hltv_pair) if hltv_pair else m["team1"]
            t2_hltv = resolve_hltv_name(m["team2"], hltv_pair) if hltv_pair else m["team2"]
            t1_hltv = t1_hltv or m["team1"]
            t2_hltv = t2_hltv or m["team2"]

            # thismap features require the upcoming match's main map
            main_map = first_map_by_match.get(hltv_id) if hltv_id else None
            if main_map:
                ct1, t1, _n1 = per_map_pistol_for_team(t1_hltv, kdate,
                                                      pistol_per_map_streams, main_map)
                ct2, t2, _n2 = per_map_pistol_for_team(t2_hltv, kdate,
                                                      pistol_per_map_streams, main_map)
                if ct1 is not None and ct2 is not None:
                    thismap_ct_diff = (ct1 - ct2) / 100.0
                if t1 is not None and t2 is not None:
                    thismap_t_diff = (t1 - t2) / 100.0
                if (ct1 is not None and ct2 is not None) or (t1 is not None and t2 is not None):
                    thismap_covered = 1

            # Per-map avg (independent of the bridge — only needs team names,
            # though we still use the HLTV-resolved name when we have it).
            a1, na1 = avg_per_map_pistol(t1_hltv, kdate, pistol_per_map_streams)
            a2, na2 = avg_per_map_pistol(t2_hltv, kdate, pistol_per_map_streams)
            if a1 is not None and a2 is not None and na1 >= 3 and na2 >= 3:
                avg_diff = (a1 - a2) / 100.0
                avg_covered = 1

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            # v8
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            # v18
            "team1_ct_pistol_diff_thismap": thismap_ct_diff,
            "team1_t_pistol_diff_thismap":  thismap_t_diff,
            "pistol_per_map_avg_diff":      avg_diff,
            "thismap_covered": thismap_covered,
            "avg_covered":     avg_covered,
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

    print("loading per-map pistol stats…")
    pistol_per_map_streams = load_team_pistol_per_map()
    print(f"  {len(pistol_per_map_streams)} teams with per-map pistol history")

    print("loading first_map_by_match…")
    first_map_by_match = load_first_map_by_match()
    print(f"  {len(first_map_by_match)} HLTV matches with map_order=1 row")

    print("loading HLTV team-pair index…")
    hltv_pairs = load_hltv_team_pair_index()
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

    rows = build_rows(
        matches,
        tm=tm, pistol=pistol, tier_map=tier_map, kd_map=kd_map, direct=direct,
        pistol_per_map_streams=pistol_per_map_streams,
        first_map_by_match=first_map_by_match,
        hltv_pairs=hltv_pairs,
    )
    print(f"  {len(rows)} matches with saved_prob\n")

    n = max(len(rows), 1)
    thismap_cov = sum(1 for r in rows if r["thismap_covered"])
    avg_cov     = sum(1 for r in rows if r["avg_covered"])
    any_cov     = sum(1 for r in rows if r["thismap_covered"] or r["avg_covered"])
    print(f"  coverage:")
    print(f"    team1_*_pistol_diff_thismap (CT+T):  {thismap_cov}/{n} ({thismap_cov/n:.1%})")
    print(f"    pistol_per_map_avg_diff:             {avg_cov}/{n} ({avg_cov/n:.1%})")
    print(f"    any (thismap OR avg):                {any_cov}/{n} ({any_cov/n:.1%})\n")

    # ── diagnostic: are per-map pistol stats orthogonal to the aggregate? ──
    # Pearson r between pistol_diff (v8 aggregate) and pistol_per_map_avg_diff
    # when both are covered.
    paired = [(r["pistol_diff"], r["pistol_per_map_avg_diff"])
              for r in rows if r["avg_covered"] and r["pistol_diff"] != 0.0]
    if len(paired) >= 30:
        a = np.array([p[0] for p in paired])
        b = np.array([p[1] for p in paired])
        denom = (a.std() * b.std())
        rho = float(((a - a.mean()) * (b - b.mean())).mean() / denom) if denom > 0 else 0.0
        print(f"  [diag] corr(v8 pistol_diff, v18 pistol_per_map_avg_diff) "
              f"on {len(paired)} matched rows: {rho:+.3f}")
        if abs(rho) < 0.4:
            print(f"         → per-map signal is MOSTLY orthogonal to the v8 aggregate")
        elif abs(rho) < 0.7:
            print(f"         → per-map signal is PARTIALLY redundant with the v8 aggregate")
        else:
            print(f"         → per-map signal is LARGELY redundant with the v8 aggregate")
        print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v18-pistols (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 83)
    print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} "
          f"{m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v18-pistols_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    blocks = [
        (V8_KEYS, "v8 reference"),
        (V8_KEYS + V18_THISMAP_BOTH, "v18: v8 + thismap (CT+T)"),
        (V8_KEYS + V18_AVG_KEY,      "v18: v8 + per_map_avg"),
        (V8_KEYS + V18_ALL_KEYS,     "v18-pistols ALL (v8 + 3 features)"),
    ]
    for keys, lbl in blocks:
        r = evaluate(rows, keys, lbl)
        if r.get("skipped"):
            print(f"{lbl:50}  (skipped, n={r['n']})")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{lbl:50} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} "
              f"{mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(f"v18-pistols_{lbl}", r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
        auc_track[lbl] = mm["auc"]

    # ── PROMOTE — per-block recommendation vs v8 reference ──
    base_auc = auc_track.get("v8 reference")
    print("\n" + "=" * 83)
    print("PROMOTE DECISION (per feature block, vs v8 reference)")
    print("=" * 83)
    if base_auc is None:
        print("PROMOTE: cannot evaluate — v8 reference run was skipped")
        return

    print(f"v8 reference AUC: {base_auc:.4f}")
    for lbl, cov, key_name in [
        ("v18: v8 + thismap (CT+T)",        thismap_cov, "team1_*_pistol_diff_thismap"),
        ("v18: v8 + per_map_avg",           avg_cov,     "pistol_per_map_avg_diff"),
        ("v18-pistols ALL (v8 + 3 features)", min(thismap_cov, avg_cov), "ALL"),
    ]:
        block_auc = auc_track.get(lbl)
        if block_auc is None:
            print(f"  {key_name:32}  SKIPPED")
            continue
        delta = block_auc - base_auc
        cov_pct = cov / n
        verdict = "PROMOTE" if delta >= 0.002 else "no"
        print(f"  {key_name:32}  AUC={block_auc:.4f} (Δ {delta:+.4f})  "
              f"cov={cov_pct:.1%}  → {verdict}")


if __name__ == "__main__":
    main()
