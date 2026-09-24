#!/usr/bin/env python3
"""1X2 LINEUP / PLAYER STRENGTH ([[#141]] round 3c).

Pre-registration: `dev/active/1x2-model-rebuild-plan.md`, "ROUND 3c", written before
this ran. Family L1-L4 (Holm m=4), half-life selected on 08-01..08-30, confirmed once
on 08-31.. .

    python3 scripts/ab_1x2_lineups.py --select
    python3 scripts/ab_1x2_lineups.py --confirm --half-life 365
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import ab_1x2_rating_arms as A  # noqa: E402
import ab_1x2_combined as CB  # noqa: E402
from workers.model.player_strength_1x2 import build_xi_features  # noqa: E402
from workers.model.combined_1x2 import fit as comb_fit, predict as comb_predict  # noqa: E402

DET = A.CACHE / "fixture_details"
XI_ACTUAL = ["xi_diff", "xi_delta_h", "xi_delta_a"]
XI_PREV = ["prev_diff", "prev_delta_h", "prev_delta_a"]


def _load(kind: str) -> pd.DataFrame:
    return pd.concat([pd.read_parquet(p) for p in sorted(DET.glob(f"{kind}_*.parquet"))], ignore_index=True)


def xi_features(half_life: float) -> pd.DataFrame:
    m = pd.read_parquet(A.CACHE / "matches.parquet")
    m["kickoff"] = pd.to_datetime(m.kickoff, utc=True)
    fm = pd.DataFrame({"fixture_id": m.af_fixture_id, "home_af": m.home_af, "away_af": m.away_af,
                       "kickoff": (m.kickoff - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()})
    h = pd.read_parquet(A.CACHE / "af_history.parquet")
    h["kickoff"] = pd.to_datetime(h.kickoff, utc=True)
    fh = pd.DataFrame({"fixture_id": h.af_fixture_id, "home_af": h.home_af, "away_af": h.away_af,
                       "kickoff": (h.kickoff - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()})
    fx = pd.concat([fm, fh[~fh.fixture_id.isin(set(fm.fixture_id.dropna()))]], ignore_index=True).dropna()
    xf = build_xi_features(fx, _load("players"), _load("lineups"), half_life)
    return xf.rename(columns={"fixture_id": "af_fixture_id"})


def attach(d: pd.DataFrame, xf: pd.DataFrame) -> pd.DataFrame:
    d = d.merge(xf, on="af_fixture_id", how="left")
    return d


def holm(ps: dict) -> dict:
    order = sorted(ps, key=ps.get); adj, run = {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (len(ps) - i) * ps[k])); adj[k] = run
    return adj


def run(half_life: float, fit_hi: str, lo: str, hi: str, label: str) -> None:
    xf = xi_features(half_life)
    res, ps = {}, {}
    # L1 — rating model with / without the ACTUAL-XI features
    f = attach(pd.read_parquet(A.CACHE / "features_hist_full.parquet"), xf)
    for c in XI_ACTUAL:
        f[c + "_has"] = f[c].notna().astype(float)
    g = A.gated(f)
    tr = f[(f.kickoff < fit_hi) & (f.kickoff >= "2022-07-01") & g]
    te = f[(f.kickoff >= lo) & (f.kickoff < hi)]
    y = te.y.to_numpy()
    base = A.fit_predict("D8+", tr, te)
    A.ARM_FEATURES["D8+XI"] = A.ARM_FEATURES["D8+"] + XI_ACTUAL + [c + "_has" for c in XI_ACTUAL]
    trx, tex = tr.copy(), te.copy()
    for c in XI_ACTUAL:
        trx[c] = trx[c].fillna(0.0); tex[c] = tex[c].fillna(0.0)
    withxi = A.fit_predict("D8+XI", trx, tex)
    d = A.per_match_ll(withxi, y) - A.per_match_ll(base, y)
    mu, clo, chi, p = A.boot_ci(-d)
    res["L1 rating +XI actual"] = (A.per_match_ll(base, y).mean(), A.per_match_ll(withxi, y).mean(), len(te),
                                   float(te.xi_diff.notna().mean())); ps["L1"] = p
    # L2-L4 — combined model (production combiner code) with / without XI
    for lid, which, cols in (("L2", "open", XI_ACTUAL), ("L3", "close", XI_ACTUAL), ("L4", "open", XI_PREV)):
        dd = attach(CB.build(which), xf)
        ctr = dd[(dd.kickoff >= CB.START) & (dd.kickoff < fit_hi)]
        cte = dd[(dd.kickoff >= lo) & (dd.kickoff < hi)]
        y2 = cte.y.to_numpy()
        P0, _ = comb_predict(cte, comb_fit(ctr))
        P1, _ = comb_predict(cte, comb_fit(ctr, extra=cols))
        l0, l1 = A.per_match_ll(P0, y2), A.per_match_ll(P1, y2)
        mu, clo, chi, p = A.boot_ci(l0 - l1)
        res[f"{lid} combined {which.upper()} +{'XI actual' if cols is XI_ACTUAL else 'XI previous'}"] = (
            l0.mean(), l1.mean(), len(cte), float(cte[cols[0]].notna().mean())); ps[lid] = p
    adj = holm(ps)
    print(f"\n== {label}: half-life {half_life} d, fit < {fit_hi}, score {lo}..{hi}")
    for (k, (a, b, n, cov)), lid in zip(res.items(), ["L1", "L2", "L3", "L4"]):
        print(f"  {k:36s} n={n:6,} XI coverage {cov:5.1%}  without {a:.4f}  with {b:.4f}  Δ {b - a:+.4f}  "
              f"p {ps[lid]:.3f}  Holm {adj[lid]:.3f}  {'PASS' if (b < a and adj[lid] < 0.05) else 'FAIL'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--half-life", type=float, default=365.0)
    a = ap.parse_args()
    if a.select:
        for hl in (180.0, 365.0):
            run(hl, "2026-08-01", "2026-08-01", "2026-08-31", "SELECTION")
    if a.confirm:
        run(a.half_life, "2026-08-31", "2026-08-31", "2099-01-01", "CONFIRM (run once)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
