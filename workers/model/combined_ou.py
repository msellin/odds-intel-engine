"""COMBINED O/U MODEL ([[#149]] round O1, production copy for [[#152]]) — ratings + consensus + Pinnacle.

Tested (dev/active/market2-model-plan.md "ROUND O1"; scripts/ab_ou_combined.py): on 12,640
unseen matches (2026-08-31..09-24) it beat the served O/U ensemble on every line — log-loss
1.5 0.5655 vs 0.5934, 2.5 0.6738 vs 0.7086, 3.5 0.6428 vs 0.6961 — where the ensemble was worse
than the base rate on all three. On rows Pinnacle prices, Pinnacle alone is marginally better
still (0.5612 / 0.6731 / 0.6422), so the SERVED probability is Pinnacle where it prices the
line and the combined model elsewhere (`served_over`).

Per line L in {1.5, 2.5, 3.5}: one binary logit per availability group (P&C / P / C / none) over
log-odds of P(total > L) from
  * rating    — Poisson(dp_lh + dp_la), the walk-forward dynamic-Poisson goal rates
                (workers/model/ratings_1x2.py build_features)
  * consensus — mean power-de-vigged log-odds over non-Pinnacle books on that exact line
                (outlier guard |p - median| > 0.20 with >= 3 books) + log n_books
  * Pinnacle  — power de-vigged (never proportional: ANALYSIS_GOTCHAS #78)
Epoch seconds only (pandas 3.0.4, RELIABILITY_LEDGER #26).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from workers.model.market_consensus_1x2 import CONSENSUS_BOOKS

MODEL_VERSION = "ou_comb_v1"
LINES = {"over_under_15": 1.5, "over_under_25": 2.5, "over_under_35": 3.5}
GROUPS = ("P&C", "P", "C", "none")
MIN_ROWS = 300
OUTLIER_GAP = 0.20
LEG_SPREAD_S = 120


def power_devig(o1, o2) -> np.ndarray:
    """2-way power method: k with (1/o1)^k + (1/o2)^k = 1; returns p1."""
    a, b = 1 / np.asarray(o1, float), 1 / np.asarray(o2, float)
    lo, hi = np.full(a.shape, 0.2), np.full(a.shape, 5.0)
    for _ in range(60):
        k = (lo + hi) / 2
        f = a ** k + b ** k - 1
        hi = np.where(f < 0, k, hi)
        lo = np.where(f >= 0, k, lo)
    return a ** ((lo + hi) / 2)


def logit(p) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fetch_legs(conn, match_ids: list[str], books: list[str] | None = None) -> pd.DataFrame:
    """Latest pre-kickoff O/U leg per (match, line, book, side). `books` defaults to the
    consensus books + Pinnacle; the decision-time override passes ["Pinnacle"]."""
    books = list(books) if books else list(CONSENSUS_BOOKS) + ["Pinnacle"]
    q = """
        SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
               o.match_id::text, o.market, o.bookmaker, o.selection, o.odds::float8,
               extract(epoch FROM o."timestamp")::float8
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%(ids)s::uuid[]) AND o.market = ANY(%(mk)s)
           AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date
           AND o.bookmaker = ANY(%(books)s)
         ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o."timestamp" DESC"""
    rows = []
    with conn.cursor() as cur:
        for i in range(0, len(match_ids), 2000):
            cur.execute(q, {"ids": match_ids[i:i + 2000], "mk": list(LINES), "books": books})
            rows += cur.fetchall()
    return pd.DataFrame(rows, columns=["match_id", "market", "bookmaker", "selection", "odds", "ts"])


def consensus(legs: pd.DataFrame) -> pd.DataFrame:
    """Per (match, market): c_over, n_books, pin_over."""
    if legs.empty:
        return pd.DataFrame(columns=["match_id", "market", "c_over", "n_books", "pin_over"])
    w = legs.pivot_table(index=["match_id", "market", "bookmaker"], columns="selection",
                         values="odds", aggfunc="first")
    t = legs.groupby(["match_id", "market", "bookmaker"]).ts.agg(["min", "max"])
    w = w.join(t)
    if not {"over", "under"} <= set(w.columns):
        return pd.DataFrame(columns=["match_id", "market", "c_over", "n_books", "pin_over"])
    w = w.dropna(subset=["over", "under"])
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


def rating_over(lam, line: float) -> np.ndarray:
    from scipy.stats import poisson
    return 1 - poisson.cdf(np.floor(line), np.clip(np.asarray(lam, float), 0.05, 12))


def frame(matches: pd.DataFrame, cons: pd.DataFrame) -> pd.DataFrame:
    """matches: match_id, lam (= dp_lh + dp_la) [, y_total]. One row per (match, line)."""
    out = []
    for mk, L in LINES.items():
        d = matches.merge(cons[cons.market == mk].drop(columns="market"), on="match_id", how="left")
        d["market"], d["line"] = mk, L
        d["r_over"] = rating_over(d.lam.to_numpy(), L)
        if "total" in d:
            d["y"] = (d.total > L).astype(int)
        out.append(d)
    return pd.concat(out, ignore_index=True)


def groups(d: pd.DataFrame) -> np.ndarray:
    hp, hc = d.pin_over.notna().to_numpy(), d.c_over.notna().to_numpy()
    return np.where(hp & hc, "P&C", np.where(hp, "P", np.where(hc, "C", "none")))


def design(d: pd.DataFrame, g: str) -> np.ndarray:
    cols = [logit(d.r_over)[:, None]]
    if g in ("P&C", "C"):
        cols += [logit(d.c_over)[:, None], np.log(d.n_books.to_numpy(float))[:, None]]
    if g in ("P&C", "P"):
        cols.append(logit(d.pin_over)[:, None])
    return np.hstack(cols)


def fit(d: pd.DataFrame) -> dict:
    """d = frame() rows with y. Returns {market: {group: {coef, intercept, n}}}."""
    from sklearn.linear_model import LogisticRegression
    out = {}
    for mk in LINES:
        a = d[d.market == mk]
        g = groups(a); prm = {}
        for G in GROUPS:
            s = a[g == G]
            if len(s) >= MIN_ROWS and s.y.nunique() == 2:
                m = LogisticRegression(max_iter=3000, C=1.0).fit(design(s, G), s.y)
                prm[G] = {"coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]), "n": int(len(s))}
        out[mk] = prm
    return out


def predict(d: pd.DataFrame, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Combined P(over) per row + group label. A group with no params falls back to the rule
    source (Pinnacle > consensus > rating)."""
    P = d.r_over.to_numpy(float).copy()
    cm = d.c_over.notna().to_numpy(); P[cm] = d.c_over.to_numpy(float)[cm]
    pm = d.pin_over.notna().to_numpy(); P[pm] = d.pin_over.to_numpy(float)[pm]
    g = groups(d)
    mk = d.market.to_numpy()
    for market, prm in params.items():
        for G, p in prm.items():
            sel = (mk == market) & (g == G)
            if sel.any():
                z = design(d[sel], G) @ np.asarray(p["coef"]) + p["intercept"]
                P[sel] = 1 / (1 + np.exp(-z))
    return P, g


def served_over(d: pd.DataFrame, p_comb: np.ndarray) -> np.ndarray:
    """The SERVED probability: Pinnacle where it prices the line (marginally better there),
    the combined model elsewhere."""
    s = np.asarray(p_comb, float).copy()
    pm = d.pin_over.notna().to_numpy()
    s[pm] = d.pin_over.to_numpy(float)[pm]
    return s


# ── SERVED p AT DECISION TIME ([[#176]], 2026-09-26) ──────────────────────────────────
# ou_model_predictions is written by the combined refresh; a bot must never decide on a
# row written before Pinnacle's CURRENT quote landed. Measured: bot_v10_ou_comb_v1 took
# over 2.5 @ 2.62 at 02:06 on p 0.4201 (combined model, written 01:40 before Pinnacle
# priced the line) while Pinnacle's 02:00 quote de-vigs to 0.3447 -> EV -9.7%. So at
# decision time the served p is re-derived: Pinnacle's latest pre-match quote on that exact
# line, if it is <= QUOTE_MAX_AGE_H old (the same freshness as VIP O/U EARLY), else the
# stored p. One implementation: fetch_legs + consensus (power de-vig) + served_over.
def fresh_pinnacle_over(conn, match_ids: list[str], now_ts: float,
                        max_age_h: float | None = None) -> pd.DataFrame:
    """(match_id, market, pin_over) from Pinnacle's latest pre-match O/U quote, only where
    both legs are <= max_age_h old at now_ts (default: ou_sharp_outlier.QUOTE_MAX_AGE_H)."""
    if max_age_h is None:
        from workers.jobs.ou_sharp_outlier import QUOTE_MAX_AGE_H as max_age_h
    legs = fetch_legs(conn, match_ids, books=["Pinnacle"]) if match_ids else pd.DataFrame()
    return pinnacle_from_legs(legs, now_ts, max_age_h)


def pinnacle_from_legs(legs: pd.DataFrame, now_ts: float, max_age_h: float) -> pd.DataFrame:
    """Pure part of fresh_pinnacle_over (behaviour-tested in smoke)."""
    cols = ["match_id", "market", "pin_over"]
    if legs is None or legs.empty:
        return pd.DataFrame(columns=cols)
    legs = legs[(legs.bookmaker == "Pinnacle") & (now_ts - legs.ts <= max_age_h * 3600)]
    if legs.empty:
        return pd.DataFrame(columns=cols)
    c = consensus(legs)
    return c.dropna(subset=["pin_over"])[cols] if "pin_over" in c else pd.DataFrame(columns=cols)


def served_at_decision(stored: dict, pin: pd.DataFrame) -> dict:
    """stored: {(match_id, market): (p_over, p_pin)} from ou_model_predictions. Returns the same
    shape with p_over = fresh Pinnacle where `pin` prices the line (served_over's rule), else
    the stored p unchanged."""
    if not stored or pin is None or pin.empty:
        return dict(stored)
    keys = list(stored)
    d = pd.DataFrame({"match_id": [k[0] for k in keys], "market": [k[1] for k in keys]})
    d = d.merge(pin[["match_id", "market", "pin_over"]], on=["match_id", "market"], how="left")
    s = served_over(d, np.array([stored[k][0] for k in keys], float))
    fresh = d.pin_over.to_numpy()
    return {k: (float(s[i]), (float(fresh[i]) if pd.notna(fresh[i]) else stored[k][1]))
            for i, k in enumerate(keys)}
