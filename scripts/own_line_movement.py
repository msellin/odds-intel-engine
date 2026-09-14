#!/usr/bin/env python3
"""OWN-path line-movement study: is a soft book's price ever stale in our favour?

THE QUESTION. Every own-path test so far compared a SNAPSHOT of a sharp line
against a SNAPSHOT of a book we can bet. Nobody tested the DELTA. If Coolbet /
Epicbet / Unibet-Site lag the market, there is a window where their price is
stale and generous, and betting into it is free closing-line value.

THE BAR. One definition of profitable, and it is strict:

    margin-corrected own-book CLV  =  (1 + clv) / (1 + m) - 1  >  0

  clv = quote_taken / own_book_closing_quote - 1   (RAW price ratio, no de-vig)
  m   = that book's own closing overround on that fixture+market, per row,
        exactly as `settlement.closing_book_margin()` computes it.

Betting at the close itself gives clv = 0 and therefore mc = -m/(1+m) ~= -7%.
So the bar is not "beat the close" — it is "beat the close by more than the
book's own vig", i.e. clv > m ~= 7-9pp.

WHY IT IS RUN ON EIGHT DAYS OF DATA. `prune_old_simple` keeps three rows per
price series after 7 days (ANALYSIS_GOTCHAS 59a). Measured 2026-09-14,
rows-per-series at our bettable books is 5-45 inside the window and 1.0 outside
it. The intra-day price path — the whole subject here — exists for ~7 days.
`scripts/own_movement_snapshot.py` freezes it so this is reproducible.

METHOD NOTES, each one a trap that has cost money:
  * Never join books on timestamp equality (63). Own-book quotes are compared
    against a consensus assembled into 10-minute buckets, and every event
    records its own alignment gap, which is reported per cell.
  * `is_live=false` is not a pre-kickoff filter (37). Bound on timestamp < kickoff.
  * Phantom/aggregate feeds excluded ('Unibet' AF, 'Unibet-Kambi', Max, Avg,
    'Betfair Exchange', BetWin, Betfred).
  * Every cell prints n, FIXTURE count, date span, median alignment gap, and a
    CI from SEs clustered on fixture — a fixture contributes many correlated
    quotes and unclustered SEs are ~3x too tight here.
  * A gate-matched placebo runs beside every real cell: the signal column is
    permuted within (book, market, selection), which preserves its marginal
    distribution exactly, so the placebo passes the same gate at the same rate.

Usage:
    python3 scripts/own_line_movement.py --parquet data/own_movement_2026_09_14.parquet
    python3 scripts/own_line_movement.py --parquet ... --mode cadence
"""
from __future__ import annotations

import argparse
import collections
import sys

import numpy as np
import pandas as pd

OWN = ["Coolbet", "Epicbet", "Unibet-Site"]
# Candidate consensus members. Phantom / aggregate feeds are NOT here.
AF = ["Pinnacle", "Bet365", "1xBet", "Marathonbet", "Betfair",
      "William Hill", "Betano", "BetVictor", "SBO"]
COMP = {"1x2": ["home", "draw", "away"],
        "over_under_25": ["over", "under"],
        "over_under_35": ["over", "under"],
        "btts": ["yes", "no"]}
EPOCH = pd.Timestamp("2020-01-01", tz="UTC")
MAX_ANCHOR_AGE = 40.0    # minutes; a consensus point older than this is not used
MIN_CONSENSUS_BOOKS = 4


# ---------------------------------------------------------------- loading

def load(parquet: str, markets) -> pd.DataFrame:
    df = pd.read_parquet(parquet)
    df = df[df.market.isin(markets)].copy()
    df["ttk"] = (df.kickoff - df.timestamp).dt.total_seconds() / 60.0
    df = df[df.ttk > 0]                       # real pre-kickoff bound, gotcha 37
    # Resolution-independent epoch minutes. NOTE: `.astype("int64")` on a
    # datetime64[us] column yields MICROseconds, not nanoseconds — dividing by
    # 60e9 silently makes every window 1000x too long and every freshness filter
    # a no-op. Cost: one whole pass of this study returned zero events.
    df["tmin"] = (df.timestamp - EPOCH).dt.total_seconds() / 60.0
    return df.sort_values("tmin")


def build(df):
    """series[(mid,mkt,sel)][book] -> (t[], odds[]); plus own-book close & margin."""
    series = collections.defaultdict(dict)
    close, margin = {}, {}
    for (mid, mkt, sel, bk), g in df.groupby(
            ["match_id", "market", "selection", "bookmaker"], sort=False):
        series[(mid, mkt, sel)][bk] = (g.tmin.to_numpy(), g.odds.to_numpy())
        close[(mid, mkt, bk, sel)] = g.odds.iloc[-1]   # latest pre-KO = the close
    for (mid, mkt, bk), _ in df.groupby(["match_id", "market", "bookmaker"],
                                        sort=False):
        try:
            inv = sum(1.0 / close[(mid, mkt, bk, s)] for s in COMP[mkt])
        except KeyError:
            continue                       # incomplete complement -> leave NULL
        m = inv - 1.0
        margin[(mid, mkt, bk)] = m if 0.0 <= m <= 0.5 else np.nan
    return series, close, margin


def consensus(df):
    """Market consensus per (mid, sel): median log-odds over >=4 AF books in a
    10-min bucket. A single book is not a market; the AF feed writes in :00/:30
    batches so buckets are naturally dense."""
    af = df[df.bookmaker.isin(AF)].copy()
    af["bkt"] = (af.tmin // 10).astype(int)
    c = (af.groupby(["match_id", "market", "selection", "bkt"])
           .agg(lo=("odds", lambda s: float(np.median(np.log(s)))),
                nb=("bookmaker", "nunique"), ovr=("odds", "size"))
           .reset_index())
    c = c[c.nb >= MIN_CONSENSUS_BOOKS]
    out = {}
    for (mid, mkt, sel), g in c.groupby(["match_id", "market", "selection"],
                                        sort=False):
        out[(mid, mkt, sel)] = (g.bkt.to_numpy() * 10.0 + 5.0, g.lo.to_numpy())
    return out


def _at(ts, vs, t, max_age):
    i = np.searchsorted(ts, t, side="right") - 1
    if i < 0:
        return np.nan, np.nan
    age = t - ts[i]
    return (vs[i], age) if age <= max_age else (np.nan, np.nan)


# ---------------------------------------------------------------- event table

def events(df, windows=(60, 120, 240)) -> pd.DataFrame:
    series, close, margin = build(df)
    C = consensus(df)
    ko = df.groupby("match_id").kickoff.first()
    rows = []
    for (mid, mkt, sel), books in series.items():
        c = C.get((mid, mkt, sel))
        if c is None:
            continue
        ct, cv = c
        kod = ko[mid]
        for ob in OWN:
            if ob not in books:
                continue
            ot, oo = books[ob]
            m = margin.get((mid, mkt, ob), np.nan)
            clo = close.get((mid, mkt, ob, sel), np.nan)
            if not (clo == clo):
                continue
            lo = np.log(oo)
            for t, p in zip(ot, oo):
                cn, age = _at(ct, cv, t, MAX_ANCHOR_AGE)
                if not (cn == cn):
                    continue
                r = {"mid": mid, "book": ob, "market": mkt, "sel": sel,
                     "kickoff": kod, "p": p, "close": clo, "m": m,
                     "ttk": (kod - EPOCH).total_seconds() / 60.0 - t,
                     "align_gap": age, "gap": np.log(p) - cn}
                keep = False
                for W in windows:
                    cp, _ = _at(ct, cv, t - W, MAX_ANCHOR_AGE)
                    r[f"ra{W}"] = cn - cp if cp == cp else np.nan
                    op, _ = _at(ot, lo, t - W, 1e9)
                    r[f"ro{W}"] = np.log(p) - op if op == op else np.nan
                    keep |= r[f"ra{W}"] == r[f"ra{W}"]
                if keep:
                    rows.append(r)
    E = pd.DataFrame(rows)
    E["clv"] = E.p / E.close - 1.0
    E["mc"] = (1.0 + E.clv) / (1.0 + E.m) - 1.0     # margin-corrected own-book CLV
    return E


# ---------------------------------------------------------------- reporting

def cell(d: pd.DataFrame, name: str):
    """mc-CLV of a selection of legs, with SEs CLUSTERED ON FIXTURE.

    A fixture contributes many highly-correlated quotes; treating them as
    independent understates the SE roughly 3x here and manufactures significance.
    """
    d = d.dropna(subset=["mc"])
    if len(d) < 30:
        return None
    g = d.groupby("mid").mc.mean()
    mu, se = d.mc.mean(), g.std() / np.sqrt(len(g))
    return dict(cell=name, n=len(d), fx=len(g), mc=mu * 100,
                lo=(mu - 1.96 * se) * 100, hi=(mu + 1.96 * se) * 100,
                gap=d.align_gap.median(),
                span=f"{d.kickoff.min().date()}..{d.kickoff.max().date()}")


def gates(E: pd.DataFrame, signal_prefix="ra"):
    """Every configuration tested, as (name, boolean mask) pairs."""
    out = [("ALL (no gate, baseline)", pd.Series(True, index=E.index))]
    for W in (60, 120, 240):
        col = f"{signal_prefix}{W}"
        if col not in E:
            continue
        for thr in (-0.02, -0.05, -0.10):
            out.append((f"consensus W={W}m move <= {thr:+.0%}", E[col] <= thr))
        out.append((f"consensus W={W}m move >= +5%", E[col] >= 0.05))
        # the actual "stale price" condition: market moved, our book has not
        if f"ro{W}" in E:
            out.append((f"consensus W={W}m <= -3% AND own book unmoved",
                        (E[col] <= -0.03) & (E[f"ro{W}"].abs() <= 0.005)))
    for thr in (0.03, 0.05, 0.10):
        out.append((f"own price >= consensus +{thr:.0%}", E.gap >= thr))
    out.append(("within 60 min of kickoff", E.ttk <= 60))
    out.append(("consensus W=120m <= -5% AND within 6h of KO",
                (E.get("ra120", pd.Series(np.nan, index=E.index)) <= -0.05) & (E.ttk <= 360)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="data/own_movement_2026_09_14.parquet")
    ap.add_argument("--markets", default="1x2,over_under_25,over_under_35,btts")
    ap.add_argument("--mode", default="cells",
                    choices=["cells", "cadence", "leadlag"])
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()
    mkts = a.markets.split(",")

    df = load(a.parquet, mkts)
    print(f"loaded {len(df):,} pre-kickoff quotes, "
          f"{df.match_id.nunique():,} fixtures, "
          f"{df.kickoff.min().date()} .. {df.kickoff.max().date()}\n")

    if a.mode == "cadence":
        d = df.copy()
        d["day"] = d.timestamp.dt.floor("D")
        d["minute"] = d.timestamp.dt.floor("min")
        wm = d.groupby(["bookmaker", "day"]).minute.nunique().groupby("bookmaker").median()
        st = (d.groupby(["bookmaker", "match_id", "market", "selection"])
                .agg(n=("odds", "size"), uniq=("odds", "nunique"),
                     span=("timestamp", lambda s: (s.max() - s.min()).total_seconds() / 3600))
                .groupby("bookmaker")
                .agg(series=("n", "size"), med_obs=("n", "median"),
                     med_distinct_prices=("uniq", "median"),
                     med_span_h=("span", "median")))
        st["write_min_per_day"] = wm
        print(st.sort_values("med_obs", ascending=False).round(2).to_string())
        return 0

    E = events(df)
    print(f"events (own-book quotes with a fresh consensus): {len(E):,}   "
          f"median alignment gap {E.align_gap.median():.1f} min\n")

    if a.mode == "leadlag":
        for ob in OWN:
            s = E[E.book == ob]
            for W in (60, 120, 240):
                d = s[[f"ra{W}", "p", "close"]].dropna()
                if len(d) < 300:
                    continue
                y = np.log(d.close / d.p)
                x = d[f"ra{W}"].to_numpy()
                b = np.polyfit(x, y, 1)[0]
                print(f"  {ob:12s} W={W:>3}m n={len(d):>7,} "
                      f"follow-through beta={b:+.3f} r={np.corrcoef(x, y)[0, 1]:+.3f}")
            d = s[["gap", "p", "close"]].dropna()
            b = np.polyfit(d.gap, np.log(d.close / d.p), 1)[0]
            print(f"  {ob:12s} gap mean-reversion beta={b:+.3f} "
                  f"(-1 = full convergence to consensus)\n")
        return 0

    rng = np.random.default_rng(a.seed)
    rows, placebo = [], []
    for mkt in mkts:
        Em = E[E.market == mkt]
        if len(Em) < 200:
            continue
        for ob in OWN:
            s = Em[Em.book == ob].copy()
            if len(s) < 200:
                continue
            # GATE-MATCHED PLACEBO: permute the signals within this book+market,
            # so the junk arm passes the same gate at the same rate on the same
            # marginal distribution. An unmatched control compares a tail
            # selection against a near-flat back and proves nothing.
            j = s.copy()
            for c in [c for c in s.columns if c.startswith(("ra", "ro"))] + ["gap"]:
                j[c] = rng.permutation(s[c].to_numpy())
            for name, mask in gates(s):
                r = cell(s[mask], f"{mkt} | {ob} | {name}")
                if r:
                    rows.append(r)
            for name, mask in gates(j):
                r = cell(j[mask], f"{mkt} | {ob} | {name}")
                if r:
                    placebo.append(r)

    R = pd.DataFrame(rows).sort_values("mc", ascending=False)
    P = pd.DataFrame(placebo).set_index("cell")
    R["placebo_mc"] = R.cell.map(P.mc)
    print(f"{'cell':64s} {'n':>7} {'fx':>5} {'gap':>5} {'mc%':>7} "
          f"{'CI lo':>7} {'CI hi':>7} {'junk%':>7}  span")
    for _, r in R.iterrows():
        print(f"{r.cell:64s} {r.n:>7,} {r.fx:>5} {r.gap:>5.0f} {r.mc:>7.2f} "
              f"{r.lo:>7.2f} {r.hi:>7.2f} {r.placebo_mc:>7.2f}  {r.span}")

    print(f"\ncells tested: {len(R)} real + {len(P)} placebo")
    paired = R.dropna(subset=["placebo_mc"])
    if len(paired):
        adv = paired.mc - paired.placebo_mc
        print(f"real vs its OWN gate-matched placebo: mean advantage "
              f"{adv.mean():+.2f}pp, median {adv.median():+.2f}pp; the real signal "
              f"beat its placebo in {(adv > 0).sum()}/{len(paired)} paired cells "
              f"({(adv > 0).mean():.1%} — 50% is a coin flip and means the gate "
              f"carries no information)")
    pos = R[R.lo > 0]
    print(f"cells with margin-corrected own-book CLV CI strictly above 0: "
          f"{len(pos)}  <-- the only thing that would justify BUILD")
    if len(pos):
        print(pos.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
