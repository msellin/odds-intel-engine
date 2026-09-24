#!/usr/bin/env python3
"""#089 — FAITHFUL WHEATCROFT (2020, IJF 36(3)) REPLICATION on football-data.co.uk.

Pre-registered in dev/active/per-market-feature-sets-design.md ("#089 FAITHFUL WHEATCROFT
REPLICATION") before this was written. Separates "our shots construction was unfaithful"
from "a shots rating adds nothing to a sharp price".

GAP ratings (his eqs 1-2): four per team — home attack/defence, away attack/defence —
additive updates, floored at 0, no decay. After home i vs away j with outputs S_h, S_a:

    e_h = S_h − (H_i^a + A_j^d)/2        e_a = S_a − (A_j^a + H_i^d)/2
    H_i^a += λφ1·e_h   A_i^a += λ(1−φ1)·e_h     H_i^d += λφ1·e_a   A_i^d += λ(1−φ1)·e_a
    A_j^a += λφ2·e_a   H_j^a += λ(1−φ2)·e_a     A_j^d += λφ2·e_h   H_j^d += λ(1−φ2)·e_h

Forecast: logit P(over 2.5) = α + β1·(H_i^a + H_i^d + A_j^a + A_j^d) + β2·m — the market
term m is ALWAYS in the model; the question is whether β1 adds anything.

    python3 scripts/wheatcroft_replication.py            # full run (~20-40 min)
    python3 scripts/wheatcroft_replication.py --quick    # fixed params, no tuning (smoke)
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent / "data" / "raw" / "football_data_co_uk" / "main"
LEAGUES = ("E0", "E1", "E2", "E3", "EC", "SC0", "SP1", "I1", "F1", "D1")
FIRST_SEASON, BURN_IN = "0506", "0506"
PIN_ERA = "1920"                     # Pinnacle pre-close + close columns exist from here
MIN_PLAYED, EXCL_LAST = 6, 6         # his eligibility rule
THETA0 = (0.44, 0.49, 0.6)           # his shots fit, the Nelder-Mead start
INPUTS = ("shots_corners", "goals")
N_BOOT = 2000


# ── data ──────────────────────────────────────────────────────────────────────
def _col(d, *names):
    for n in names:
        if n in d.columns:
            return pd.to_numeric(d[n], errors="coerce")
    return pd.Series(np.nan, index=d.index)


def load() -> pd.DataFrame:
    frames = []
    for lg in LEAGUES:
        for f in sorted((BASE / lg).glob("*.csv")):
            s = f.stem
            d = pd.read_csv(f, encoding="latin-1", on_bad_lines="skip")
            d = d[pd.to_numeric(d.get("FTHG"), errors="coerce").notna() & d["HomeTeam"].notna()]
            if d.empty:
                continue
            out = pd.DataFrame({
                "lg": lg, "season": s,
                "date": pd.to_datetime(d["Date"], dayfirst=True, errors="coerce", format="mixed"),
                "h": d["HomeTeam"].str.strip(), "a": d["AwayTeam"].str.strip(),
                "hg": _col(d, "FTHG"), "ag": _col(d, "FTAG"),
                "hs": _col(d, "HS"), "as_": _col(d, "AS"), "hc": _col(d, "HC"), "ac": _col(d, "AC"),
                "max_o": _col(d, "Max>2.5", "BbMx>2.5"), "max_u": _col(d, "Max<2.5", "BbMx<2.5"),
                "avg_o": _col(d, "Avg>2.5", "BbAv>2.5"), "avg_u": _col(d, "Avg<2.5", "BbAv<2.5"),
                "p_o": _col(d, "P>2.5"), "p_u": _col(d, "P<2.5"),
                "pc_o": _col(d, "PC>2.5"), "pc_u": _col(d, "PC<2.5"),
            })
            frames.append(out)
    df = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    df = df.sort_values(["date", "lg", "h"], kind="stable").reset_index(drop=True)
    df["over"] = ((df.hg + df.ag) > 2.5).astype(float)
    df["has_sc"] = df[["hs", "as_", "hc", "ac"]].notna().all(axis=1)
    # eligibility: both teams >= 6 league games played this season, and not in either's last 6
    df["n_h"] = df.groupby(["lg", "season", "h"]).cumcount()  # placeholder, replaced below
    played, total = defaultdict(int), defaultdict(int)
    for lg, s, h, a in zip(df.lg, df.season, df.h, df.a):
        total[(lg, s, h)] += 1
        total[(lg, s, a)] += 1
    ph, pa, rh, ra = [], [], [], []
    for lg, s, h, a in zip(df.lg, df.season, df.h, df.a):
        ph.append(played[(lg, s, h)]); pa.append(played[(lg, s, a)])
        played[(lg, s, h)] += 1; played[(lg, s, a)] += 1
        rh.append(total[(lg, s, h)] - played[(lg, s, h)]); ra.append(total[(lg, s, a)] - played[(lg, s, a)])
    df["elig"] = ((np.array(ph) >= MIN_PLAYED) & (np.array(pa) >= MIN_PLAYED)
                  & (np.array(rh) >= EXCL_LAST) & (np.array(ra) >= EXCL_LAST))
    df = df.drop(columns="n_h")
    df["week"] = df.date.dt.strftime("%G-%V")
    return df


# ── GAP ratings ───────────────────────────────────────────────────────────────
def gap_sums(df: pd.DataFrame, inp: str, theta) -> np.ndarray:
    """Walk every match in date order; return the PRE-match rating sum per row
    (NaN where the input is missing). Ratings keyed by (league, team); a team new to
    a league inherits the mean last rating of the teams that left it."""
    lam, f1, f2 = theta
    if inp == "goals":
        SH, SA = df.hg.to_numpy(), df.ag.to_numpy()
    else:
        SH, SA = (df.hs + df.hc).to_numpy(), (df.as_ + df.ac).to_numpy()
    R: dict = {}
    members: dict = defaultdict(set)          # (lg, season) -> teams
    for lg, s, h, a in zip(df.lg, df.season, df.h, df.a):
        members[(lg, s)].update((h, a))
    seasons = sorted({s for _, s in members})
    prev = {}
    for s in seasons:                          # promotion / relegation inheritance
        for lg in LEAGUES:
            now = members.get((lg, s))
            if not now:
                continue
            before = prev.get(lg)
            if before:
                left = [R[(lg, t)] for t in before - now if (lg, t) in R]
                mean = [sum(x[k] for x in left) / len(left) for k in range(4)] if left else [0.0] * 4
                for t in now - before:
                    R[(lg, t)] = list(mean)
            prev[lg] = now
    out = np.full(len(df), np.nan)
    la1, la1c, la2, la2c = lam * f1, lam * (1 - f1), lam * f2, lam * (1 - f2)
    for idx, (lg, h, a, sh, sa) in enumerate(zip(df.lg, df.h, df.a, SH, SA)):
        ri = R.get((lg, h))
        if ri is None:
            ri = R[(lg, h)] = [0.0, 0.0, 0.0, 0.0]    # [H_a, H_d, A_a, A_d]
        rj = R.get((lg, a))
        if rj is None:
            rj = R[(lg, a)] = [0.0, 0.0, 0.0, 0.0]
        if sh != sh or sa != sa:                      # input missing: no rating, no update
            continue
        out[idx] = ri[0] + ri[1] + rj[2] + rj[3]
        e_h = sh - (ri[0] + rj[3]) / 2
        e_a = sa - (rj[2] + ri[1]) / 2
        ri[0] = max(ri[0] + la1 * e_h, 0.0); ri[2] = max(ri[2] + la1c * e_h, 0.0)
        ri[1] = max(ri[1] + la1 * e_a, 0.0); ri[3] = max(ri[3] + la1c * e_a, 0.0)
        rj[2] = max(rj[2] + la2 * e_a, 0.0); rj[0] = max(rj[0] + la2c * e_a, 0.0)
        rj[3] = max(rj[3] + la2 * e_h, 0.0); rj[1] = max(rj[1] + la2c * e_h, 0.0)
    return out


# ── logistic (MLE, Newton) ────────────────────────────────────────────────────
def logit_fit(X: np.ndarray, y: np.ndarray, iters: int = 25) -> np.ndarray:
    X1 = np.column_stack([np.ones(len(y)), X])
    mu, sd = X1.mean(0), X1.std(0)
    mu[0], sd[0] = 0.0, 1.0
    sd[sd == 0] = 1.0
    Z = (X1 - mu) / sd
    b = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Z @ b, -30, 30)))
        g = Z.T @ (y - p)
        H = (Z * (p * (1 - p))[:, None]).T @ Z + 1e-9 * np.eye(len(b))
        step = np.linalg.solve(H, g)
        b += step
        if np.abs(step).max() < 1e-8:
            break
    w = b / sd                                   # back to raw scale
    w[0] = b[0] - (b[1:] * mu[1:] / sd[1:]).sum()
    return w


def logit_pred(w, X):
    return 1 / (1 + np.exp(-np.clip(w[0] + np.atleast_2d(X) @ w[1:], -30, 30)))


def ll(p, y):
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


# ── market terms ──────────────────────────────────────────────────────────────
def market_terms(df: pd.DataFrame) -> dict[str, np.ndarray]:
    from workers.model.devig import devig

    def shin(o, u):
        out = np.full(len(o), np.nan)
        for i, (a, b) in enumerate(zip(o, u)):
            if a == a and b == b and a > 1 and b > 1:
                p = devig([a, b])
                if p:
                    out[i] = p[0]
        return out
    df["pin_pre_fair"] = shin(df.p_o.to_numpy(), df.p_u.to_numpy())
    df["pin_close_fair"] = shin(df.pc_o.to_numpy(), df.pc_u.to_numpy())
    return {
        "M1": 1 / df.max_o.to_numpy(),                 # his term: 1/max odds, not de-vigged
        "M2": logit(df.pin_pre_fair.to_numpy()),
        "M3": logit(df.pin_close_fair.to_numpy()),
    }


# ── tuning (between seasons, pooled) ──────────────────────────────────────────
def tune(df, inp, season, m1, theta_start, maxfev):
    from scipy.optimize import minimize
    prior = (df.season < season).to_numpy() & df.elig.to_numpy() & np.isfinite(m1)
    y = df.over.to_numpy()

    def obj(th):
        th = (min(max(th[0], 1e-3), 1.0), min(max(th[1], 0.0), 1.0), min(max(th[2], 0.0), 1.0))
        s = gap_sums(df, inp, th)
        k = prior & np.isfinite(s)
        X = np.column_stack([s[k], m1[k]])
        w = logit_fit(X, y[k])
        return ll(logit_pred(w, X), y[k]).mean()
    r = minimize(obj, theta_start, method="Nelder-Mead",
                 bounds=[(1e-3, 1.0), (0.0, 1.0), (0.0, 1.0)],
                 options={"maxfev": maxfev, "xatol": 2e-3, "fatol": 1e-6})
    return tuple(float(x) for x in r.x)


# ── evaluation ────────────────────────────────────────────────────────────────
def weekly_forecasts(df, S, m, mask):
    """Refit weekly on all PRIOR eligible matches; return p_full, p_mkt for rows in mask."""
    y = df.over.to_numpy()
    wk = df.week.to_numpy()
    # has_sc on the TRAINING rows too: otherwise the goals input trains on EC matches
    # that have no shots, starts its weekly fits earlier, and scores different fixtures.
    base = df.elig.to_numpy() & np.isfinite(S) & np.isfinite(m) & df.has_sc.to_numpy()
    pf, pm = np.full(len(df), np.nan), np.full(len(df), np.nan)
    weeks = sorted(set(wk[mask & base]))
    first_row = {w: i for i, w in reversed(list(enumerate(wk)))}
    for w in weeks:
        cut = first_row[w]
        tr = base.copy(); tr[cut:] = False
        # a market term's own fit must use matches that HAVE that market
        if tr.sum() < 500:
            continue
        te = mask & base & (wk == w)
        wf = logit_fit(np.column_stack([S[tr], m[tr]]), y[tr])
        wm = logit_fit(m[tr][:, None], y[tr])
        pf[te] = logit_pred(wf, np.column_stack([S[te], m[te]]))
        pm[te] = logit_pred(wm, m[te][:, None])
    return pf, pm


def boot_p(d, weeks, rng):
    uw, inv = np.unique(weeks, return_inverse=True)
    sums = np.bincount(inv, weights=d); cnts = np.bincount(inv)
    idx = rng.integers(0, len(uw), size=(N_BOOT, len(uw)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return float((means <= 0).mean())


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, run = [0.0] * len(ps), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        out[i] = run
    return out


def money(p_over, o_over, o_under, y, close_fair=None):
    """Level stakes: back over if p·o_over > 1, else under if (1−p)·o_under > 1."""
    stakes = rets = clvs = 0.0
    n = 0
    clv_list = []
    for p, oo, ou, yy, cf in zip(p_over, o_over, o_under, y,
                                 close_fair if close_fair is not None else [np.nan] * len(y)):
        if not (p == p and oo == oo and ou == ou and oo > 1 and ou > 1):
            continue
        eo, eu = p * oo - 1, (1 - p) * ou - 1
        if max(eo, eu) <= 0:
            continue
        over = eo >= eu
        odds = oo if over else ou
        n += 1
        rets += (odds - 1) if (yy == 1) == over else -1.0
        if cf == cf:
            clv_list.append(odds * (cf if over else 1 - cf) - 1)
    roi = rets / n if n else float("nan")
    clv = float(np.mean(clv_list)) if clv_list else float("nan")
    return n, roi, clv


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fixed THETA0, no tuning")
    ap.add_argument("--maxfev", type=int, default=90)
    a = ap.parse_args()
    rng = np.random.default_rng(89)
    df = load()
    M = market_terms(df)
    seasons = sorted(df.season.unique())
    eval_seasons = [s for s in seasons if s > BURN_IN]
    print(f"{len(df):,} matches, {df.elig.sum():,} eligible, seasons {seasons[0]}..{seasons[-1]}\n")

    S = {}
    for inp in INPUTS:
        S[inp] = np.full(len(df), np.nan)
        th = THETA0
        for s in eval_seasons:
            if not a.quick:
                th = tune(df, inp, s, M["M1"], th, a.maxfev)
            sums = gap_sums(df, inp, th)
            rows = (df.season == s).to_numpy()
            S[inp][rows] = sums[rows]
            print(f"  {inp:14s} {s}  λ={th[0]:.3f} φ1={th[1]:.3f} φ2={th[2]:.3f}", flush=True)

    sc_ok = df.has_sc.to_numpy()
    cells = []
    era = {"M1": (df.season > BURN_IN) & (df.season < PIN_ERA), "M2": df.season >= PIN_ERA,
           "M3": df.season >= PIN_ERA}
    preds = {}
    for inp in INPUTS:
        for mk in ("M1", "M2", "M3"):
            mask = era[mk].to_numpy() & sc_ok            # SAME fixtures for both inputs
            pf, pm = weekly_forecasts(df, S[inp], M[mk], mask)
            k = mask & np.isfinite(pf) & np.isfinite(pm)
            y = df.over.to_numpy()[k]
            d = ll(pm[k], y) - ll(pf[k], y)
            p = boot_p(d, df.week.to_numpy()[k], rng)
            cells.append([inp, mk, int(k.sum()), float(d.mean()), float(ll(pm[k], y).mean()),
                          float(ll(pf[k], y).mean()), p])
            preds[(inp, mk)] = (pf, k)
    hp = holm([c[6] for c in cells])
    print(f"\nSKILL — ΔLL = LL(market-only) − LL(market + GAP rating), nats; block bootstrap by week; Holm m={len(cells)}")
    print(f"  {'input':14s} {'market':6s} {'n':>6s} {'ΔLL':>10s} {'LL mkt':>8s} {'LL full':>8s} {'p':>7s} {'Holm':>7s}")
    for c, h in zip(cells, hp):
        print(f"  {c[0]:14s} {c[1]:6s} {c[2]:6d} {c[3]:+10.5f} {c[4]:8.4f} {c[5]:8.4f} {c[6]:7.3f} {h:7.3f}"
              f"{'  PASS' if (c[3] > 0 and h < 0.05) else ''}")
    d1 = {c[0]: c[3] for c in cells if c[1] == "M1"}
    print(f"\n  FIDELITY (M1: shots+corners ΔLL > goals ΔLL): "
          f"{'REPRODUCED' if d1['shots_corners'] > d1['goals'] else 'NOT REPRODUCED'} "
          f"({d1['shots_corners']:+.5f} vs {d1['goals']:+.5f})")

    print("\nMONEY — level stakes where p̂ beats the price (reported, not a pass bar)")
    y_all = df.over.to_numpy()
    for inp in INPUTS:
        pf, k = preds[(inp, "M1")]
        for lab, lo, hi in (("2006-12", "0607", "1213"), ("2013-19", "1314", "1819")):
            kk = k & (df.season >= lo).to_numpy() & (df.season <= hi).to_numpy()
            for col, name in (("max", "max odds"), ("avg", "avg odds")):
                n, roi, _ = money(pf[kk], df[f"{col}_o"].to_numpy()[kk], df[f"{col}_u"].to_numpy()[kk], y_all[kk])
                print(f"  {inp:14s} M1 {lab}  at {name:9s} n={n:6d} ROI {100*roi:+6.2f}%")
        pf, k = preds[(inp, "M2")]
        for col, name in (("max", "max odds"), ("avg", "avg odds"), ("p", "Pinnacle pre-close")):
            n, roi, clv = money(pf[k], df[f"{col}_o"].to_numpy()[k], df[f"{col}_u"].to_numpy()[k], y_all[k],
                                df.pin_close_fair.to_numpy()[k])
            print(f"  {inp:14s} M2 2019-26 at {name:18s} n={n:6d} ROI {100*roi:+6.2f}%  CLV vs Pinnacle close {100*clv:+6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
