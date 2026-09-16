#!/usr/bin/env python3
"""CANDIDATE-SIGNAL-SCREEN (2026-09-16) — which of the unused signals are worth adding?

`FEED-THE-MODEL-WHAT-WE-ALREADY-COMPUTE`: 50 of the 90 signals we write are not
in the model's feature list, and several are denser than anything it has
(`league_over25_pct` 75.1%, `rest_days_norm_*` 83%). Adding all 50 would be a
different mistake from omitting them — more columns on the same rows is how a
gradient booster learns noise.

So this screens them on evidence rather than judgement, per candidate:

  * coverage      — fraction of finished matches carrying the signal, counting
                    only values captured BEFORE kickoff (see the bound in the
                    query; the unbounded version inflated every league_* AUC)
  * AUC vs target — univariate rank association with over-2.5 AND with home-win,
                    on settled matches. 0.5 is no information. Reported for both
                    because a signal can be strong for goals and useless for
                    outcome, which is the entire argument for splitting the two
                    feature sets (SPLIT-FEATURE-SETS-1X2-VS-GOALS).
  * redundancy    — max |correlation| against any feature the model ALREADY has.
                    A signal that merely restates `elo_diff` adds variance, not
                    information.

WHAT THIS IS NOT. Univariate AUC cannot see interactions, so a low score here is
weak evidence against and a high score is not proof of usefulness in the fitted
model — only a retrain measures that. This ranks what to TRY, it does not decide
what works. The decision still comes from the residual harness.

Read-only.

    python3 scripts/candidate_signal_screen.py [--days 400] [--min-n 2000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from workers.api_clients.db import execute_query  # noqa: E402


def auc(scores: list[float], ys: list[int]) -> float:
    """Rank AUC with tie handling. Returns nan when a class is absent."""
    pairs = sorted(zip(scores, ys))
    n = len(pairs)
    ranks: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    pos = sum(y for _, y in pairs)
    neg = n - pos
    if not pos or not neg:
        return float("nan")
    s = sum(ranks[k] for k, (_, y) in enumerate(pairs) if y == 1)
    return (s - pos * (pos + 1) / 2) / (pos * neg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-n", type=int, default=2000)
    a = ap.parse_args()

    import joblib
    cols = joblib.load("data/models/soccer/v20260914_clean_cut0820/feature_cols.pkl")
    already = {c for c in cols if not c.endswith("_missing")}

    names = [r["signal_name"] for r in (execute_query(
        """SELECT signal_name FROM match_signals
            WHERE captured_at > now() - make_interval(days => %s)
            GROUP BY 1 HAVING count(DISTINCT match_id) >= %s""",
        (a.days, a.min_n)) or [])]
    cands = sorted(set(names) - already)
    if not cands:
        print("no candidates")
        return 1

    tot = execute_query(
        """SELECT count(*) n FROM matches WHERE status='finished'
            AND score_home IS NOT NULL AND date > now() - make_interval(days => %s)""",
        (a.days,))[0]["n"]

    # Existing model features for the redundancy check, from the SAME rows.
    have_mfv = {r["column_name"] for r in execute_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='match_feature_vectors'") or []}
    ref_cols = sorted(already & have_mfv)

    print(f"\n=== candidate signal screen — {len(cands)} unused signals, "
          f"{tot:,} settled matches in {a.days}d ===\n")
    print(f"{'signal':34s} {'n':>7s} {'cov':>6s} {'AUC o2.5':>9s} {'AUC home':>9s} {'max|r| vs existing':>19s}")

    rows = []
    for sig in cands:
        d = execute_query(
            f"""SELECT ms.signal_value::float v,
                       (m.score_home + m.score_away > 2.5)::int y_ou,
                       (m.score_home > m.score_away)::int y_hw,
                       {', '.join(f'mfv."{c}"::float AS "r{i}"' for i, c in enumerate(ref_cols))}
                  FROM matches m
                  -- PRE-KICKOFF BOUND (2026-09-16). Without it this takes the
                  -- LATEST captured value, which for a match played months ago
                  -- can have been written long afterwards — and the league
                  -- aggregates are computed over "the last 200 finished matches
                  -- in this league" with no bound relative to the match being
                  -- scored, so a later capture encodes results that had not
                  -- happened yet. The same defect shape the 1x2 residual test
                  -- was already guarded against (28 pct of its "pre-kickoff"
                  -- Pinnacle prices were collected after kickoff).
                  JOIN LATERAL (SELECT signal_value FROM match_signals
                                 WHERE match_id = m.id AND signal_name = %s
                                   AND captured_at < m.date
                                 ORDER BY captured_at DESC LIMIT 1) ms ON true
                  LEFT JOIN match_feature_vectors mfv ON mfv.match_id = m.id
                 WHERE m.status='finished' AND m.score_home IS NOT NULL
                   AND m.date > now() - make_interval(days => %s)
                   AND ms.signal_value IS NOT NULL""",
            (sig, a.days)) or []
        if len(d) < a.min_n:
            continue
        v = [float(r["v"]) for r in d]
        if len(set(v)) < 3:
            continue
        a_ou = auc(v, [int(r["y_ou"]) for r in d])
        a_hw = auc(v, [int(r["y_hw"]) for r in d])
        best_r, best_c = 0.0, "-"
        av = np.array(v)
        for i, c in enumerate(ref_cols):
            o = np.array([(float(r[f"r{i}"]) if r[f"r{i}"] is not None else np.nan) for r in d])
            m = ~np.isnan(o)
            if m.sum() < 500 or np.std(o[m]) == 0 or np.std(av[m]) == 0:
                continue
            r_ = abs(float(np.corrcoef(av[m], o[m])[0, 1]))
            if r_ > best_r:
                best_r, best_c = r_, c
        rows.append((sig, len(d), len(d) / tot, a_ou, a_hw, best_r, best_c))

    # Rank by the stronger of the two AUCs, distance from 0.5.
    rows.sort(key=lambda r: -max(abs(r[3] - 0.5), abs(r[4] - 0.5)))
    for sig, n, cov, a_ou, a_hw, r_, rc in rows:
        flag = ""
        if r_ > 0.9:
            flag = f"  REDUNDANT vs {rc}"
        elif max(abs(a_ou - 0.5), abs(a_hw - 0.5)) < 0.01:
            flag = "  no univariate signal"
        print(f"  {sig:32s} {n:7,} {cov*100:5.1f}% {a_ou:9.4f} {a_hw:9.4f} "
              f"{r_:9.2f} ({rc[:14]}){flag}")

    print("\nAUC 0.5 = no information. |AUC-0.5| > 0.02 on 10k+ rows is a real association.")
    print("Univariate screening cannot see interactions: this ranks what to TRY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
