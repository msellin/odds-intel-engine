#!/usr/bin/env python3
"""DIXON-COLES-SCORE — phase 4: the alpha this whole plan exists to produce.

Takes the walk-forward per-match probabilities from `dixon_coles_fit.py` and
scores them through the SAME harness that measured the shipped heads at
alpha = 0.0000 — so the number is directly comparable rather than merely
similar.

WHY IT HAS TO BE THE SAME HARNESS. `residual_test_ou.py` reads a model BUNDLE;
Dixon-Coles is not a bundle, it is a CSV of per-match rates. The temptation is
to write a fresh scorer, and then any difference in the answer could be the
model or could be the scorer. Instead this imports the harness functions
(`fit_platt`, `fit_alpha`, `ll`, `auc`, `shin2`, `devig_two_way`, `ou_target`)
from that file and reproduces its exact sequence: Platt for LEVEL only on the
first half, alpha fitted on the first half, everything evaluated on the second,
with a Shin de-vig arm as the pre-registered robustness check.

Those functions were verified against known answers on 2026-09-16
(`verify_residual_harness.py`): the harness recovers a planted alpha of 1.0000
when the model is truth and 0.6450 on a small realistic edge, so an alpha of 0
here means the absence of edge rather than the absence of an instrument.

NO POST-HOC ARM. The XGBoost heads run OPTIMISTIC/REALISTIC because eight of
their features are post-hoc columns that must be NULLed to be honest.
Dixon-Coles has no features — only (home, away, score, date) — so there is
nothing to null and the single arm IS the realistic one.

    python3 scripts/dixon_coles_score.py --rates /tmp/dc_rates_xi0.csv
"""
from __future__ import annotations

import argparse
import csv
import importlib.util as _ilu
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_spec = _ilu.spec_from_file_location(
    "_rt_ou", Path(__file__).resolve().parent / "residual_test_ou.py")
_rt = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rt)

from workers.api_clients.db import execute_query  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", default="/tmp/dc_rates_xi0.csv")
    ap.add_argument("--cutoff", default="2026-08-20")
    a = ap.parse_args()

    rates = {r["match_id"]: r for r in csv.DictReader(open(a.rates))}
    if not rates:
        print("no rates")
        return 1

    # Pinnacle O/U 2.5, pre-kickoff, non-closing — the identical market
    # definition residual_test_ou.py uses, including the pre-KO bound.
    rows = execute_query(
        """
        WITH pin AS (SELECT DISTINCT ON (o.match_id, o.selection)
                            o.match_id::text AS mid, o.selection, o.odds::float od
                       FROM odds_snapshots o
                      WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
                        AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
                        AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT m.id::text AS mid, m.date,
               (m.score_home + m.score_away) AS total_goals,
               po.od AS po, pu.od AS pu
          FROM matches m
          JOIN pin po ON po.mid = m.id::text AND po.selection='over'
          JOIN pin pu ON pu.mid = m.id::text AND pu.selection='under'
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.score_away IS NOT NULL AND m.date >= %s
         ORDER BY m.date""",
        (a.cutoff,)) or []

    joined = [(r, rates[r["mid"]]) for r in rows if r["mid"] in rates]
    if len(joined) < 200:
        print(f"only {len(joined)} matches have BOTH a Dixon-Coles rate and a "
              f"Pinnacle O/U pair — too few to conclude")
        return 1

    ys = [_rt.ou_target(r["total_goals"], 2.5) for r, _ in joined]
    pk = [_rt.devig_two_way(r["po"], r["pu"]) for r, _ in joined]
    pk_shin = [_rt.shin2(r["po"], r["pu"]) for r, _ in joined]
    raw = [float(d["p_over25"]) for _, d in joined]
    inv = [1 / r["po"] + 1 / r["pu"] for r, _ in joined]

    n = len(joined)
    print(f"\nDIXON-COLES vs PINNACLE (O/U 2.5) — matches on/after {a.cutoff}")
    print(f"  n = {n}   over-2.5 base rate = {sum(ys)/n:.4f}")
    print(f"  Pinnacle overround = {sum(inv)/n:.4f}  (vig ~ {100*(sum(inv)/n-1):.2f} pct)")
    print(f"  DC raw mean P(over) = {sum(raw)/n:.4f}  (actual {sum(ys)/n:.4f}, "
          f"gap {sum(raw)/n - sum(ys)/n:+.4f})\n")

    cut = n // 2
    for arm, mkt in (("PROPORTIONAL de-vig — DECIDES", pk),
                     ("SHIN de-vig (robustness)", pk_shin)):
        a_, b_ = _rt.fit_platt(list(zip(raw[:cut], [float(v) for v in ys[:cut]])))
        pm = [_rt.sig(a_ * p + b_) for p in raw]
        alpha = _rt.fit_alpha(pm[:cut], mkt[:cut], ys[:cut])
        te = slice(cut, None)
        l_mkt = _rt.ll(mkt[te], ys[te])
        l_mod = _rt.ll(pm[te], ys[te])
        l_bl = _rt.ll([alpha * m + (1 - alpha) * k
                       for m, k in zip(pm[te], mkt[te])], ys[te])
        resid = _rt.auc([m - k for m, k in zip(pm[te], mkt[te])], ys[te])
        ok = (l_bl < l_mkt) and (alpha > 0.02)
        print(f"  -- {arm}")
        print(f"     Platt a={a_:.3f} b={b_:+.3f}   fitted alpha = {alpha:.4f}")
        print(f"     market alone   log-loss {l_mkt:.4f}   AUC {_rt.auc(mkt[te], ys[te]):.4f}")
        print(f"     DC alone       log-loss {l_mod:.4f}   AUC {_rt.auc(pm[te], ys[te]):.4f}")
        print(f"     BLEND          log-loss {l_bl:.4f}")
        print(f"     blend vs market: {100*(l_mkt-l_bl)/l_mkt:+.3f} pct")
        print(f"     residual AUC (DC-market predicts outcome): {resid:.4f}  (0.5 = no info)")
        print(f"     PRIMARY: {'PASS' if ok else 'FAIL'}  (needs blend<market AND alpha>0.02)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
