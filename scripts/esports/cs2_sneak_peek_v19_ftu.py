"""
CS2 sneak-peek v19-ftu — v8 + HLTV /stats/teams/ftu utility/teamwork features.

Background: HLTV's /stats/teams/ftu page is HLTV's only public per-team
utility/teamwork breakdown. The visual header groups columns under
"Firepower / Teamwork / Utility" composites and the underlying data is
ten plain columns:
    Team | Maps | RW% | OpK | MultiK | 5v4% | 4v5% | Traded% | ADR | FA
Crucially the ADR column on this specific page is the *utility damage per
round* component (typical range 20–30, not the 70-90 total ADR seen on
the players page) and FA is *flash assists per round* — so this is the
closest signal HLTV publishes to "team utility usage". Per-team
flashes-thrown / molotovs-thrown counts are not exposed.

Migration 240 stores three side slices (all/ct/t) per period and four
quarterly periods so v19 can pick the most recent PIT-eligible sample
per match.

Three new features (PIT-correct, period_end < kickoff_date):

  util_dmg_diff       — utility ADR diff, team1 − team2 (the "ADR"
                        column on /stats/teams/ftu, scaled /100).
  flash_efficiency_diff
                      — FA per round, team1 − team2 (higher FA = better
                        flash use). Direct surrogate for the per-flash-thrown
                        efficiency we'd want if HLTV published flash throw
                        counts.
  nade_economy_diff   — composite of FA + util ADR (combined utility
                        intensity), team1 − team2 (broader = more
                        deliberate setups). Stored as a single feature
                        so the regression can size it independently.

(Bonus: traded_pct is also informative — included as `traded_diff` to test
whether team-coordination signal stacks alongside utility.)

Coverage gate: per-feature, set to 0.0 and `covered=0` when either team
lacks per-team data — the regression still sees the row.

PIT: pull the most recent cs2_hltv_team_ftu period_end strictly before
each match's kickoff_date.

Persists to cs2_model_backtest_history with feature_set='v19-ftu_*'.

Run:
    python3 scripts/esports/cs2_sneak_peek_v19_ftu.py [--since 2025-06-01]
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

V19_UTIL_KEY    = ["util_dmg_diff"]
V19_FLASH_KEY   = ["flash_efficiency_diff"]
V19_ECON_KEY    = ["nade_economy_diff"]
V19_TRADED_KEY  = ["traded_diff"]
V19_ALL_KEYS    = V19_UTIL_KEY + V19_FLASH_KEY + V19_ECON_KEY + V19_TRADED_KEY


# ── Loaders ────────────────────────────────────────────────────────────
def load_team_ftu_streams() -> dict:
    """{norm_team_name: [(period_end, period_start, side, adr, fa,
                          traded_pct, maps_played), ...]} sorted by
    period_end ascending so bisect can find "most recent period strictly
    before kickoff".

    Loads only the 'all' side slice — v19 uses side-agnostic features for
    simplicity. The ct/t slices remain in the table for future per-side
    work. cs2_hltv_team_ftu stores HLTV display names (e.g. "Spirit"),
    bo3gg often passes "Team Spirit" — _norm_team collapses both to
    "spirit".
    """
    rows = execute_query("""
        SELECT team_name, side, period_start, period_end,
               adr, fa, traded_pct, maps_played
        FROM cs2_hltv_team_ftu
        WHERE side = 'all'
    """)
    streams: dict = defaultdict(list)
    for r in rows:
        key = _norm_team(r["team_name"])
        if not key:
            continue
        streams[key].append((
            r["period_end"], r["period_start"], r["side"],
            float(r["adr"]) if r["adr"] is not None else None,
            float(r["fa"])  if r["fa"]  is not None else None,
            float(r["traded_pct"]) if r["traded_pct"] is not None else None,
            int(r["maps_played"]) if r["maps_played"] is not None else 0,
        ))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


# ── Per-team PIT helpers ───────────────────────────────────────────────
def _pit_select(stream: list, kickoff_date: date) -> list:
    """Return only stream rows whose period_end < kickoff_date.
    Stream is sorted ascending by period_end."""
    if not stream:
        return []
    end_keys = [row[0] for row in stream]
    cut = bisect.bisect_left(end_keys, kickoff_date)
    return stream[:cut]


def ftu_for_team(team: str, kickoff_date: date, streams: dict
                 ) -> tuple[float | None, float | None, float | None, int]:
    """Most recent (adr, fa, traded_pct, maps_played) for team among
    periods whose period_end < kickoff_date."""
    s = streams.get(_norm_team(team))
    if not s:
        return None, None, None, 0
    eligible = _pit_select(s, kickoff_date)
    if not eligible:
        return None, None, None, 0
    _pe, _ps, _side, adr, fa, traded, n = eligible[-1]
    return adr, fa, traded, n


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
               ftu_streams, hltv_pairs):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # ── v8 base features (identical to v18 build_rows) ──
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

        # ── v19 FTU features ──
        util_dmg_diff = 0.0
        flash_eff_diff = 0.0
        nade_econ_diff = 0.0
        traded_diff = 0.0
        util_covered  = 0
        flash_covered = 0
        econ_covered  = 0
        traded_covered = 0

        if kdate is not None:
            # Resolve HLTV team names via the bridge.
            hltv_id = m.get("hltv_match_id")
            hltv_pair = hltv_pairs.get(hltv_id) if hltv_id else None
            t1_hltv = resolve_hltv_name(m["team1"], hltv_pair) if hltv_pair else m["team1"]
            t2_hltv = resolve_hltv_name(m["team2"], hltv_pair) if hltv_pair else m["team2"]
            t1_hltv = t1_hltv or m["team1"]
            t2_hltv = t2_hltv or m["team2"]

            adr1, fa1, tr1, _n1 = ftu_for_team(t1_hltv, kdate, ftu_streams)
            adr2, fa2, tr2, _n2 = ftu_for_team(t2_hltv, kdate, ftu_streams)

            if adr1 is not None and adr2 is not None:
                # util_dmg_diff in "ADR per round" — typical 20-30 → scale /10
                # so coefficients stay in a comparable range with other features.
                util_dmg_diff = (adr1 - adr2) / 10.0
                util_covered = 1

            if fa1 is not None and fa2 is not None:
                # FA per round is 0.1-0.4 — already in a small range, leave as-is.
                flash_eff_diff = fa1 - fa2
                flash_covered = 1

            if (adr1 is not None and adr2 is not None
                    and fa1 is not None and fa2 is not None):
                # Composite "utility intensity" — z-scoreless combine.
                # ADR scaled /10, FA scaled ×10 so both roughly comparable.
                t1_econ = (adr1 / 10.0) + (fa1 * 10.0)
                t2_econ = (adr2 / 10.0) + (fa2 * 10.0)
                nade_econ_diff = t1_econ - t2_econ
                econ_covered = 1

            if tr1 is not None and tr2 is not None:
                # traded_pct is in percent — scale /100
                traded_diff = (tr1 - tr2) / 100.0
                traded_covered = 1

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
            # v19
            "util_dmg_diff":         util_dmg_diff,
            "flash_efficiency_diff": flash_eff_diff,
            "nade_economy_diff":     nade_econ_diff,
            "traded_diff":           traded_diff,
            "util_covered":   util_covered,
            "flash_covered":  flash_covered,
            "econ_covered":   econ_covered,
            "traded_covered": traded_covered,
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

    print("loading FTU streams…")
    ftu_streams = load_team_ftu_streams()
    print(f"  {len(ftu_streams)} teams with FTU history")

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
        ftu_streams=ftu_streams, hltv_pairs=hltv_pairs,
    )
    print(f"  {len(rows)} matches with saved_prob\n")

    n = max(len(rows), 1)
    util_cov   = sum(1 for r in rows if r["util_covered"])
    flash_cov  = sum(1 for r in rows if r["flash_covered"])
    econ_cov   = sum(1 for r in rows if r["econ_covered"])
    traded_cov = sum(1 for r in rows if r["traded_covered"])
    any_cov    = sum(1 for r in rows if r["util_covered"] or r["flash_covered"])
    print(f"  coverage:")
    print(f"    util_dmg_diff:         {util_cov}/{n} ({util_cov/n:.1%})")
    print(f"    flash_efficiency_diff: {flash_cov}/{n} ({flash_cov/n:.1%})")
    print(f"    nade_economy_diff:     {econ_cov}/{n} ({econ_cov/n:.1%})")
    print(f"    traded_diff:           {traded_cov}/{n} ({traded_cov/n:.1%})")
    print(f"    any FTU:               {any_cov}/{n} ({any_cov/n:.1%})\n")

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"--- v19-ftu (n={len(rows)}, test={len(rows)-cut}) ---")
    print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 83)
    print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} "
          f"{m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v19-ftu_baseline", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    auc_track: dict[str, float] = {}
    blocks = [
        (V8_KEYS, "v8 reference"),
        (V8_KEYS + V19_UTIL_KEY,    "v19: v8 + util_dmg_diff"),
        (V8_KEYS + V19_FLASH_KEY,   "v19: v8 + flash_efficiency_diff"),
        (V8_KEYS + V19_ECON_KEY,    "v19: v8 + nade_economy_diff"),
        (V8_KEYS + V19_TRADED_KEY,  "v19: v8 + traded_diff"),
        (V8_KEYS + V19_ALL_KEYS,    "v19-ftu ALL (v8 + 4 features)"),
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
        persist(f"v19-ftu_{lbl}", r["n"], mm, since_d,
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
        ("v19: v8 + util_dmg_diff",         util_cov,   "util_dmg_diff"),
        ("v19: v8 + flash_efficiency_diff", flash_cov,  "flash_efficiency_diff"),
        ("v19: v8 + nade_economy_diff",     econ_cov,   "nade_economy_diff"),
        ("v19: v8 + traded_diff",           traded_cov, "traded_diff"),
        ("v19-ftu ALL (v8 + 4 features)",   min(util_cov, flash_cov), "ALL"),
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
