#!/usr/bin/env python3
"""COMBINED O/U MODEL — round O1 ([[#149]]).

Pre-registration: dev/active/market2-model-plan.md §3 (written before this ran).
Per line L in {1.5, 2.5, 3.5}: one binary logit per availability group (P&C / P / C /
none) over log-odds of P(total > L) from
  * rating    — Poisson(dp_lh + dp_la), the walk-forward dynamic-Poisson goal rates
                (workers/model/ratings_1x2.py; research cache features_hist_full.parquet)
  * consensus — mean power-de-vigged log-odds over non-Pinnacle books on that exact line
                (+ log n_books)
  * Pinnacle  — power de-vigged, separate input
Fit 2026-05-01..2026-08-30, score ONCE on 2026-08-31..2026-09-24 at OPEN and CLOSE.

    python3 scripts/ab_ou_combined.py --pull        # legs -> data/models/_research/market2/
    python3 scripts/ab_ou_combined.py --run         # the pre-registered comparison (run once)
Read-only against the database. Epoch seconds only (pandas 3.0.4, RELIABILITY_LEDGER #26).
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
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

CACHE_1X2 = ROOT / "data" / "models" / "_research" / "1x2"
OUT = ROOT / "data" / "models" / "_research" / "market2"
LINES = {"over_under_15": 1.5, "over_under_25": 2.5, "over_under_35": 3.5}
FIT_LO, SPLIT, HI = "2026-05-01", "2026-08-31", "2026-09-25"
OUTLIER_GAP = 0.20
LEG_SPREAD_S = 120
GROUPS = ("P&C", "P", "C", "none")
MIN_ROWS = 300
from workers.model.market_consensus_1x2 import CONSENSUS_BOOKS  # noqa: E402


def ep(s: str) -> float:
    return pd.Timestamp(s, tz="UTC").timestamp()


# ── de-vig ────────────────────────────────────────────────────────────────────
def power_devig(o1: np.ndarray, o2: np.ndarray) -> np.ndarray:
    """2-way power method: find k with (1/o1)^k + (1/o2)^k = 1; returns p1."""
    a, b = 1 / np.asarray(o1, float), 1 / np.asarray(o2, float)
    lo, hi = np.full(a.shape, 0.2), np.full(a.shape, 5.0)
    for _ in range(60):
        k = (lo + hi) / 2
        f = a ** k + b ** k - 1
        hi = np.where(f < 0, k, hi)
        lo = np.where(f >= 0, k, lo)
    k = (lo + hi) / 2
    return a ** k


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


# ── data ──────────────────────────────────────────────────────────────────────
def pull() -> None:
    import psycopg2
    OUT.mkdir(parents=True, exist_ok=True)
    f = pd.read_parquet(CACHE_1X2 / "features_hist_full.parquet",
                        columns=["match_id", "kickoff"])
    f["ks"] = (pd.to_datetime(f.kickoff, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    ids = f[(f.ks >= ep(FIT_LO)) & (f.ks < ep(HI))].match_id.astype(str).tolist()
    books = list(CONSENSUS_BOOKS) + ["Pinnacle"]
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    for which, order, extra in (("close", 'o."timestamp" DESC', ""), ("open", 'o."timestamp" ASC', "")):
        parts = []
        for i in range(0, len(ids), 1500):
            q = f"""
                SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
                       o.match_id::text, o.market, o.bookmaker, o.selection, o.odds::float8,
                       extract(epoch FROM o."timestamp")::float8
                  FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                 WHERE o.match_id = ANY(%(ids)s::uuid[]) AND o.market = ANY(%(mk)s)
                   AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date
                   AND o.bookmaker = ANY(%(books)s) {extra}
                 ORDER BY o.match_id, o.market, o.bookmaker, o.selection, {order}"""
            with conn.cursor() as cur:
                cur.execute(q, {"ids": ids[i:i + 1500], "mk": list(LINES), "books": books})
                parts += cur.fetchall()
            print(f"  {which}: {min(i + 1500, len(ids)):,}/{len(ids):,}", flush=True)
        df = pd.DataFrame(parts, columns=["match_id", "market", "bookmaker", "selection", "odds", "ts"])
        df.to_parquet(OUT / f"ou_legs_{which}.parquet", index=False)
        print(f"{which}: {len(df):,} legs")


def consensus(legs: pd.DataFrame) -> pd.DataFrame:
    """Per (match, market): c_over (consensus), n_books, pin_over — power de-vigged."""
    w = legs.pivot_table(index=["match_id", "market", "bookmaker"], columns="selection",
                         values="odds", aggfunc="first")
    t = legs.groupby(["match_id", "market", "bookmaker"]).ts.agg(["min", "max"])
    w = w.join(t).dropna(subset=["over", "under"])
    w = w[(w["max"] - w["min"]) <= LEG_SPREAD_S]
    ovr = 1 / w.over + 1 / w.under
    w = w[(ovr > 0.98) & (ovr < 1.30)].copy()
    w["p"] = power_devig(w.over.to_numpy(), w.under.to_numpy())
    w = w.reset_index()
    pin = w[w.bookmaker == "Pinnacle"].set_index(["match_id", "market"]).p.rename("pin_over")
    b = w[w.bookmaker.isin(CONSENSUS_BOOKS)].copy()
    med = b.groupby(["match_id", "market"]).p.transform("median")
    cnt = b.groupby(["match_id", "market"]).p.transform("count")
    b = b[~((cnt >= 3) & ((b.p - med).abs() > OUTLIER_GAP))]
    b["l"] = logit(b.p)
    g = b.groupby(["match_id", "market"]).agg(cl=("l", "mean"), n_books=("p", "size"))
    g["c_over"] = 1 / (1 + np.exp(-g.cl))
    return g[["c_over", "n_books"]].join(pin, how="outer").reset_index()


def rating_over(lam: np.ndarray, line: float) -> np.ndarray:
    from scipy.stats import poisson
    return 1 - poisson.cdf(np.floor(line), np.clip(lam, 0.05, 12))


def frame(which: str) -> pd.DataFrame:
    f = pd.read_parquet(CACHE_1X2 / "features_hist_full.parquet",
                        columns=["match_id", "kickoff", "gh", "ga", "dp_lh", "dp_la", "n_home", "n_away"])
    f["ks"] = (pd.to_datetime(f.kickoff, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    f = f.drop(columns="kickoff")
    f = f[(f.ks >= ep(FIT_LO)) & (f.ks < ep(HI)) & f.gh.notna() & f.ga.notna()].copy()
    f["match_id"] = f.match_id.astype(str)
    c = consensus(pd.read_parquet(OUT / f"ou_legs_{which}.parquet"))
    rows = []
    for mk, L in LINES.items():
        d = f.merge(c[c.market == mk], on="match_id", how="left")
        d["market"], d["line"] = mk, L
        d["r_over"] = rating_over((d.dp_lh + d.dp_la).to_numpy(), L)
        d["y"] = ((d.gh + d.ga) > L).astype(int)
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


# ── combiner (production copy lives in workers/model/combined_ou.py once adopted) ──
def groups(d):
    hp, hc = d.pin_over.notna().to_numpy(), d.c_over.notna().to_numpy()
    return np.where(hp & hc, "P&C", np.where(hp, "P", np.where(hc, "C", "none")))


def design(d, g):
    cols = [logit(d.r_over)[:, None]]
    if g in ("P&C", "C"):
        cols += [logit(d.c_over)[:, None], np.log(d.n_books.to_numpy(float))[:, None]]
    if g in ("P&C", "P"):
        cols.append(logit(d.pin_over)[:, None])
    return np.hstack(cols)


def fit(d):
    from sklearn.linear_model import LogisticRegression
    g = groups(d); prm = {}
    for G in GROUPS:
        a = d[g == G]
        if len(a) >= MIN_ROWS:
            m = LogisticRegression(max_iter=3000, C=1.0).fit(design(a, G), a.y)
            prm[G] = {"coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]), "n": int(len(a))}
    return prm


def predict(d, prm):
    g = groups(d)
    P = d.r_over.to_numpy(float).copy()
    P[d.c_over.notna().to_numpy()] = d.c_over[d.c_over.notna()].to_numpy()
    P[d.pin_over.notna().to_numpy()] = d.pin_over[d.pin_over.notna()].to_numpy()
    for G, p in prm.items():
        mk = g == G
        if mk.any():
            z = design(d[mk], G) @ np.asarray(p["coef"]) + p["intercept"]
            P[mk] = 1 / (1 + np.exp(-z))
    return P, g


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def boot_p(diff, n=5000, seed=7):
    """One-sided p that mean(diff) <= 0 (diff = comparator_ll - combined_ll; > 0 = combined better)."""
    rng = np.random.default_rng(seed)
    m = np.array([diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)])
    return float((m <= 0).mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def holm(ps):
    order = sorted(ps, key=ps.get); adj, run = {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (len(ps) - i) * ps[k])); adj[k] = run
    return adj


def run() -> None:
    res = {}
    for which in ("open", "close"):
        d = frame(which)
        tr = d[d.ks < ep(SPLIT)]
        te = d[d.ks >= ep(SPLIT)].copy()
        out = {}
        pvals = {}
        preds = []
        for mk, L in LINES.items():
            a, b = tr[tr.market == mk], te[te.market == mk].copy()
            prm = fit(a)
            P, g = predict(b, prm)
            b["p_comb"], b["group"] = P, g
            y = b.y.to_numpy()
            lc = ll(P, y)
            comps = {"rating": ll(b.r_over.to_numpy(), y)}
            cm = b.c_over.notna().to_numpy(); pm = b.pin_over.notna().to_numpy()
            rule = b.r_over.to_numpy(float).copy(); rule[cm] = b.c_over[cm]; rule[pm] = b.pin_over[pm]
            comps["rule"] = ll(rule, y)
            row = {"n": int(len(b)), "comb": float(lc.mean()), "groups": {G: int((g == G).sum()) for G in GROUPS},
                   "params": prm}
            for k, v in comps.items():
                row[k] = float(v.mean())
            # on Pinnacle rows: vs Pinnacle; on consensus rows: vs consensus
            row["pin_rows"] = {"n": int(pm.sum()), "comb": float(lc[pm].mean()),
                               "pinnacle": float(ll(b.pin_over[pm].to_numpy(), y[pm]).mean())}
            row["cons_rows"] = {"n": int(cm.sum()), "comb": float(lc[cm].mean()),
                                "consensus": float(ll(b.c_over[cm].to_numpy(), y[cm]).mean())}
            # pre-registered test: vs the best single comparator on the same rows = the rule
            p1, lo_, hi_ = boot_p(comps["rule"] - lc)
            row["vs_rule"] = {"delta": float((lc - comps["rule"]).mean()), "p": p1, "ci_gain": [lo_, hi_]}
            pvals[mk] = p1
            out[mk] = row
            preds.append(b[["match_id", "market", "line", "ks", "y", "p_comb", "group", "r_over",
                            "c_over", "n_books", "pin_over"]])
        adj = holm(pvals)
        for mk in out:
            out[mk]["vs_rule"]["holm"] = adj[mk]
            out[mk]["PASS"] = bool(out[mk]["vs_rule"]["delta"] < 0 and adj[mk] < 0.05)
        res[which] = out
        pd.concat(preds).to_parquet(OUT / f"ou_comb_test_{which}.parquet", index=False)
        print(f"\n== {which.upper()} (fit {FIT_LO}..{SPLIT}, score {SPLIT}..)")
        for mk, r in out.items():
            print(f"  {mk}: n={r['n']:,}  comb {r['comb']:.4f}  rule {r['rule']:.4f}  rating {r['rating']:.4f}  "
                  f"| pin rows {r['pin_rows']['n']:,}: comb {r['pin_rows']['comb']:.4f} vs pin {r['pin_rows']['pinnacle']:.4f}"
                  f" | cons rows {r['cons_rows']['n']:,}: comb {r['cons_rows']['comb']:.4f} vs cons {r['cons_rows']['consensus']:.4f}"
                  f" | Δ vs rule {r['vs_rule']['delta']:+.4f} Holm {r['vs_rule']['holm']:.3f} {'PASS' if r['PASS'] else 'FAIL'}"
                  f" | groups {r['groups']}")
    (OUT / "round_o1_results.json").write_text(json.dumps(res, indent=1, default=float))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.pull:
        pull()
    if a.run:
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
