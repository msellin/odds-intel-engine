"""#128 OU-LOW-LINES-BIAS-SWEEP — is any O/U line (esp. over 0.5) mispriced at our books?

Pre-registration: dev/active/ou-low-lines-bias-sweep.md (committed before this ran).
This is a MARKET-BIAS test, not a model test: flat short-side backing per line x book x
league scoring tier x price time, plus over 0.5 against a Pinnacle-derived fair price.
44-cell family, Holm on the discovery half (md5(match_id) even), replication on the odd half.

    python3 scripts/ou_low_lines_bias_sweep.py            # prints the report
"""
from __future__ import annotations

import hashlib
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
from scipy import stats
from scipy.stats import poisson

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workers.model.devig import devig  # Shin (ANALYSIS_GOTCHAS §78)

START, END = "2026-05-15", "2026-09-24"
FAMILY_BOOKS = ["Coolbet", "Epicbet"]
DESC_BOOKS = ["Tonybet", "Unibet-Site", "Pinnacle", "Bet365", "1xBet", "Marathonbet", "Betano"]
LINES = {"over_under_05": 0, "over_under_15": 1, "over_under_25": 2, "over_under_35": 3}
SHORT = {"over_under_05": "over", "over_under_15": "over", "over_under_25": "over", "over_under_35": "under"}
TIERS = [("all", -1, 99), ("low", -1, 2.50), ("mid", 2.50, 2.90), ("high", 2.90, 99)]
RHO = -0.10
MIN_N = 100

# SQL has no literal per-cent signs anywhere (ANALYSIS_GOTCHAS §59 d).
PRICES_SQL = """
with base as (
  select o.match_id, o.bookmaker, o.market, o.selection, o.odds, o.timestamp, m.date as ko
  from odds_snapshots o join matches m on m.id = o.match_id
  where o.market = any(%(markets)s) and o.bookmaker = any(%(books)s)
    and not coalesce(o.is_live, false) and o.timestamp <= m.date
    and m.status = 'finished' and m.date >= %(start)s and m.date < %(end)s
)
select distinct on (match_id, bookmaker, market, selection, win)
       match_id, bookmaker, market, selection, odds, win
from (
  select *, 'close' as win from base
  union all
  select *, 'early' as win from base
   where timestamp between ko - interval '48 hours' and ko - interval '6 hours'
) x
order by match_id, bookmaker, market, selection, win, timestamp desc
"""

PIN_SQL = """
select distinct on (o.match_id, o.market, o.selection) o.match_id, o.market, o.selection, o.odds
from odds_snapshots o join matches m on m.id = o.match_id
where o.bookmaker = 'Pinnacle' and o.market in ('1x2', 'over_under_25', 'over_under_15')
  and not coalesce(o.is_live, false) and o.timestamp <= m.date
  and o.timestamp >= m.date - interval '6 hours'
  and m.status = 'finished' and m.date >= %(start)s and m.date < %(end)s
order by o.match_id, o.market, o.selection, o.timestamp desc
"""

MATCHES_SQL = """
select id as match_id, league_id, date, score_home + score_away as goals
from matches where status = 'finished' and score_home is not null
  and date >= %(hist)s and date < %(end)s
"""


def load(conn):
    books = FAMILY_BOOKS + DESC_BOOKS
    px = pd.read_sql(PRICES_SQL, conn, params=dict(markets=list(LINES), books=books, start=START, end=END))
    pin = pd.read_sql(PIN_SQL, conn, params=dict(start=START, end=END))
    mt = pd.read_sql(MATCHES_SQL, conn, params=dict(hist="2025-05-01", end=END))
    return px, pin, mt


def league_tier(mt: pd.DataFrame) -> pd.DataFrame:
    """Walk-forward league goals/match over the prior 365 days, current match excluded."""
    mt = mt.sort_values("date").copy()
    mt["date"] = pd.to_datetime(mt["date"], utc=True)
    out = []
    for _, g in mt.groupby("league_id"):
        s = g.set_index("date")["goals"].astype(float)
        mean = s.rolling("365D", closed="left").mean()
        cnt = s.rolling("365D", closed="left").count()
        out.append(pd.DataFrame({"match_id": g["match_id"].values,
                                 "lg_goals": np.where(cnt.values >= 30, mean.values, np.nan)}))
    return pd.concat(out)


def two_way(px: pd.DataFrame) -> pd.DataFrame:
    w = px.pivot_table(index=["match_id", "bookmaker", "market", "win"], columns="selection",
                       values="odds", aggfunc="first").reset_index()
    w = w.dropna(subset=["over", "under"])
    return w[(w.over > 1) & (w.under > 1)]


def ladder_ok(w: pd.DataFrame) -> pd.Series:
    """Over-odds must strictly increase with the line wherever adjacent lines exist."""
    lad = w.pivot_table(index=["match_id", "bookmaker", "win"], columns="market", values="over", aggfunc="first")
    bad = pd.Series(False, index=lad.index)
    cols = [c for c in LINES if c in lad.columns]
    for a, b in zip(cols, cols[1:]):
        bad |= (lad[a] >= lad[b]).fillna(False)
    return bad.groupby(level=["match_id", "bookmaker"]).any()


# ── Dixon-Coles goal grid, fitted to Shin-de-vigged Pinnacle ─────────────────────────
G = np.arange(0.05, 4.01, 0.05)
LH, LA = [x.ravel() for x in np.meshgrid(G, G, indexing="ij")]
K = np.arange(0, 11)


def _grid(lh, la, rho=RHO):
    ph = poisson.pmf(K[:, None], lh[None]); pa = poisson.pmf(K[:, None], la[None])
    J = ph[:, None, :] * pa[None, :, :]
    J[0, 0] *= 1 - lh * la * rho
    J[0, 1] *= 1 + lh * rho
    J[1, 0] *= 1 + la * rho
    J[1, 1] *= 1 - rho
    i, j = np.meshgrid(K, K, indexing="ij")
    return dict(H=(J * (i > j)[..., None]).sum((0, 1)), D=(J * (i == j)[..., None]).sum((0, 1)),
                A=(J * (i < j)[..., None]).sum((0, 1)), U25=(J * ((i + j) <= 2)[..., None]).sum((0, 1)),
                U15=(J * ((i + j) <= 1)[..., None]).sum((0, 1)), P00=J[0, 0])


GRID = _grid(LH, LA)


def fair_over05(pin: pd.DataFrame) -> pd.DataFrame:
    w = pin.pivot_table(index="match_id", columns=["market", "selection"], values="odds", aggfunc="first")
    rows = []
    for mid, r in w.iterrows():
        try:
            x = devig([r[("1x2", "home")], r[("1x2", "draw")], r[("1x2", "away")]])
            u = devig([r[("over_under_25", "over")], r[("over_under_25", "under")]])
        except KeyError:
            continue
        if not x or not u or any(pd.isna(v) for v in x + u):
            continue
        err = (GRID["H"] - x[0]) ** 2 + (GRID["D"] - x[1]) ** 2 + (GRID["A"] - x[2]) ** 2 + (GRID["U25"] - u[1]) ** 2
        o15 = r.get(("over_under_15", "over")); u15 = r.get(("over_under_15", "under"))
        if o15 is not None and u15 is not None and not pd.isna(o15) and not pd.isna(u15):
            v = devig([o15, u15])
            if v:
                err = err + (GRID["U15"] - v[1]) ** 2
        k = int(err.argmin())
        rows.append((mid, 1 - GRID["P00"][k]))
    return pd.DataFrame(rows, columns=["match_id", "fair_o05"])


def cell(pnl) -> tuple[int, float, float, float]:
    pnl = np.asarray(pnl, float); n = len(pnl)
    if n < MIN_N:
        return n, (pnl.mean() if n else np.nan), np.nan, 1.0
    t, p = stats.ttest_1samp(pnl, 0.0)
    return n, pnl.mean(), t, p


def holm(ps):
    ps = np.asarray(ps); order = np.argsort(ps); m = len(ps); adj = np.empty(m); run = 0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * ps[i])); adj[i] = run
    return adj


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    px, pin, mt = load(conn)
    w = two_way(px)
    bad = ladder_ok(w)
    n_bad = int(bad.sum())
    w = w.merge(bad.rename("bad").reset_index(), on=["match_id", "bookmaker"], how="left")
    w = w[~w.bad.fillna(False)].drop(columns="bad")
    w = w.merge(mt[["match_id", "goals"]], on="match_id").merge(league_tier(mt), on="match_id", how="left")
    w["line"] = w.market.map(LINES)
    w["over_won"] = w.goals > w.line
    w["half"] = [int(hashlib.md5(str(m).encode()).hexdigest(), 16) % 2 for m in w.match_id]
    short = w.market.map(SHORT)
    w["s_odds"] = np.where(short == "over", w.over, w.under)
    w["s_won"] = np.where(short == "over", w.over_won, ~w.over_won)
    w["s_pnl"] = np.where(w.s_won, w.s_odds - 1, -1.0)
    w["l_pnl"] = np.where(short == "over", np.where(~w.over_won, w.under - 1, -1.0), np.where(w.over_won, w.over - 1, -1.0))

    fo = fair_over05(pin)
    print(f"#128 O/U low-lines bias sweep — kickoffs {START}..{END}")
    print(f"(fixture, book) pairs dropped by the ladder-monotonicity guard: {n_bad:,}")
    print(f"Pinnacle-derived fair over-0.5 available for {len(fo):,} matches\n")

    # ── descriptive: calibration + long side, all books, CLOSE, both halves ──
    print("DESCRIPTIVE (not in family) — CLOSE, both halves")
    print(f"{'line':14}{'book':13}{'n':>7}{'implied_over':>13}{'actual_over':>12}{'ROI short':>10}{'ROI long':>10}")
    c = w[w.win == "close"]
    for mk in LINES:
        for bk in FAMILY_BOOKS + DESC_BOOKS:
            x = c[(c.market == mk) & (c.bookmaker == bk)]
            if len(x) < 50:
                continue
            imp = np.mean([devig([a, b])[0] for a, b in zip(x.over, x.under)])
            print(f"{mk:14}{bk:13}{len(x):7}{imp:13.4f}{x.over_won.mean():12.4f}{x.s_pnl.mean():+10.2%}{x.l_pnl.mean():+10.2%}")
    print()

    # ── the family, discovery half ──
    fam = []
    d = w[w.half == 0]
    for mk in LINES:
        for bk in FAMILY_BOOKS:
            for tn, lo, hi in TIERS:
                x = d[(d.win == "close") & (d.market == mk) & (d.bookmaker == bk)]
                if tn != "all":
                    x = x[(x.lg_goals >= lo) & (x.lg_goals < hi)]
                fam.append(("A", f"{mk} {SHORT[mk]} close {bk} tier={tn}", x))
    for mk in LINES:
        for bk in FAMILY_BOOKS:
            x = d[(d.win == "early") & (d.market == mk) & (d.bookmaker == bk)]
            fam.append(("B", f"{mk} {SHORT[mk]} early {bk}", x))
    c05 = w[(w.win == "close") & (w.market == "over_under_05")].merge(fo, on="match_id")
    c05["impl"] = [devig([a, b])[0] for a, b in zip(c05.over, c05.under)]
    c05 = c05[(c05.impl - c05.fair_o05).abs() <= 0.10]
    c05["ev"] = c05.over * c05.fair_o05 - 1
    for thr in (0.0, 0.02):
        for bk in FAMILY_BOOKS:
            x = c05[(c05.half == 0) & (c05.bookmaker == bk) & (c05.ev >= thr)]
            x = x.assign(s_pnl=np.where(x.over_won, x.over - 1, -1.0))
            fam.append(("C", f"over_05 derived-fair EV>={thr:.0%} close {bk}", x))

    res = [(arm, name, *cell(x.s_pnl), x) for arm, name, x in fam]
    adj = holm([r[5] for r in res])
    print(f"FAMILY — discovery half (md5 even), m = {len(res)} cells, Holm two-sided")
    print(f"{'arm':4}{'cell':52}{'n':>6}{'ROI':>9}{'t':>7}{'p':>8}{'p_holm':>8}")
    survivors = []
    for (arm, name, n, roi, t, p, x), pa in zip(res, adj):
        flag = " ◀" if pa < 0.05 else ""
        print(f"{arm:4}{name:52}{n:6}{roi:+9.2%}{(t if not np.isnan(t) else 0):7.2f}{p:8.3f}{pa:8.3f}{flag}")
        if pa < 0.05:
            survivors.append(name)
    print(f"\nSurvivors: {survivors or 'none'}")

    # replication half for the cells worth reading (all shown so nothing is hidden)
    print("\nREPLICATION half (md5 odd) — over 0.5 cells, for the owner's question")
    r = w[(w.half == 1) & (w.market == "over_under_05")]
    for bk in FAMILY_BOOKS:
        for wn in ("close", "early"):
            n, roi, t, p = cell(r[(r.bookmaker == bk) & (r.win == wn)].s_pnl)
            print(f"  over 0.5 {wn:5} {bk:8} n={n:5} ROI {roi:+6.2%}  t={t if not np.isnan(t) else 0:5.2f}")
    for thr in (0.0, 0.02):
        for bk in FAMILY_BOOKS:
            x = c05[(c05.half == 1) & (c05.bookmaker == bk) & (c05.ev >= thr)]
            n, roi, t, p = cell(np.where(x.over_won, x.over - 1, -1.0))
            print(f"  over 0.5 derived EV>={thr:.0%} {bk:8} n={n:5} ROI {roi:+6.2%}")

    # over 0.5 by month, both halves (descriptive)
    print("\nOver 0.5 by kickoff month, CLOSE, both halves (descriptive)")
    m05 = w[(w.market == "over_under_05") & (w.win == "close")].merge(mt[["match_id", "date"]], on="match_id")
    m05["month"] = pd.to_datetime(m05.date, utc=True).dt.strftime("%Y-%m")
    for (bk, mo), x in m05.groupby(["bookmaker", "month"]):
        if len(x) >= 30:
            print(f"  {bk:12} {mo}  n={len(x):5}  avg odds {x.over.mean():.3f}  hit {x.over_won.mean():.3f}  ROI {x.s_pnl.mean():+6.2%}")


if __name__ == "__main__":
    main()
