#!/usr/bin/env python3
"""#187 AH sharp-outlier bot — the pre-registered backtest (dev/active/ah-market-bot-prereg.md).

Reads the extract.py parquet files. Every rule below is the pre-registration's; change them only through a
dated AMENDMENT in that doc.

    python3 scripts/analysis/ah_market/backtest.py --phase discovery
    python3 scripts/analysis/ah_market/backtest.py --phase holdout --cells "<cell>,<cell>"   # run ONCE
    python3 scripts/analysis/ah_market/backtest.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

D = "data/models/_research/ah_market"
NON_OFFERS = {"Unibet-Kambi", "Unibet", "Max", "Avg", "Betfair Exchange", "BetWin", "Betfred",
              "api-football", "api-football-live"}
DIRECT_BOOKS = {"Epicbet", "Tonybet", "Coolbet"}
EV_LO, EV_HI = 0.03, 0.15
LEAD_MIN = 45
EARLY_H = 12
CLOSE_WINDOW_MIN = 90
MIN_CONS_BOOKS = 3            # AMENDMENT 1 (was 5 — structurally impossible for AH)
DISCOVERY = ("2026-07-01", "2026-09-01")
HOLDOUT = ("2026-09-01", "2026-09-26")
SEED, B = 20260926, 10_000


# ── pure helpers (self-tested) ────────────────────────────────────────────────────────────────────
def power_devig(o1: np.ndarray, o2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """2-way power de-vig: k with (1/o1)^k + (1/o2)^k = 1. Returns (q1, q2)."""
    a, b = 1.0 / np.asarray(o1, float), 1.0 / np.asarray(o2, float)
    lo, hi = np.full(a.shape, 0.2), np.full(a.shape, 5.0)
    for _ in range(60):
        k = (lo + hi) / 2
        f = a ** k + b ** k - 1
        hi = np.where(f < 0, k, hi)      # sum too small → k too big
        lo = np.where(f < 0, lo, k)
    k = (lo + hi) / 2
    return a ** k, b ** k


def line_type(line: float) -> str:
    f = round(abs(line) % 1, 2)
    return "whole" if f == 0 else ("half" if f == 0.5 else "quarter")


def ah_pnl(side: str, home_line: float, margin: int, odds: float) -> float:
    """Flat 1-unit P&L. `home_line` = the HOME team's handicap; margin = home − away goals.
    Quarter lines split the stake over the two neighbouring lines."""
    def one(line: float) -> float:
        x = margin + line if side == "home" else -(margin + line)
        return (odds - 1) if x > 1e-9 else (0.0 if abs(x) <= 1e-9 else -1.0)
    if line_type(home_line) == "quarter":
        return 0.5 * one(home_line - 0.25) + 0.5 * one(home_line + 0.25)
    return one(home_line)


def boot_p_greater_zero(x: np.ndarray, rng) -> float:
    """One-sided bootstrap p for mean(x) > 0 (share of resampled means <= 0)."""
    x = np.asarray(x, float)
    if len(x) < 2:
        return float("nan")
    idx = rng.integers(0, len(x), size=(B, len(x)))
    return float((x[idx].mean(axis=1) <= 0).mean())


def holm(pvals: dict[str, float], alpha=0.05) -> dict[str, bool]:
    items = sorted((p, k) for k, p in pvals.items() if p == p)
    m, out, stop = len(items), {}, False
    for i, (p, k) in enumerate(items):
        ok = (not stop) and p <= alpha / (m - i)
        stop = stop or not ok
        out[k] = ok
    return out


def selftest() -> None:
    # whole line: home -1 wins by 1 → push; by 2 → win
    assert ah_pnl("home", -1.0, 1, 2.0) == 0.0 and ah_pnl("home", -1.0, 2, 2.0) == 1.0
    assert ah_pnl("away", -1.0, 1, 2.0) == 0.0 and ah_pnl("away", -1.0, 0, 2.0) == 1.0
    # half line: home -0.5 draw → loss; away (+0.5) draw → win
    assert ah_pnl("home", -0.5, 0, 1.9) == -1.0 and abs(ah_pnl("away", -0.5, 0, 1.9) - 0.9) < 1e-9
    # quarter: home -0.25, draw → half loss; home -0.75 win by 1 → half win (0.5*(o-1))
    assert ah_pnl("home", -0.25, 0, 2.0) == -0.5
    assert abs(ah_pnl("home", -0.75, 1, 2.0) - 0.5) < 1e-9
    # away side of home -0.75 (= away +0.75), home wins by 1 → half loss
    assert ah_pnl("away", -0.75, 1, 2.0) == -0.5
    # home +0.25 (home receives), draw → half win
    assert abs(ah_pnl("home", 0.25, 0, 2.0) - 0.5) < 1e-9
    q1, q2 = power_devig(np.array([1.9]), np.array([1.9]))
    assert abs(q1[0] - 0.5) < 1e-6 and abs(q1[0] + q2[0] - 1) < 1e-9
    q1, q2 = power_devig(np.array([1.5]), np.array([2.6]))
    assert abs(q1[0] + q2[0] - 1) < 1e-9 and q1[0] > 1 / 1.5 - 0.05
    assert line_type(-1.25) == "quarter" and line_type(0.5) == "half" and line_type(-2.0) == "whole"
    assert holm({"a": 0.01, "b": 0.04}) == {"a": True, "b": True}
    assert holm({"a": 0.03, "b": 0.04}) == {"a": False, "b": False}
    print("selftest OK")


# ── data ──────────────────────────────────────────────────────────────────────────────────────────
def load():
    runs = pd.read_parquet(f"{D}/runs.parquet")
    pin = pd.read_parquet(f"{D}/pin_fetch.parquet")
    m = pd.read_parquet(f"{D}/matches.parquet").drop_duplicates("match_id")
    for c in ("first_ts", "last_ts"):
        runs[c] = pd.to_datetime(runs[c], utc=True)
    pin["ts"] = pd.to_datetime(pin["ts"], utc=True)
    m["kickoff"] = pd.to_datetime(m["kickoff"], utc=True)
    runs = runs[~runs.bookmaker.isin(NON_OFFERS)]
    runs["line"] = runs["line"].round(2)
    pin["line"] = pin["line"].round(2)
    return runs, pin, m


def pin_fair(pin: pd.DataFrame) -> pd.DataFrame:
    """One row per Pinnacle fetch × line with both sides: q_home, q_away (power de-vig)."""
    w = (pin.pivot_table(index=["match_id", "ts", "line"], columns="selection", values="odds", aggfunc="last")
            .dropna(subset=["home", "away"]).reset_index())
    w["q_home"], w["q_away"] = power_devig(w["home"].values, w["away"].values)
    return w


def fitted_fair(fair: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """TWIN — LADDER FIT (prereg): for every Pinnacle fetch whose quoted rungs fit ONE Skellam goal-difference
    distribution (workers/model/ah_ladder.fit_grid, max resid 0.03), a fair row for each rung a book quotes on
    that match that Pinnacle did NOT quote in the fetch. `fitted` = True marks them."""
    import os
    import sys as _sys
    _sys.path.insert(0, os.getcwd())
    from workers.model.ah_ladder import fit_grid, grid_q
    book_lines = runs[runs.bookmaker != "Pinnacle"].groupby("match_id").line.agg(lambda x: set(x.round(2)))
    rows = []
    for (mid, ts), g in fair.groupby(["match_id", "ts"], sort=False):
        quoted = dict(zip(g.line.round(2), g.q_home))
        want = book_lines.get(mid, set()) - set(quoted)
        if not want:
            continue
        f = fit_grid(quoted)
        if f is None:
            continue
        for L in want:
            qh = grid_q(f, L, "home")
            if qh is not None:
                rows.append((mid, ts, L, qh, 1.0 - qh))
    out = pd.DataFrame(rows, columns=["match_id", "ts", "line", "q_home", "q_away"])
    out["fitted"] = True
    return out


def book_close(runs: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """Each book's last pre-kickoff quote per (match, line, side) seen within CLOSE_WINDOW_MIN of kickoff,
    both sides present, power de-vigged → q_home/q_away per (match, book, line)."""
    r = runs.merge(m[["match_id", "kickoff"]], on="match_id")
    r = r[r.last_ts >= r.kickoff - pd.Timedelta(minutes=CLOSE_WINDOW_MIN)]
    r = r.sort_values("last_ts").groupby(["match_id", "bookmaker", "line", "selection"]).tail(1)
    w = (r.pivot_table(index=["match_id", "bookmaker", "line"], columns="selection", values="odds",
                       aggfunc="last").dropna(subset=["home", "away"]).reset_index())
    w["q_home"], w["q_away"] = power_devig(w["home"].values, w["away"].values)
    return w


def candidates(runs: pd.DataFrame, fair: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """Every (Pinnacle fetch tp, book run live across tp) pair on the same line and side with EV in band."""
    out = []
    fair = fair.merge(m[["match_id", "kickoff"]], on="match_id")
    fair = fair[fair.ts <= fair.kickoff - pd.Timedelta(minutes=LEAD_MIN)]
    books = runs[runs.bookmaker != "Pinnacle"]
    mids = fair.match_id.unique()
    for i in range(0, len(mids), 1500):
        chunk = set(mids[i:i + 1500])
        f = fair[fair.match_id.isin(chunk)]
        b = books[books.match_id.isin(chunk)]
        for side in ("home", "away"):
            fs = f[["match_id", "ts", "line", "kickoff", "fitted", f"q_{side}"]].rename(columns={f"q_{side}": "q"})
            bs = b[b.selection == side][["match_id", "line", "bookmaker", "odds", "first_ts", "last_ts"]]
            x = fs.merge(bs, on=["match_id", "line"])
            x = x[(x.first_ts <= x.ts) & (x.ts <= x.last_ts)]
            x["ev"] = x.odds * x.q - 1
            # AMENDMENT 1: CONFIRMED = another book, same instant/side/line, at EV >= 0 vs the same fair price
            nonneg = x[x.ev >= 0].groupby(["match_id", "ts", "line"]).bookmaker.nunique()
            x = x[(x.ev >= EV_LO) & (x.ev < EV_HI)].copy()
            x["n_nonneg"] = x.set_index(["match_id", "ts", "line"]).index.map(nonneg).fillna(0).values
            x["confirmed"] = x.n_nonneg >= 2          # itself + at least one other book
            x["side"] = side
            out.append(x)
    return pd.concat(out, ignore_index=True)


def ladder_guard(c: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """AMENDMENT 2: the pick book's price on (side, L) must lie between ITS OWN prices on L-0.25 and L+0.25
    (same side, runs live at the decision instant) wherever those exist. Home odds fall as the home line
    rises; away odds rise. Adds `ladder_ok` and `ladder_checked`."""
    c = c.reset_index(drop=True)
    b = runs[["match_id", "bookmaker", "selection", "line", "odds", "first_ts", "last_ts"]].rename(
        columns={"selection": "side", "line": "nline", "odds": "nodds"})
    ok = np.ones(len(c), bool)
    checked = np.zeros(len(c), bool)
    for d in (-0.25, 0.25):
        k = c[["match_id", "bookmaker", "side", "ts", "line", "odds"]].copy()
        k["nline"] = (k.line + d).round(2)
        k["i"] = k.index
        j = k.merge(b, on=["match_id", "bookmaker", "side", "nline"])
        j = j[(j.first_ts <= j.ts) & (j.ts <= j.last_ts)].drop_duplicates("i", keep="last")
        # moving the HOME line up (d>0) makes home likelier -> home odds DOWN, away odds UP
        home_dir = -1 if d > 0 else 1
        sign = np.where(j.side == "home", home_dir, -home_dir)
        bad = (sign * (j.nodds.values - j.odds.values)) < -1e-9
        ok[j.i.values[bad]] = False
        checked[j.i.values] = True
    c["ladder_ok"], c["ladder_checked"] = ok, checked
    return c


def picks_from(c: pd.DataFrame) -> pd.DataFrame:
    """First qualifying instant per (match, side, line), best-EV book at that instant; then one per match."""
    c = c.sort_values(["match_id", "side", "line", "ts", "ev"], ascending=[True, True, True, True, False])
    first_ts = c.groupby(["match_id", "side", "line"]).ts.transform("min")
    c = c[c.ts == first_ts].groupby(["match_id", "side", "line"]).head(1)
    c = c.sort_values(["match_id", "ts", "ev"], ascending=[True, True, False])
    return c.groupby("match_id").head(1).reset_index(drop=True)


def score(p: pd.DataFrame, close: pd.DataFrame, pin_close: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    p = p.merge(m[["match_id", "score_home", "score_away", "league", "tier"]], on="match_id")
    p["line_type"] = p.line.map(line_type)
    p["timing"] = np.where((p.kickoff - p.ts) >= pd.Timedelta(hours=EARLY_H), "early", "late")
    p["band"] = pd.cut(p.ev, [0.03, 0.05, 0.08, 0.15], right=False, labels=["3-5", "5-8", "8-15"]).astype(str)
    p["direct"] = p.bookmaker.isin(DIRECT_BOOKS)
    margin = (p.score_home - p.score_away).astype(int)
    p["pnl"] = [ah_pnl(s, l, mg, o) for s, l, mg, o in zip(p.side, p.line, margin, p.odds)]
    # independent consensus close: mean q over >= 5 books, Pinnacle and the pick's own book excluded
    cc = close[close.bookmaker != "Pinnacle"][["match_id", "bookmaker", "line", "q_home", "q_away"]]
    j = p[["match_id", "line", "side", "bookmaker"]].reset_index().merge(
        cc, on=["match_id", "line"], suffixes=("", "_c"))
    j = j[j.bookmaker != j.bookmaker_c]
    j["q"] = np.where(j.side == "home", j.q_home, j.q_away)
    agg = j.groupby("index").q.agg(["mean", "count"])
    p["cons_n"] = agg["count"].reindex(p.index).fillna(0).astype(int)
    p["q_cons"] = agg["mean"].reindex(p.index)
    p.loc[p.cons_n < MIN_CONS_BOOKS, "q_cons"] = np.nan
    p["clv_ind"] = p.odds * p.q_cons - 1
    # Pinnacle close: last fetch within the close window on the same line
    pc = pin_close.merge(m[["match_id", "kickoff"]], on="match_id")
    pc = pc[(pc.ts <= pc.kickoff) & (pc.ts >= pc.kickoff - pd.Timedelta(minutes=CLOSE_WINDOW_MIN))]
    pc = pc.sort_values("ts").groupby(["match_id", "line"]).tail(1)[["match_id", "line", "q_home", "q_away"]]
    p = p.merge(pc, on=["match_id", "line"], how="left", suffixes=("", "_pc"))
    p["clv_pin"] = p.odds * np.where(p.side == "home", p.q_home, p.q_away) - 1
    return p


def summarise(p: pd.DataFrame, keys: list[str], rng) -> pd.DataFrame:
    rows = []
    for k, g in p.groupby(keys) if keys else [("pooled", p)]:
        gi = g.dropna(subset=["clv_ind"])
        rows.append({
            "cell": "|".join(map(str, k)) if isinstance(k, tuple) else str(k),
            "n": len(g), "n_ind": len(gi),
            "clv_ind": gi.clv_ind.mean() if len(gi) else np.nan,
            "p_ind": boot_p_greater_zero(gi.clv_ind.values, rng) if len(gi) >= 30 else np.nan,
            "clv_pin": g.clv_pin.mean(), "roi": g.pnl.mean(),
            "roi_lo": g.pnl.mean() - 1.96 * g.pnl.std(ddof=1) / max(np.sqrt(len(g)), 1),
            "roi_hi": g.pnl.mean() + 1.96 * g.pnl.std(ddof=1) / max(np.sqrt(len(g)), 1),
            "ev": g.ev.mean(), "direct_share": g.direct.isin([True, "direct"]).mean(),
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["discovery", "holdout"])
    ap.add_argument("--cells", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--variant", choices=["base", "ladderfit", "consistency"], default="base")
    a = ap.parse_args()
    selftest()
    if a.selftest:
        return 0
    runs, pin, m = load()
    lo, hi = DISCOVERY if a.phase == "discovery" else HOLDOUT
    m = m[(m.kickoff >= lo) & (m.kickoff < hi)]
    runs, pin = runs[runs.match_id.isin(m.match_id)], pin[pin.match_id.isin(m.match_id)]
    fair = pin_fair(pin)
    close = book_close(runs, m)
    pin_close = fair.copy()                    # Pinnacle close = QUOTED rungs only, never a fitted one
    fair["fitted"] = False
    if a.variant == "ladderfit":
        ff = fitted_fair(fair[fair.ts <= fair.ts.max()], runs)
        print(f"ladder fit: {len(ff)} fitted rung-fetch rows")
        fair = pd.concat([fair, ff], ignore_index=True)
    c = candidates(runs, fair, m)
    c = ladder_guard(c, runs[runs.bookmaker != "Pinnacle"])
    print(f"ladder guard: {len(c)} candidates, checked {c.ladder_checked.mean():.0%}, "
          f"dropped {(~c.ladder_ok).sum()} ({(~c.ladder_ok).mean():.0%}); by book dropped:",
          c[~c.ladder_ok].bookmaker.value_counts().to_dict())
    c = c[c.ladder_ok]
    p = score(picks_from(c), close, pin_close, m)
    rng = np.random.default_rng(SEED)
    # AMENDMENT 1: two variants — every pick, and CONFIRMED picks (picked from confirmed candidates only)
    pc = score(picks_from(c[c.confirmed]), close, pin_close, m)
    p["variant"], pc["variant"] = "any", "confirmed"
    p = pd.concat([p, pc], ignore_index=True)
    pooled = summarise(p, ["variant"], rng)
    cells = summarise(p, ["variant", "line_type", "band", "timing"], rng)
    p.to_parquet(f"{D}/picks_{a.phase}{'' if a.variant == 'base' else '_' + a.variant}.parquet")
    if a.variant == "ladderfit":
        # PRIMARY (prereg TWIN): the FITTED-RUNG picks on independent-close CLV
        pa = p[p.variant == "any"].copy()
        pa["rung"] = np.where(pa.fitted.astype(bool), "fitted", "quoted")
        print("\nTWIN — LADDER FIT, by rung source (any variant):\n",
              summarise(pa, ["rung"], rng).round(4)[["cell", "n", "n_ind", "clv_ind", "p_ind", "clv_pin", "roi", "roi_lo", "roi_hi", "ev"]].to_string(index=False))
        if (pa.rung == "fitted").any():
            print("\nfitted picks by timing:\n", summarise(pa[pa.rung == "fitted"], ["timing"], rng).round(4)[["cell", "n", "n_ind", "clv_ind", "p_ind", "roi"]].to_string(index=False))
    if a.variant == "consistency":
        # PINNACLE-CONSISTENCY SPLIT (prereg): fit status of Pinnacle's quoted rungs at each pick's decision fetch
        import os
        import sys as _sys
        _sys.path.insert(0, os.getcwd())
        from workers.model.ah_ladder import fit_grid
        pa = p[p.variant == "any"].copy()
        stat = []
        for mid, ts in zip(pa.match_id, pa.ts):
            g = fair[(fair.match_id == mid) & (fair.ts == ts)]
            rungs = dict(zip(g.line.round(2), g.q_home))
            stat.append("unfittable" if len(rungs) < 2 else ("consistent" if fit_grid(rungs) else "inconsistent"))
        pa["pin_fit"] = stat
        print("\nPINNACLE-CONSISTENCY SPLIT:\n", summarise(pa, ["pin_fit"], rng).round(4)[["cell", "n", "n_ind", "clv_ind", "p_ind", "roi", "roi_lo", "roi_hi", "ev"]].to_string(index=False))
        cc = pa[(pa.pin_fit == "consistent")].clv_ind.dropna().values
        ci = pa[(pa.pin_fit == "inconsistent")].clv_ind.dropna().values
        if len(cc) >= 2 and len(ci) >= 2:
            diffs = rng.choice(cc, (B, len(cc))).mean(1) - rng.choice(ci, (B, len(ci))).mean(1)
            print(f"consistent - inconsistent = {cc.mean() - ci.mean():+.4f}  one-sided p = {(diffs <= 0).mean():.4f}  (n {len(cc)} / {len(ci)})")
        else:
            print(f"consistent - inconsistent: too few legs (n {len(cc)} / {len(ci)})")
    pd.set_option("display.width", 200)
    print(f"\n{a.phase}: matches {m.match_id.nunique()}  candidates {len(c)}  picks (any/confirmed) "
          f"{(p.variant == 'any').sum()}/{(p.variant == 'confirmed').sum()}  with independent close "
          f"{p.clv_ind.notna().sum()}")
    print("\nPOOLED (all lines, EV 3-15%, one per match):\n", pooled.round(4).to_string(index=False))
    if a.phase == "discovery":
        tested = cells[cells.n_ind >= 30]
        passed = holm(dict(zip(tested.cell, tested.p_ind)))
        cells["holm_pass"] = cells.cell.map(passed).fillna(False)
        print("\nCELLS (line type | EV band | timing):\n", cells.round(4).to_string(index=False))
        json.dump({"passed": [k for k, v in passed.items() if v]}, open(f"{D}/discovery_passed.json", "w"))
    else:
        want = [x for x in a.cells.split(",") if x]
        sel = cells[cells.cell.isin(want)]
        sel = pd.concat([pooled, sel])
        passed = holm(dict(zip(sel.cell, sel.p_ind)))
        sel["holm_pass"] = sel.cell.map(passed).fillna(False)
        print("\nHOLDOUT (carried cells + pooled):\n", sel.round(4).to_string(index=False))
    p["direct"] = p.direct.map({True: "direct", False: "af"})
    for k in ("direct", "timing", "line_type", "bookmaker"):
        print(f"\nby variant x {k}:\n", summarise(p, ["variant", k], rng)
              .round(4)[["cell", "n", "n_ind", "clv_ind", "p_ind", "clv_pin", "roi"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
