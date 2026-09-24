"""1X2 MARKET CONSENSUS ([[#141]] round 3b) — de-vigged multi-book 1X2 probabilities.

Why: the 2026-09-24 data audit found the equal-weight de-vigged consensus of our
non-Pinnacle books is as accurate as Pinnacle (log-loss 0.9781 vs 0.9783 on 18,877
shared matches) and, on matches Pinnacle does not price, beats the walk-forward
rating model by ~0.07 log-loss. The literature agrees (Robberechts & Davis; the
2023 Soccer Prediction Challenge: nothing beat the bookmaker consensus).

What it does, per match:
  * one triple per book: its latest pre-kickoff price (CLOSE) or its opening price
    (OPEN); the three legs must be written within 120 s of each other;
  * proportional de-vig per book;
  * drop a book whose home probability is > 0.25 from the median of the match's
    books (>= 3 books) — catches LARGE transposed / wrong-fixture rows (~0.1%); a
    near-even swap (e.g. 2.00/4.00) moves home prob only ~0.22 and passes — the
    write-time mirror guard (#006) is the defence for those;
  * consensus = mean of the books' log-odds (vs draw), mapped back to probabilities;
  * Pinnacle is returned SEPARATELY, never inside the consensus.

Everything is epoch seconds / plain numbers — no tz-aware datetime columns
(pandas 3.0.4 segfaults on them; RELIABILITY_LEDGER #26).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Live books first; the second group no longer quotes but carries history.
CONSENSUS_BOOKS = (
    "1xBet", "Marathonbet", "Bet365", "William Hill", "Betano", "Betfair", "BetVictor",
    "Coolbet", "Epicbet", "Unibet-Site", "Tonybet",
    "Dafabet", "10Bet", "Unibet", "Superbet", "888Sport", "BetWin", "Betfred",
)
# Excluded on purpose: Pinnacle (separate input), SBO (worst accuracy, 15% margin),
# Unibet-Kambi (retired, prices off-site), Avg/Max (synthetic CSV aggregates),
# Betfair Exchange (an exchange, not a book), and junk names.
OUTLIER_GAP = 0.25
LEG_SPREAD_S = 120


def fetch_legs(conn, match_ids: list[str], which: str = "close") -> pd.DataFrame:
    """Latest pre-kickoff (close) or opening (open) 1X2 leg per (match, book, selection)."""
    books = list(CONSENSUS_BOOKS) + ["Pinnacle"]
    if which == "close":
        order, extra = 'o."timestamp" DESC', ""
    elif which == "open":
        order, extra = 'o."timestamp" ASC', "AND o.is_opening"
    else:
        raise ValueError(which)
    q = f"""
        SELECT DISTINCT ON (o.match_id, o.bookmaker, o.selection)
               o.match_id::text match_id, o.bookmaker, o.selection, o.odds::float8 odds,
               extract(epoch FROM o."timestamp")::float8 ts
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%(ids)s::uuid[]) AND o.market = '1x2'
           AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date
           AND o.bookmaker = ANY(%(books)s) {extra}
         ORDER BY o.match_id, o.bookmaker, o.selection, {order}"""
    with conn.cursor() as cur:
        cur.execute(q, {"ids": match_ids, "books": books})
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["match_id", "bookmaker", "selection", "odds", "ts"])


def _triples(legs: pd.DataFrame) -> pd.DataFrame:
    w = legs.pivot_table(index=["match_id", "bookmaker"], columns="selection", values="odds", aggfunc="first")
    t = legs.groupby(["match_id", "bookmaker"]).ts.agg(["min", "max"])
    w = w.join(t).dropna(subset=["home", "draw", "away"])
    w = w[(w["max"] - w["min"]) <= LEG_SPREAD_S]
    inv = 1 / w[["home", "draw", "away"]].to_numpy()
    over = inv.sum(1)
    w = w[(over > 0.98) & (over < 1.40)]
    inv = 1 / w[["home", "draw", "away"]].to_numpy()
    p = inv / inv.sum(1, keepdims=True)
    out = w.reset_index()[["match_id", "bookmaker"]]
    out["ph"], out["pd"], out["pa"] = p[:, 0], p[:, 1], p[:, 2]
    return out


def consensus(legs: pd.DataFrame) -> pd.DataFrame:
    """Per match: c_h/c_d/c_a + n_books (consensus books) and pin_h/pin_d/pin_a."""
    if legs.empty:
        return pd.DataFrame(columns=["c_h", "c_d", "c_a", "n_books", "pin_h", "pin_d", "pin_a"])
    t = _triples(legs)
    pin = t[t.bookmaker == "Pinnacle"].set_index("match_id")[["ph", "pd", "pa"]]
    pin.columns = ["pin_h", "pin_d", "pin_a"]
    b = t[t.bookmaker.isin(CONSENSUS_BOOKS)].copy()
    med = b.groupby("match_id").ph.transform("median")
    cnt = b.groupby("match_id").ph.transform("count")
    b = b[~((cnt >= 3) & ((b.ph - med).abs() > OUTLIER_GAP))]
    b["lh"] = np.log(b.ph / b.pd)
    b["la"] = np.log(b.pa / b.pd)
    g = b.groupby("match_id").agg(lh=("lh", "mean"), la=("la", "mean"), n_books=("ph", "size"))
    e = np.exp(np.stack([g.lh.to_numpy(), np.zeros(len(g)), g.la.to_numpy()], 1))
    e = e / e.sum(1, keepdims=True)
    c = pd.DataFrame({"c_h": e[:, 0], "c_d": e[:, 1], "c_a": e[:, 2], "n_books": g.n_books.to_numpy()},
                     index=g.index)
    return c.join(pin, how="outer")
