#!/usr/bin/env python3
"""1X2 COMBINED MODEL ([[#141]] round 3b) — ratings + bookmaker consensus + Pinnacle.

Pre-registration: `dev/active/1x2-model-rebuild-plan.md`, "ROUND 3b", written
before this ran. Arms R / RULE / COMB / COMB+AF, price timing CLOSE and OPEN,
combiner selected on 2026-08-01..08-30 and confirmed once on 2026-08-31..

Sources per match (all as log-odds vs the draw):
  R    rating model H-D8+ — logit fitted on gated rows <= 2026-04-30, so every
       combiner row (>= 2026-05-01) is out-of-sample for it
  C    de-vigged multi-book consensus (workers/model/market_consensus_1x2.py)
  P    Pinnacle, de-vigged separately
  AF   API-Football's prediction percent + its comparison.total home share

The combiner is a multinomial logit PER AVAILABILITY GROUP ({P&C, P, C, none}),
because a missing source is not a zero — one model per pattern avoids inventing
fill values.

    python3 scripts/ab_1x2_combined.py --pull      # legs + AF -> cache (read-only DB)
    python3 scripts/ab_1x2_combined.py --run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import ab_1x2_rating_arms as A  # noqa: E402
from workers.model.market_consensus_1x2 import fetch_legs, consensus  # noqa: E402

START = "2026-05-01"
CACHE = A.CACHE


def pull() -> None:
    f = pd.read_parquet(CACHE / "features_hist_full.parquet", columns=["match_id", "kickoff"])
    ids = f[f.kickoff >= START].match_id.tolist()
    c = A._conn()
    for which in ("close", "open"):
        parts = []
        for i in range(0, len(ids), 3000):
            parts.append(fetch_legs(c, ids[i:i + 3000], which))
            print(f"  {which}: {min(i + 3000, len(ids)):,}/{len(ids):,}", flush=True)
        legs = pd.concat(parts, ignore_index=True)
        legs.to_parquet(CACHE / f"legs_{which}.parquet")
        print(f"{which}: {len(legs):,} legs")
    af = pd.read_sql("""
        SELECT id::text match_id,
               af_prediction->'predictions'->'percent'->>'home' af_h,
               af_prediction->'predictions'->'percent'->>'draw' af_d,
               af_prediction->'predictions'->'percent'->>'away' af_a,
               af_prediction->'comparison'->'total'->>'home' af_th
          FROM matches WHERE id::text = ANY(%(ids)s) AND af_prediction IS NOT NULL""", c, params={"ids": ids})
    af.to_parquet(CACHE / "af_pred.parquet")
    print(f"af: {len(af):,}")
    c.close()


def _lo(p: np.ndarray) -> np.ndarray:
    """(n,3) probabilities -> (n,2) log-odds vs draw."""
    p = np.clip(p, 1e-4, 1)
    return np.stack([np.log(p[:, 0] / p[:, 1]), np.log(p[:, 2] / p[:, 1])], 1)


def build(which: str) -> pd.DataFrame:
    f = pd.read_parquet(CACHE / "features_hist_full.parquet")
    g = A.gated(f)
    tr = f[(f.kickoff <= "2026-04-30 23:59") & (f.kickoff >= "2022-07-01") & g]
    d = f[f.kickoff >= START].copy()
    R = A.fit_predict("D8+", tr, d)
    d[["r_h", "r_d", "r_a"]] = R
    cons = consensus(pd.read_parquet(CACHE / f"legs_{which}.parquet"))
    d = d.merge(cons, left_on="match_id", right_index=True, how="left")
    af = pd.read_parquet(CACHE / "af_pred.parquet")
    for k in ("af_h", "af_d", "af_a", "af_th"):
        af[k] = pd.to_numeric(af[k].str.rstrip("%"), errors="coerce") / 100
    d = d.merge(af, on="match_id", how="left")
    d["has_c"] = d.c_h.notna()
    d["has_p"] = d.pin_h.notna()
    d["has_af"] = d.af_h.notna() & d.af_th.notna()
    d["group"] = np.where(d.has_p & d.has_c, "P&C", np.where(d.has_p, "P", np.where(d.has_c, "C", "none")))
    return d.sort_values("kickoff").reset_index(drop=True)


def _X(d: pd.DataFrame, group: str, use_af: bool) -> np.ndarray:
    cols = [_lo(d[["r_h", "r_d", "r_a"]].to_numpy())]
    if group in ("P&C", "C"):
        cols += [_lo(d[["c_h", "c_d", "c_a"]].to_numpy()), np.log(d.n_books.to_numpy())[:, None]]
    if group in ("P&C", "P"):
        cols.append(_lo(d[["pin_h", "pin_d", "pin_a"]].to_numpy()))
    if use_af:
        afp = d[["af_h", "af_d", "af_a"]].fillna(1 / 3).to_numpy()
        th = d.af_th.fillna(0.5).clip(0.02, 0.98).to_numpy()
        cols += [_lo(afp), np.log(th / (1 - th))[:, None], d.has_af.to_numpy(float)[:, None]]
    return np.hstack(cols)


def combiner(tr: pd.DataFrame, te: pd.DataFrame, use_af) -> np.ndarray:
    """use_af: False, True, or "none_only" (COMB-HYB — AF only where no book prices the match)."""
    from sklearn.linear_model import LogisticRegression
    P = te[["r_h", "r_d", "r_a"]].to_numpy().copy()
    for grp in ("P&C", "P", "C", "none"):
        a, b = tr[tr.group == grp], te.group.to_numpy() == grp
        if b.sum() == 0:
            continue
        if len(a) < 300:            # too few to fit: fall back to the RULE source
            continue
        uaf = (grp == "none") if use_af == "none_only" else bool(use_af)
        m = LogisticRegression(max_iter=3000, C=1.0).fit(_X(a, grp, uaf), a.y)
        P[b] = m.predict_proba(_X(te[b], grp, uaf))
    return P


def rule(d: pd.DataFrame) -> np.ndarray:
    P = d[["r_h", "r_d", "r_a"]].to_numpy().copy()
    c = d.has_c.to_numpy(); P[c] = d.loc[c, ["c_h", "c_d", "c_a"]].to_numpy()
    p = d.has_p.to_numpy(); P[p] = d.loc[p, ["pin_h", "pin_d", "pin_a"]].to_numpy()
    return P


def evaluate(d: pd.DataFrame, fit_lo: str, fit_hi: str, te_lo: str, te_hi: str, label: str) -> dict:
    tr = d[(d.kickoff >= fit_lo) & (d.kickoff < fit_hi)]
    te = d[(d.kickoff >= te_lo) & (d.kickoff < te_hi)]
    y = te.y.to_numpy()
    arms = {"R": te[["r_h", "r_d", "r_a"]].to_numpy(), "RULE": rule(te),
            "COMB": combiner(tr, te, False), "COMB+AF": combiner(tr, te, True),
            "COMB-HYB": combiner(tr, te, "none_only")}
    ll = {k: A.per_match_ll(v, y) for k, v in arms.items()}
    print(f"\n== {label}: fit {fit_lo}..{fit_hi} ({len(tr):,}), score {te_lo}..{te_hi} ({len(te):,})")
    out = {}
    for grp in ("ALL", "P&C", "P", "C", "none"):
        mk = np.ones(len(te), bool) if grp == "ALL" else (te.group.to_numpy() == grp)
        if mk.sum() < 30:
            continue
        line = f"  {grp:5s} n={mk.sum():6,}  " + "  ".join(f"{k} {v[mk].mean():.4f}" for k, v in ll.items())
        for k in ("COMB", "COMB+AF", "COMB-HYB"):
            mu, lo, hi, _ = A.boot_ci(ll[k][mk] - ll["RULE"][mk])
            line += f" | {k}-RULE {mu:+.4f} [{lo:+.4f},{hi:+.4f}]"
        print(line)
        out[grp] = {k: float(v[mk].mean()) for k, v in ll.items()} | {"n": int(mk.sum())}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="also score the Q1 window (run ONCE)")
    a = ap.parse_args()
    if a.pull:
        pull()
    if a.run:
        res = {}
        for which in ("close", "open"):
            d = build(which)
            res[f"{which}_select"] = evaluate(d, START, "2026-08-01", "2026-08-01", "2026-08-31",
                                              f"{which.upper()} — SELECTION")
            if a.confirm:
                res[f"{which}_confirm"] = evaluate(d, START, "2026-08-31", "2026-08-31", "2099-01-01",
                                                   f"{which.upper()} — CONFIRM (Q1 window)")
        (CACHE / "round3b_results.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
