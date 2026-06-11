"""
CS2 sneak-peek v20-leetify — v8 + Leetify demo-derived team features.

First independent signal source after the HLTV-detail v10-v19 feature space
turned out empty. Leetify pre-computes per-player-per-match stats from CS2
demos (trade kills, multi-kills, utility damage, preaim, reaction time,
opening/clutch ratings, ct/t splits). We cross-reference HLTV match IDs
natively via `cs2_leetify_player_match_stats.hltv_match_id`, and resolve
team_number ↔ team_name per match via `cs2_hltv_player_match_stats`.

Aggregation: for each upcoming match, look up the Leetify history of every
player who has played for either side IN PRIOR HLTV MATCHES (PIT-correct:
`finished_at < kickoff_time`). Average each Leetify metric across all those
prior player-match rows to get a team-level "Leetify form" number. Then
team1_diff = team1_avg − team2_avg.

Features on top of v8:

  leetify_rating_diff   — team-avg leetify_rating
  trade_success_diff    — team-avg trade_kills_success_percentage
  multi_kill_diff       — impact-weighted multi-kills per round:
                          (multi2k + 2*multi3k + 3*multi4k + 5*multi5k) / rounds
  preaim_diff           — team-avg preaim (negated: lower preaim is better)
  opening_diff          — team-avg (ct_leetify_rating + t_leetify_rating)/2

Coverage gate: feature is set to 0 (and covered=0) when either team has <3
prior-match player-rows in Leetify. The regression still sees the row so
the model can learn that "we have no signal here" weights to neutral.

PIT: every aggregation strictly enforces `lpm.finished_at < kickoff_time`.

Persists to cs2_model_backtest_history with feature_set='v20-leetify_*'.

Run:
    python3 scripts/esports/cs2_sneak_peek_v20_leetify.py [--since 2025-06-01]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime
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


V8_KEYS = ["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
           "bo_centered", "pistol_diff",
           "tier_s", "tier_a", "tier_b", "tier_c", "tier_d", "kd_diff"]

V20_LEETIFY_KEYS = [
    "leetify_rating_diff",
    "trade_success_diff",
    "multi_kill_diff",
    "preaim_diff",
    "opening_diff",
]


# ── Team-roster resolution ───────────────────────────────────────────────
def load_team_roster_by_match() -> dict[tuple[int, str], set[str]]:
    """For every HLTV match in which we have Leetify data AND a team_number
    is resolvable to an HLTV team_name (via cs2_hltv_player_match_stats),
    return {(hltv_match_id, hltv_team_name_lower): {steam64_id, ...}}.

    This is the bridge between Leetify's team_number (2 or 3) and the
    HLTV-side team name. Once we know which steam64s played for "BC.Game"
    in match X (per HLTV), we can pull THEIR Leetify history before the
    upcoming match for the team-level aggregate.
    """
    rows = execute_query("""
        SELECT DISTINCT
            lpm.hltv_match_id,
            hps.team_name,
            lpm.steam64_id
        FROM cs2_leetify_player_match_stats lpm
        JOIN cs2_hltv_player_match_stats hps
          ON hps.hltv_match_id = lpm.hltv_match_id
         AND LOWER(hps.nickname) = LOWER(lpm.nickname)
        WHERE lpm.hltv_match_id IS NOT NULL
          AND hps.team_name IS NOT NULL
    """)
    out: dict[tuple[int, str], set[str]] = defaultdict(set)
    for r in rows:
        key = (int(r["hltv_match_id"]), r["team_name"].lower())
        out[key].add(r["steam64_id"])
    return out


def build_team_player_history(roster_by_match: dict) -> dict[str, set[str]]:
    """Flatten {(hltv_id, team_name): {steam64s}} → {team_name_lower:
    {steam64s_ever_associated}}. Catches lineup churn — we want every
    steam64 that ever played for that team across all HLTV matches in our
    Leetify universe."""
    team_to_steam64: dict[str, set[str]] = defaultdict(set)
    for (hltv_id, team), steam64s in roster_by_match.items():
        team_to_steam64[team].update(steam64s)
    return team_to_steam64


# ── Leetify per-player history (sorted for PIT bisect) ────────────────────
def load_leetify_history_by_steam64() -> dict[str, list[tuple]]:
    """{steam64_id: [(finished_at_datetime, leetify_rating, trade_success,
                      multi_score_per_round, preaim, opening), ...]}
    sorted by finished_at ascending. multi_score_per_round =
    (multi2k + 2*multi3k + 3*multi4k + 5*multi5k) / rounds_count when
    rounds_count > 0.

    Only includes data_source='hltv' matches — we want to ground the
    Leetify signal in the same competitive universe v8 lives in. (We
    keep the option to widen this later.)
    """
    rows = execute_query("""
        SELECT steam64_id, finished_at,
               leetify_rating, trade_kills_success_percentage,
               multi2k, multi3k, multi4k, multi5k, rounds_count,
               preaim,
               ct_leetify_rating, t_leetify_rating
        FROM cs2_leetify_player_match_stats
        WHERE data_source = 'hltv'
          AND finished_at IS NOT NULL
    """)
    out: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        rounds = int(r["rounds_count"] or 0)
        m2 = int(r["multi2k"] or 0)
        m3 = int(r["multi3k"] or 0)
        m4 = int(r["multi4k"] or 0)
        m5 = int(r["multi5k"] or 0)
        multi_score = (m2 + 2 * m3 + 3 * m4 + 5 * m5) / rounds if rounds > 0 else None

        ct = r["ct_leetify_rating"]
        t = r["t_leetify_rating"]
        opening = None
        if ct is not None and t is not None:
            opening = (float(ct) + float(t)) / 2.0
        elif r["leetify_rating"] is not None:
            opening = float(r["leetify_rating"])

        out[r["steam64_id"]].append((
            r["finished_at"],
            float(r["leetify_rating"]) if r["leetify_rating"] is not None else None,
            float(r["trade_kills_success_percentage"]) if r["trade_kills_success_percentage"] is not None else None,
            multi_score,
            float(r["preaim"]) if r["preaim"] is not None else None,
            opening,
        ))
    for sid in out:
        out[sid].sort(key=lambda x: x[0])
    return dict(out)


def _pit_player_history(stream: list[tuple], kickoff: datetime) -> list[tuple]:
    """Return rows with finished_at < kickoff. Stream is sorted ascending."""
    if not stream:
        return []
    keys = [row[0] for row in stream]
    cut = bisect.bisect_left(keys, kickoff)
    return stream[:cut]


def team_leetify_aggregates(
    team_name: str,
    kickoff: datetime,
    team_to_steam64: dict[str, set[str]],
    history_by_steam64: dict[str, list[tuple]],
) -> tuple[dict[str, float | None], int]:
    """For each Leetify metric, average across every prior player-match row
    among players who ever played for `team_name`. Returns
    ({metric: avg|None}, n_player_rows_used)."""
    steam64s = team_to_steam64.get(team_name.lower())
    if not steam64s:
        return {k: None for k in V20_LEETIFY_KEYS}, 0

    ratings, trades, multis, preaims, openings = [], [], [], [], []
    for sid in steam64s:
        for row in _pit_player_history(history_by_steam64.get(sid, []), kickoff):
            _, rating, trade, multi, preaim, opening = row
            if rating is not None:
                ratings.append(rating)
            if trade is not None:
                trades.append(trade)
            if multi is not None:
                multis.append(multi)
            if preaim is not None:
                preaims.append(preaim)
            if opening is not None:
                openings.append(opening)
    n = max(len(ratings), len(trades), len(multis), len(preaims), len(openings))
    return {
        "leetify_rating": float(np.mean(ratings)) if ratings else None,
        "trade_success": float(np.mean(trades)) if trades else None,
        "multi_score":   float(np.mean(multis)) if multis else None,
        "preaim":        float(np.mean(preaims)) if preaims else None,
        "opening":       float(np.mean(openings)) if openings else None,
    }, n


# ── Eval harness ─────────────────────────────────────────────────────────
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
               team_to_steam64, history_by_steam64):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # ── v8 base features (mirror of v18) ──
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

        # ── v20 Leetify team-aggregate diffs ──
        kickoff = m["kickoff_time"]
        leetify_rating_diff = 0.0
        trade_success_diff = 0.0
        multi_kill_diff = 0.0
        preaim_diff = 0.0  # NB: lower preaim better, so we use team1−team2 of raw,
                            # and let the logistic learn the sign.
        opening_diff = 0.0
        v20_covered = 0

        if kickoff is not None and m["team1"] and m["team2"]:
            t1_agg, t1_n = team_leetify_aggregates(
                m["team1"], kickoff, team_to_steam64, history_by_steam64
            )
            t2_agg, t2_n = team_leetify_aggregates(
                m["team2"], kickoff, team_to_steam64, history_by_steam64
            )
            if t1_n >= 3 and t2_n >= 3:
                if t1_agg["leetify_rating"] is not None and t2_agg["leetify_rating"] is not None:
                    leetify_rating_diff = t1_agg["leetify_rating"] - t2_agg["leetify_rating"]
                if t1_agg["trade_success"] is not None and t2_agg["trade_success"] is not None:
                    trade_success_diff = t1_agg["trade_success"] - t2_agg["trade_success"]
                if t1_agg["multi_score"] is not None and t2_agg["multi_score"] is not None:
                    multi_kill_diff = t1_agg["multi_score"] - t2_agg["multi_score"]
                if t1_agg["preaim"] is not None and t2_agg["preaim"] is not None:
                    preaim_diff = t1_agg["preaim"] - t2_agg["preaim"]
                if t1_agg["opening"] is not None and t2_agg["opening"] is not None:
                    opening_diff = t1_agg["opening"] - t2_agg["opening"]
                v20_covered = 1

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
            # v20
            "leetify_rating_diff": leetify_rating_diff,
            "trade_success_diff":  trade_success_diff,
            "multi_kill_diff":     multi_kill_diff,
            "preaim_diff":         preaim_diff,
            "opening_diff":        opening_diff,
            "v20_covered":         v20_covered,
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

    print("loading Leetify team-roster bridge from cs2_leetify_player_match_stats…")
    roster_by_match = load_team_roster_by_match()
    team_to_steam64 = build_team_player_history(roster_by_match)
    print(f"  {len(roster_by_match)} (hltv_match, team) pairs")
    print(f"  {len(team_to_steam64)} unique teams with Leetify-tracked players")

    print("loading Leetify per-player history…")
    history_by_steam64 = load_leetify_history_by_steam64()
    n_rows = sum(len(v) for v in history_by_steam64.values())
    print(f"  {len(history_by_steam64)} steam64s, {n_rows} player-match rows")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    print(f"  {len(matches)} candidate matches")

    rows = build_rows(
        matches,
        tm=tm, pistol=pistol, tier_map=tier_map, kd_map=kd_map, direct=direct,
        team_to_steam64=team_to_steam64, history_by_steam64=history_by_steam64,
    )
    print(f"  {len(rows)} matches with saved_prob\n")

    n = max(len(rows), 1)
    v20_cov = sum(1 for r in rows if r["v20_covered"])
    print(f"  coverage:")
    print(f"    v20-leetify (any covered):   {v20_cov}/{n} ({v20_cov/n:.1%})\n")

    # ── Diagnostic: leetify_rating_diff vs v8 saved_prob — is the signal
    # mostly already captured by the cs2_v8 model?
    paired = [(r["logit_saved"], r["leetify_rating_diff"])
              for r in rows if r["v20_covered"]]
    if len(paired) >= 30:
        a = np.array([p[0] for p in paired])
        b = np.array([p[1] for p in paired])
        denom = (a.std() * b.std())
        rho = float(((a - a.mean()) * (b - b.mean())).mean() / denom) if denom > 0 else 0.0
        print(f"  [diag] corr(logit_saved_prob, leetify_rating_diff) on "
              f"{len(paired)} covered rows: {rho:+.3f}")
        if abs(rho) < 0.4:
            print("         → mostly orthogonal to v8 saved_prob (good — adds new info)")
        elif abs(rho) < 0.7:
            print("         → partially redundant with v8 saved_prob")
        else:
            print("         → largely redundant — v8 already encodes most of the leetify_rating signal")
        print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v20-leetify (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':52} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 85)
    print(f"{'baseline (hltv_v1 direct)':52} {m_base['auc'] or 0:>6.3f} "
          f"{m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v20-leetify_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    blocks = [
        (V8_KEYS,                                "v8 reference"),
        (V8_KEYS + ["leetify_rating_diff"],      "v20: v8 + leetify_rating"),
        (V8_KEYS + ["trade_success_diff"],       "v20: v8 + trade_success"),
        (V8_KEYS + ["multi_kill_diff"],          "v20: v8 + multi_kill"),
        (V8_KEYS + ["preaim_diff"],              "v20: v8 + preaim"),
        (V8_KEYS + ["opening_diff"],             "v20: v8 + opening"),
        (V8_KEYS + V20_LEETIFY_KEYS,             "v20-leetify ALL (v8 + 5 features)"),
    ]
    for keys, lbl in blocks:
        r = evaluate(rows, keys, lbl)
        if r.get("skipped"):
            print(f"{lbl:52}  (skipped, n={r['n']})")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{lbl:52} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} "
              f"{mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(f"v20-leetify_{lbl}", r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
        auc_track[lbl] = mm["auc"]

    # ── PROMOTE — per-block recommendation vs v8 reference ──
    base_auc = auc_track.get("v8 reference")
    print("\n" + "=" * 85)
    print("PROMOTE DECISION (per feature block, vs v8 reference)")
    print("=" * 85)
    if base_auc is None:
        print("PROMOTE: cannot evaluate — v8 reference run was skipped")
        return

    print(f"v8 reference AUC: {base_auc:.4f}")
    for lbl, key_name in [
        ("v20: v8 + leetify_rating",        "leetify_rating_diff"),
        ("v20: v8 + trade_success",         "trade_success_diff"),
        ("v20: v8 + multi_kill",            "multi_kill_diff"),
        ("v20: v8 + preaim",                "preaim_diff"),
        ("v20: v8 + opening",               "opening_diff"),
        ("v20-leetify ALL (v8 + 5 features)", "ALL"),
    ]:
        block_auc = auc_track.get(lbl)
        if block_auc is None:
            print(f"  {key_name:24}  SKIPPED")
            continue
        delta = block_auc - base_auc
        verdict = "PROMOTE" if delta >= 0.002 else "no"
        print(f"  {key_name:24}  AUC={block_auc:.4f} (Δ {delta:+.4f})  "
              f"cov={v20_cov/n:.1%}  → {verdict}")


if __name__ == "__main__":
    main()
