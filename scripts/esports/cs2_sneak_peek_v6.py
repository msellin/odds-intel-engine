"""
CS2 sneak-peek v6 — adds team_kd_diff from player K/D × current rosters.

The "Virtus.pro vs Oxuji" incident exposed the HLTV-rank fallback model's
biggest blind spot: it doesn't see individual player skill. VP has
electroNic (tier-1, K/D ~1.15+) on the roster; Oxuji's players are all
unproven (K/D ~0.95). Pure rank diff says "close"; player skill says VP
strongly favoured. The market agrees with player skill, not rank.

v6 adds team_kd_diff stacked on v5's best (v4-ALL + bo). The team K/D is
derived by:
  1. Look up current PandaScore roster (data/esports/cs2/pandascore_rosters.json)
  2. For each of the 5 players, fetch K/D from cs2_hltv_player_stats by nickname
  3. Average across the available players (≥3 required, else NULL)

Persists to cs2_model_backtest_history for the admin UI trend chart.

Run:
    python3 scripts/esports/cs2_sneak_peek_v6.py [--since 2025-06-01]
"""

import argparse
import json
import os
import sys
import uuid
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

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


def load_team_kd_map() -> dict[str, float]:
    """{team_name: avg_kd_of_current_roster}. Requires ≥3 players resolved."""
    # 1) load rosters from PandaScore cache
    ps_path = Path(__file__).resolve().parents[2] / "data/esports/cs2/pandascore_rosters.json"
    rosters: dict[str, list[str]] = {}
    if ps_path.exists():
        ps = json.loads(ps_path.read_text())
        for team_name, payload in ps.items():
            if isinstance(payload, dict):
                players = payload.get("players") or []
                names = [p.get("nickname") for p in players if p.get("nickname")]
                rosters[team_name] = names
            elif isinstance(payload, list):
                rosters[team_name] = [p.get("nickname") for p in payload if isinstance(p, dict) and p.get("nickname")]
    print(f"  rosters loaded: {len(rosters)}")

    # 2) Pull K/D per nickname from cs2_hltv_player_stats (some rows store "-"
    # for no data; CAST WHERE clause filters them via regex on the string).
    rows = execute_query(
        """SELECT nickname, stats->>'k_d_ratio' AS kd_str
            FROM cs2_hltv_player_stats
            WHERE stats ? 'k_d_ratio'
              AND stats->>'k_d_ratio' ~ '^[0-9]+\\.?[0-9]*$'"""
    )
    kd_by_nick: dict[str, float] = {}
    for r in rows:
        try:
            kd_by_nick[r["nickname"].lower()] = float(r["kd_str"])
        except (TypeError, ValueError):
            continue
    print(f"  player K/D map: {len(kd_by_nick)} nicks")

    # 3) Average K/D per team (need ≥3 resolved players)
    out: dict[str, float] = {}
    for team_name, nicks in rosters.items():
        kds = [kd_by_nick[n.lower()] for n in nicks if n and n.lower() in kd_by_nick]
        if len(kds) >= 3:
            out[team_name] = sum(kds) / len(kds)
    print(f"  teams with ≥3 K/D resolved: {len(out)}")
    return out


def build_rows(matches, tm, kd_map):
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # Re-derive v5 base features
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

        # NEW v6: team K/D diff
        t1_kd = kd_map.get(m["team1"])
        t2_kd = kd_map.get(m["team2"])
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff,
            "h2h_diff": h2h_diff,
            "rest_diff": rest_diff,
            "rank_diff": rank_diff,
            "tm_diff": tm_diff,
            "bo_centered": bo_centered,
            "kd_diff": kd_diff,
        })
    return out


def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
    }


def evaluate_stack(rows, extra_keys, name):
    cut = int(len(rows) * 0.7)
    if cut < 50:
        return {"skipped": True, "n": len(rows)}
    keys = ["logit_saved"] + extra_keys
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=2000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(keys, m.coef_[0].tolist())),
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

    print("loading team_map…")
    tm = load_team_map()
    print(f"  {len(tm)} teams")

    print("loading team K/D map…")
    kd_map = load_team_kd_map()

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm, kd_map)
    print(f"  {len(rows)} matches with saved_prob")
    print()

    # KD coverage diagnostic
    kd_covered = sum(1 for r in rows if r["kd_diff"] != 0.0)
    print(f"  kd_diff coverage: {kd_covered}/{len(rows)} ({kd_covered / len(rows):.1%}) — both teams in kd_map")
    print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)
    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"{'set':38} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 72)
    print(f"{'baseline (hltv_v1 direct)':38} {len(rows):>5} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v6_baseline_hltv_v1", len(rows), m_base, since_d,
            keys=["win_prob1"], n_train=cut)

    # v5 best for reference
    for keys, label in [
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered"],
         "v5 v4-ALL + bo (prior best)"),
        (["kd_diff"], "v6 + kd alone"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered", "kd_diff"],
         "v6 v5-best + kd"),
    ]:
        r = evaluate_stack(rows, keys, label)
        if r.get("skipped"):
            print(f"{label:38} {r['n']:>5}  (skipped)")
            continue
        mm = r["metrics"]
        delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{label:38} {r['n']:>5} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(label, r["n"], mm, since_d,
                keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))

    # Same on KD-covered subset only — cleaner test of whether kd_diff helps
    # where it actually has data
    covered = [r for r in rows if r["kd_diff"] != 0.0]
    print()
    print(f"--- KD-covered subset only (n={len(covered)}) ---")
    if len(covered) >= 100:
        cut_c = int(len(covered) * 0.7)
        y_te_c = np.array([r["y"] for r in covered[cut_c:]], dtype=int)
        p_base_c = np.array([r["saved"] for r in covered[cut_c:]], dtype=float)
        m_base_c = _metrics(y_te_c, p_base_c)
        print(f"{'baseline (hltv_v1 direct)':38} {len(covered):>5} {m_base_c['auc'] or 0:>6.3f}")
        persist("v6_kd_covered_baseline", len(covered), m_base_c, since_d,
                keys=["win_prob1"], n_train=cut_c)
        for keys, label in [
            (["kd_diff"], "v6 covered + kd"),
            (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered", "kd_diff"],
             "v6 covered v5-best + kd"),
        ]:
            r = evaluate_stack(covered, keys, label)
            if r.get("skipped"):
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base_c["auc"]) if (mm["auc"] and m_base_c["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{label:38} {r['n']:>5} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(label, r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
    else:
        print("  (not enough rows)")


if __name__ == "__main__":
    main()
