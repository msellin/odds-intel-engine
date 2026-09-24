#!/usr/bin/env python3
"""#118 — does an xG-fed GAP rating add anything to Pinnacle's O/U 2.5 price, in the ten
leagues that carry API-Football xG?

Pre-registered in dev/active/per-market-feature-sets-design.md ("#118 xG GAP RATINGS")
before this was written. Reuses the #089 Wheatcroft harness (scripts/wheatcroft_replication.py):
four additive GAP ratings per team, the market ALWAYS in the forecast, weekly refit on
prior matches, block bootstrap by week, Holm across the family.

    inputs   xG · shots+corners · goals (control)
    markets  EARLY = Pinnacle's first complete O/U 2.5 pair
             CLOSE = latest complete pair (sides <= 2 min apart), <= 60 min old, before kickoff

    python3 scripts/xg_gap_top_leagues.py            # tuned (~5-10 min)
    python3 scripts/xg_gap_top_leagues.py --quick    # fixed params
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.wheatcroft_replication import (  # noqa: E402
    THETA0, boot_p, gap_sums, holm, ll, logit, tune, weekly_forecasts)

LEAGUES = (("Premier League", "England"), ("Championship", "England"), ("La Liga", "Spain"),
           ("Serie A", "Italy"), ("Bundesliga", "Germany"), ("Ligue 1", "France"),
           ("Eredivisie", "Netherlands"), ("Primeira Liga", "Portugal"), ("Süper Lig", "Turkey"),
           ("Jupiler Pro League", "Belgium"))
START, BURN_IN = "2023-07-01", "2324"
INPUTS = ("xg", "shots_corners", "goals")
MIN_PLAYED = 6
CLOSE_FRESH_MIN, PAIR_TOL_MIN = 60, 2


def season_of(d) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def load() -> pd.DataFrame:
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig

    cond = " OR ".join(["(l.name = %s AND l.country = %s)"] * len(LEAGUES))
    params = [x for pair in LEAGUES for x in pair]
    rows = execute_query(f"""
        SELECT m.id::text mid, m.league_id::text lg, m.date, m.home_team_id::text h,
               m.away_team_id::text a, m.score_home::float hg, m.score_away::float ag,
               ms.shots_home::float hs, ms.shots_away::float as_, ms.corners_home::float hc,
               ms.corners_away::float ac, ms.xg_home::float xgh, ms.xg_away::float xga
          FROM matches m JOIN leagues l ON l.id = m.league_id
          LEFT JOIN match_stats ms ON ms.match_id = m.id
         WHERE ({cond}) AND m.status = 'finished' AND m.date >= %s
           AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
         ORDER BY m.date, m.id""", params + [START])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df.date, utc=True)
    df["season"] = df.date.map(season_of)

    # Pinnacle O/U 2.5, strictly pre-kickoff, not in-play
    snaps = defaultdict(lambda: defaultdict(list))
    ids = df.mid.tolist()
    for i in range(0, len(ids), 500):
        for r in execute_query("""
            SELECT o.match_id::text mid, o.selection, o.timestamp ts, o.odds::float od,
                   COALESCE(o.is_closing, false) closing
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = 'Pinnacle'
               AND o.market = 'over_under_25' AND o.is_live IS NOT TRUE AND o.odds > 1.01
               AND (o.timestamp < m.date OR COALESCE(o.is_closing, false))""", (ids[i:i + 500],)):
            # AMENDMENT (pre-registered 2026-09-24, before any CLOSE cell was scored):
            # historical Pinnacle rows are football-data.co.uk ingests — `is_closing`
            # (FD's PC>2.5) is stamped AT kickoff, so a strict `< kickoff` filter
            # dropped every one. Closing rows are kept apart and used as CLOSE.
            if r["selection"] in ("over", "under"):
                key = "closing" if r["closing"] else "pre"
                snaps[r["mid"]][(key, r["selection"])].append((pd.Timestamp(r["ts"]), r["od"]))
    ko = dict(zip(df.mid, df.date))
    e_o, e_u, e_p, c_p = [], [], [], []
    tol = pd.Timedelta(minutes=PAIR_TOL_MIN)
    for mid in df.mid:
        s = snaps.get(mid, {})
        ov, un = sorted(s.get(("pre", "over"), [])), sorted(s.get(("pre", "under"), []))
        co, cu = s.get(("closing", "over")), s.get(("closing", "under"))
        pairs = []                              # (ts, over, under), sides within ±2 min
        for t, o in ov:
            near = [(abs((tu - t).total_seconds()), u) for tu, u in un if abs(tu - t) <= tol]
            if near:
                pairs.append((t, o, min(near)[1]))
        early = pairs[0] if pairs else None
        close = pairs[-1] if pairs and (ko[mid] - pairs[-1][0]) <= pd.Timedelta(minutes=CLOSE_FRESH_MIN) else None
        fe = devig([early[1], early[2]]) if early else None
        if co and cu:                            # FD's Pinnacle close, stamped at kickoff
            fc = devig([max(co)[1], max(cu)[1]])
        else:
            fc = devig([close[1], close[2]]) if close and close[0] > (early[0] if early else close[0] - tol) else None
        e_o.append(early[1] if early else np.nan); e_u.append(early[2] if early else np.nan)
        e_p.append(fe[0] if fe else np.nan); c_p.append(fc[0] if fc else np.nan)
    df["e_o"], df["e_u"], df["p_early"], df["p_close"] = e_o, e_u, e_p, c_p

    df["over"] = ((df.hg + df.ag) > 2.5).astype(float)
    # has_sc = EVERY input present, so all three inputs train and score on the same rows
    df["has_sc"] = df[["hs", "as_", "hc", "ac", "xgh", "xga"]].notna().all(axis=1)
    played = defaultdict(int)
    ph, pa = [], []
    for lg, s, h, a in zip(df.lg, df.season, df.h, df.a):
        ph.append(played[(lg, s, h)]); pa.append(played[(lg, s, a)])
        played[(lg, s, h)] += 1; played[(lg, s, a)] += 1
    df["elig"] = (np.array(ph) >= MIN_PLAYED) & (np.array(pa) >= MIN_PLAYED)
    df["week"] = df.date.dt.strftime("%G-%V")
    return df.reset_index(drop=True)


def money(p, o_o, o_u, y, pc):
    n, ret, clv = 0, 0.0, []
    for pp, oo, ou, yy, cf in zip(p, o_o, o_u, y, pc):
        if not (pp == pp and oo == oo and ou == ou):
            continue
        eo, eu = pp * oo - 1, (1 - pp) * ou - 1
        if max(eo, eu) <= 0:
            continue
        over = eo >= eu
        odds = oo if over else ou
        n += 1
        ret += (odds - 1) if (yy == 1) == over else -1.0
        if cf == cf:
            clv.append(odds * (cf if over else 1 - cf) - 1)
    return n, (ret / n if n else float("nan")), (float(np.mean(clv)) if clv else float("nan"))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--maxfev", type=int, default=90)
    a = ap.parse_args()
    rng = np.random.default_rng(118)
    df = load()
    M = {"EARLY": logit(df.p_early.to_numpy()), "CLOSE": logit(df.p_close.to_numpy())}
    seasons = sorted(df.season.unique())
    test = (df.season > BURN_IN).to_numpy()
    base = test & df.elig.to_numpy() & df.has_sc.to_numpy()
    print(f"{len(df):,} matches in {df.lg.nunique()} leagues, seasons {seasons}; all inputs present "
          f"{int(df.has_sc.sum()):,}; test-period eligible {int(base.sum()):,} "
          f"(EARLY {int((base & np.isfinite(M['EARLY'])).sum()):,}, CLOSE {int((base & np.isfinite(M['CLOSE'])).sum()):,})\n")

    S = {}
    for inp in INPUTS:
        S[inp] = np.full(len(df), np.nan)
        th = THETA0
        for s in [x for x in seasons if x > BURN_IN]:
            if not a.quick:
                th = tune(df, inp, s, M["EARLY"], th, a.maxfev)
            rows = (df.season == s).to_numpy()
            S[inp][rows] = gap_sums(df, inp, th)[rows]
            print(f"  {inp:14s} {s}  λ={th[0]:.3f} φ1={th[1]:.3f} φ2={th[2]:.3f}", flush=True)

    y = df.over.to_numpy()
    cells, preds = [], {}
    for mk in ("EARLY", "CLOSE"):
        # score only fixtures that EVERY input can forecast, so the rows are identical
        per = {inp: weekly_forecasts(df, S[inp], M[mk], test) for inp in INPUTS}
        common = np.ones(len(df), bool)
        for pf, pm in per.values():
            common &= np.isfinite(pf) & np.isfinite(pm)
        for inp in INPUTS:
            pf, pm = per[inp]
            d = ll(pm[common], y[common]) - ll(pf[common], y[common])
            cells.append([inp, mk, int(common.sum()), float(d.mean()), float(ll(pm[common], y[common]).mean()),
                          float(ll(pf[common], y[common]).mean()), boot_p(d, df.week.to_numpy()[common], rng)])
            preds[(inp, mk)] = (pf, pm, common)
    hp = holm([c[6] for c in cells])
    print(f"\nSKILL — ΔLL = LL(market-only) − LL(market + GAP rating), nats; Holm m={len(cells)}")
    print(f"  {'input':14s} {'market':6s} {'n':>6s} {'ΔLL':>10s} {'LL mkt':>8s} {'LL full':>8s} {'p':>7s} {'Holm':>7s}")
    for c, h in zip(cells, hp):
        print(f"  {c[0]:14s} {c[1]:6s} {c[2]:6d} {c[3]:+10.5f} {c[4]:8.4f} {c[5]:8.4f} {c[6]:7.3f} {h:7.3f}"
              f"{'  PASS' if (c[3] > 0 and h < 0.05) else ''}")

    print("\nMONEY — level stakes at Pinnacle's EARLY price where p̂ beats it; CLV vs de-vigged close")
    pc = df.p_close.to_numpy()
    for inp in INPUTS:
        pf, pm, k = preds[(inp, "EARLY")]
        n, roi, clv = money(pf[k], df.e_o.to_numpy()[k], df.e_u.to_numpy()[k], y[k], pc[k])
        print(f"  {inp:14s} n={n:5d}  ROI {100*roi:+6.2f}%  CLV {100*clv:+6.2f}%")
    pf, pm, k = preds[("goals", "EARLY")]
    n, roi, clv = money(pm[k], df.e_o.to_numpy()[k], df.e_u.to_numpy()[k], y[k], pc[k])
    print(f"  {'market-only':14s} n={n:5d}  ROI {100*roi:+6.2f}%  CLV {100*clv:+6.2f}%   (control, §75)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
