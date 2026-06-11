"""
CS2 sneak-peek v5 — adds bo_format, tournament_tier_derived, opp_strength_adjusted_form.

Builds on v4's stacking design (saved hltv_v1 prob as base + extra features
added on top via logistic). v5 adds three more derivable signals:

  bo_format            — Bo1 / Bo3 / Bo5. Bo1s have higher variance, Bo3+
                         favor sharper models. Encoded as best_of - 3 so
                         Bo3 = 0 (baseline).
  tournament_tier      — Regex-classified from league name. S = Major/IEM/EPL,
                         A = ESL Pro Tier 1, B = open qualifiers, C = ESEA-Advanced
                         and similar grassroots. Encoded as -2..+2.
  opp_strength_adj_form — form_diff * (avg_opponent_rank_diff / 100). Catches
                         "team is on a 10-win streak but only beat tier-4 teams"
                         situations that pure form misses.

Persists to cs2_model_backtest_history same as v4.

Run:
    python3 scripts/esports/cs2_sneak_peek_v5.py [--since 2025-06-01]
"""

import argparse
import json
import os
import re
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

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


# ── Tournament tier classifier ──────────────────────────────────────
# Lifetime esports orgs ranked by prize money + sharp money attention.
# Higher tier = more predictable, less upset.
_TIER_PATTERNS = [
    # S — major/premier events
    (re.compile(r"\b(major|iem katowice|iem cologne|iem dallas|blast world final|"
                r"esl pro league season|paris major|austin major|stockholm major)\b", re.I), 2),
    # A — tier-1 series
    (re.compile(r"\b(blast premier|esl pro league|iem|esl one|epl)\b", re.I), 1),
    # B — tier-2 (regional pro)
    (re.compile(r"\b(esea premier|esea mdl|wesg|cct|cs asia championship|"
                r"intel extreme masters|elisa invitational|funspark|gamers club)\b", re.I), 0),
    # C — open quals + grassroots
    (re.compile(r"\b(esea advanced|esea main|open qualifier|university|"
                r"clutch series|nodwin|circuito|liga)\b", re.I), -1),
]


def classify_tier(league_name: str) -> int:
    """Return tier in -1..+2. Default 0 if no pattern matches."""
    if not league_name:
        return 0
    for pat, tier in _TIER_PATTERNS:
        if pat.search(league_name):
            return tier
    return 0


# ── Data load (reuses v4 query) ─────────────────────────────────────
def load_team_map() -> dict:
    rows = execute_query("""
        SELECT team_name, AVG(win_pct) AS avg_wp
        FROM cs2_hltv_team_map_stats WHERE win_pct IS NOT NULL GROUP BY team_name
    """)
    return {r["team_name"]: float(r["avg_wp"]) for r in rows}


def load_matches_with_features(since: str) -> list[dict]:
    """Same as v4 plus best_of + league name pulled through."""
    return execute_query(
        """
        SELECT
            res.bo3gg_id,
            res.kickoff_time, res.team1, res.team2, res.winner,
            res.best_of,
            p.win_prob1, p.hltv_rank1, p.hltv_rank2,
            p.league,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team1 AND h.winner='team1')
                      OR (h.team2=res.team1 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=res.team1 OR h.team2=res.team1)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t1_form,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1=res.team1 OR h.team2=res.team1)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t1_form_n,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team2 AND h.winner='team1')
                      OR (h.team2=res.team2 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE (h.team1=res.team2 OR h.team2=res.team2)
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS t2_form,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE (h.team1=res.team2 OR h.team2=res.team2)
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '30 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS t2_form_n,

            COALESCE((
                SELECT AVG(CASE
                    WHEN (h.team1=res.team1 AND h.team2=res.team2 AND h.winner='team1')
                      OR (h.team1=res.team2 AND h.team2=res.team1 AND h.winner='team2')
                    THEN 1.0 ELSE 0.0 END)
                FROM cs2_results h
                WHERE ((h.team1=res.team1 AND h.team2=res.team2)
                       OR (h.team1=res.team2 AND h.team2=res.team1))
                  AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
                  AND h.kickoff_time < res.kickoff_time
                  AND h.winner IN ('team1','team2')
            ), 0.5) AS h2h_t1,
            (SELECT COUNT(*) FROM cs2_results h
             WHERE ((h.team1=res.team1 AND h.team2=res.team2)
                    OR (h.team1=res.team2 AND h.team2=res.team1))
               AND h.kickoff_time >= res.kickoff_time - INTERVAL '365 days'
               AND h.kickoff_time < res.kickoff_time
               AND h.winner IN ('team1','team2')) AS h2h_n,

            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1=res.team1 OR h.team2=res.team1)
                   AND h.kickoff_time < res.kickoff_time)
            )) / 86400, 30) AS t1_days_since,
            COALESCE(EXTRACT(EPOCH FROM (res.kickoff_time -
                (SELECT MAX(h.kickoff_time) FROM cs2_results h
                 WHERE (h.team1=res.team2 OR h.team2=res.team2)
                   AND h.kickoff_time < res.kickoff_time)
            )) / 86400, 30) AS t2_days_since

        FROM cs2_results res
        LEFT JOIN LATERAL (
            SELECT * FROM cs2_predictions p2
            WHERE p2.bo3gg_id = res.bo3gg_id
            ORDER BY scan_time DESC LIMIT 1
        ) p ON TRUE
        WHERE res.winner IN ('team1','team2')
          AND res.kickoff_time >= %s
        ORDER BY res.kickoff_time
        """,
        (since,),
    )


def _logit(p):
    p = max(min(p, 1 - 1e-4), 1e-4)
    return float(np.log(p / (1 - p)))


def build_rows(matches, tm):
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

        # NEW v5 features
        # bo_format encoded as (best_of - 3): Bo1 = -2, Bo3 = 0, Bo5 = +2
        bo_centered = float((m["best_of"] or 3) - 3)

        tier = float(classify_tier(m["league"] or ""))

        # Opponent-strength-adjusted form: weight form_diff by how strong both
        # teams have been ranking. If both top-30 → unchanged. If both ranked
        # 100+ → shrink to 0 because rank is unreliable down there.
        if m["hltv_rank1"] and m["hltv_rank2"]:
            rank_factor = max(0.0, 1.0 - (m["hltv_rank1"] + m["hltv_rank2"]) / 200.0)
        else:
            rank_factor = 0.5  # neutral when ranks unknown
        opp_adj_form = form_diff * rank_factor

        out.append({
            "kickoff": m["kickoff_time"], "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff,
            "h2h_diff": h2h_diff,
            "rest_diff": rest_diff,
            "rank_diff": rank_diff,
            "tm_diff": tm_diff,
            "bo_centered": bo_centered,
            "tier": tier,
            "opp_adj_form": opp_adj_form,
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
    if cut < 50: return {"skipped": True, "n": len(rows)}
    keys = ["logit_saved"] + extra_keys
    X = np.array([[r[k] for k in keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    model = LogisticRegression(max_iter=2000)
    model.fit(X[:cut], y[:cut])
    p = model.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(keys, model.coef_[0].tolist())),
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

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    rows = build_rows(matches, tm)
    print(f"  {len(rows)} matches with saved_prob")
    print(f"  team1 win rate: {sum(r['y'] for r in rows) / len(rows):.3f}")
    print()
    # Tier distribution diagnostic
    tier_counts = {}
    bo_counts = {}
    for r in rows:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
        bo_counts[r["bo_centered"]] = bo_counts.get(r["bo_centered"], 0) + 1
    print(f"  tier distribution: {sorted(tier_counts.items())}")
    print(f"  bo distribution:   {sorted(bo_counts.items())}")
    print()

    cut = int(len(rows) * 0.7)
    y_te = np.array([r["y"] for r in rows[cut:]], dtype=int)

    p_base = np.array([r["saved"] for r in rows[cut:]], dtype=float)
    m_base = _metrics(y_te, p_base)

    print(f"{'set':40} {'n':>5} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
    print("-" * 75)
    print(f"{'baseline (hltv_v1 direct)':40} {len(rows):>5} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
    persist("v5_baseline_hltv_v1", len(rows), m_base, since_d, keys=["win_prob1"], n_train=cut)

    for keys, label in [
        (["bo_centered"],                                "v5 + bo_format"),
        (["tier"],                                       "v5 + tier"),
        (["opp_adj_form"],                               "v5 + opp_adj_form"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff"],
         "v5 v4-ALL (regression check)"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered"],
         "v5 v4-ALL + bo"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff", "bo_centered", "tier"],
         "v5 v4-ALL + bo + tier"),
        (["form_diff", "h2h_diff", "tm_diff", "rest_diff", "rank_diff",
          "bo_centered", "tier", "opp_adj_form"],
         "v5 ALL (kitchen sink)"),
    ]:
        r = evaluate_stack(rows, keys, label)
        if r.get("skipped"):
            print(f"{label:40} {r['n']:>5}  (skipped)")
            continue
        mm = r["metrics"]
        delta_auc = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
        marker = "*" if abs(delta_auc) >= 0.005 else " "
        print(f"{label:40} {r['n']:>5} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
        persist(label, r["n"], mm, since_d, keys=["logit_saved"] + keys,
                coefs=r["coefs"], n_train=r.get("n_train"))

    # Coefficients of the kitchen sink — see which features actually mattered
    final = evaluate_stack(rows, ["form_diff", "h2h_diff", "tm_diff", "rest_diff",
                                   "rank_diff", "bo_centered", "tier", "opp_adj_form"],
                           "_dummy")
    if not final.get("skipped"):
        print()
        print("kitchen-sink coefficients:")
        for k, v in final["coefs"].items():
            print(f"  {k:18} {v:+.4f}")


if __name__ == "__main__":
    main()
