#!/usr/bin/env python3
"""[[#154]] idea 1 + W8.6 — how much do the de-vig method and the double-counted Unibet move the
NEW+ 1X2 INPUTS? A measurement on the inputs, not a model round.

WHY THIS IS NOT A HOLDOUT SPEND. The consensus and the de-vigged Pinnacle have NO fitted
parameters (fixed outlier rule, equal log-odds mean), so scoring them directly on an old window
selects nothing about the NEW+ combiner. The window is 2026-07-15 .. 2026-08-30 (kickoff): after
mid-July (real closes, ANALYSIS_GOTCHAS §83) and BEFORE the 08-31..09-24 window rounds 1–3c used.
The 1X2 home/away swap (#065, 05-10..09-14) was in the SERVED model's XGB leg, not in odds, so
odds-only inputs are unaffected.

EXPECTED (stated before running, 2026-09-26): Shin / power beat proportional on log-loss by a
SMALL margin — +0.0005 … +0.002 — mainly through the draw and the longshot (the
favourite-longshot bias proportional ignores; Štrumbelj 2014, Clarke et al. 2017). Dropping the
AF 'Unibet' feed where 'Unibet-Site' is present changes the consensus by ~0 (same book, one vote
in ~8), so ≈ 0 on log-loss. If the de-vig gain on the INPUT is < 0.0005, a combiner round on it
is not worth a forward window (the combiner can already rescale a uniform bias).

Arms (CLOSE prices, same matches for every arm — rows where ALL arms exist):
  cons_prop   consensus as production (proportional per book)          ← current NEW+ input
  cons_power  consensus, power de-vig per book
  cons_shin   consensus, Shin de-vig per book
  cons_power_1u  cons_power with the AF 'Unibet' feed dropped when 'Unibet-Site' is present
  pin_prop / pin_power / pin_shin   Pinnacle alone, three de-vigs
Metric: mean 3-way log-loss; paired bootstrap over matches (5,000, seed 154) for each arm minus
its proportional baseline.

    python3 scripts/analysis/devig_consensus_1x2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.model import market_consensus_1x2 as mc  # noqa: E402
from workers.model.devig import power_devig, shin_devig  # noqa: E402

START, END = "2026-07-15", "2026-08-31"
SEED, B = 154, 5000


def _devig_rows(t: pd.DataFrame, method: str) -> pd.DataFrame:
    """Re-de-vig each book triple from its RAW odds (columns home/draw/away)."""
    f = {"power": power_devig, "shin": shin_devig}[method]
    out = t.copy()
    ps = [f([h, d, a]) for h, d, a in zip(t.home, t.draw, t.away)]
    ok = [p is not None for p in ps]
    out = out[ok].copy()
    arr = np.array([p for p in ps if p is not None])
    out["ph"], out["pd"], out["pa"] = arr[:, 0], arr[:, 1], arr[:, 2]
    return out


def _book_triples(legs: pd.DataFrame) -> pd.DataFrame:
    """mc._triples' filters, keeping the raw odds so any de-vig can be applied."""
    w = legs.pivot_table(index=["match_id", "bookmaker"], columns="selection", values="odds", aggfunc="first")
    t = legs.groupby(["match_id", "bookmaker"]).ts.agg(["min", "max"])
    w = w.join(t).dropna(subset=["home", "draw", "away"])
    w = w[(w["max"] - w["min"]) <= mc.LEG_SPREAD_S]
    over = (1 / w[["home", "draw", "away"]]).sum(1)
    w = w[(over > 0.98) & (over < 1.40)].reset_index()
    inv = 1 / w[["home", "draw", "away"]].to_numpy()
    p = inv / inv.sum(1, keepdims=True)
    w["ph"], w["pd"], w["pa"] = p[:, 0], p[:, 1], p[:, 2]
    return w


def _consensus(b: pd.DataFrame) -> pd.DataFrame:
    """mc.consensus' aggregation (outlier rule + log-odds mean vs draw), on given per-book probs."""
    b = b[b.bookmaker.isin(mc.CONSENSUS_BOOKS)].copy()
    med = b.groupby("match_id").ph.transform("median")
    cnt = b.groupby("match_id").ph.transform("count")
    b = b[~((cnt >= 3) & ((b.ph - med).abs() > mc.OUTLIER_GAP))]
    b["lh"], b["la"] = np.log(b.ph / b.pd), np.log(b.pa / b.pd)
    g = b.groupby("match_id").agg(lh=("lh", "mean"), la=("la", "mean"), n=("ph", "size"))
    e = np.exp(np.stack([g.lh.to_numpy(), np.zeros(len(g)), g.la.to_numpy()], 1))
    e = e / e.sum(1, keepdims=True)
    return pd.DataFrame({"h": e[:, 0], "d": e[:, 1], "a": e[:, 2], "n": g.n.to_numpy()}, index=g.index)


def main() -> None:
    from workers.api_clients.db import get_conn
    from workers.api_clients.db import execute_query
    res = execute_query(
        """SELECT id::text AS match_id, score_home, score_away FROM matches
            WHERE status = 'finished' AND score_home IS NOT NULL AND date >= %s AND date < %s""",
        (START, END))
    y = {r["match_id"]: (0 if r["score_home"] > r["score_away"] else 1 if r["score_home"] == r["score_away"] else 2)
         for r in res}
    ids = list(y)
    frames = []
    with get_conn() as conn:
        for i in range(0, len(ids), 2000):
            frames.append(mc.fetch_legs(conn, ids[i:i + 2000], "close"))
    legs = pd.concat(frames, ignore_index=True)
    t = _book_triples(legs)
    arms = {}
    tp, tw, ts = t, _devig_rows(t, "power"), _devig_rows(t, "shin")
    arms["cons_prop"], arms["cons_power"], arms["cons_shin"] = _consensus(tp), _consensus(tw), _consensus(ts)
    both = set(tw[tw.bookmaker == "Unibet-Site"].match_id)
    tw1 = tw[~((tw.bookmaker == "Unibet") & tw.match_id.isin(both))]
    arms["cons_power_1u"] = _consensus(tw1)
    for name, src in (("pin_prop", tp), ("pin_power", tw), ("pin_shin", ts)):
        p = src[src.bookmaker == "Pinnacle"].set_index("match_id")[["ph", "pd", "pa"]]
        p.columns = ["h", "d", "a"]
        arms[name] = p

    def ll(df, keys):
        yy = np.array([y[k] for k in keys])
        P = df.loc[keys, ["h", "d", "a"]].to_numpy()
        return -np.log(np.clip(P[np.arange(len(keys)), yy], 1e-12, 1))

    rng = np.random.default_rng(SEED)
    for group, base, others in (("consensus", "cons_prop", ("cons_power", "cons_shin", "cons_power_1u")),
                                ("pinnacle", "pin_prop", ("pin_power", "pin_shin"))):
        keys = sorted(set.intersection(*[set(arms[a].index) for a in (base, *others)]) & set(y))
        lb = ll(arms[base], keys)
        print(f"\n{group}: n={len(keys)} matches  {base} log-loss {lb.mean():.5f}")
        for a in others:
            la = ll(arms[a], keys)
            d = lb - la                      # > 0 = the arm is better
            idx = rng.integers(0, len(d), size=(B, len(d)))
            bs = d[idx].mean(1)
            print(f"  {a:14} {la.mean():.5f}  gain {d.mean():+.5f}  95% [{np.quantile(bs, .025):+.5f}, "
                  f"{np.quantile(bs, .975):+.5f}]  P(gain<=0) {(bs <= 0).mean():.3f}")
        if group == "consensus":
            diff = (arms["cons_power_1u"].loc[keys, ["h", "d", "a"]] - arms["cons_power"].loc[keys, ["h", "d", "a"]]).abs()
            print(f"  Unibet collapse: |Δp| mean {diff.to_numpy().mean():.5f}, max {diff.to_numpy().max():.4f}, "
                  f"matches with both feeds {len(both & set(keys))}")


if __name__ == "__main__":
    main()
