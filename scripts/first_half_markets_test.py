#!/usr/bin/env python3
"""Can we price the first-half markets? ([[#084]] part 2)

Implements docs/FIRST_HALF_MARKETS_2026_09_23.md, pre-registered before the first
run. BEFORE = the market alone (Pinnacle's de-vigged 1H price, and the "bet where a
book beats Pinnacle" strategy). AFTER = a model blended with that price.

Arms (no 1H market input in either):
  R  our walk-forward half-time ratings -> lambda_1H -> independent Poisson
  F  Pinnacle's FULL-TIME 1x2 + O/U 2.5 -> solved lambda_FT -> scaled by s
     (the first-half share of goals before the universe starts)

Harness: residual_test_ou.py's method — date-ordered universe, Platt (level) and
alpha fitted on the first half, scored on the second. 1x2_1h is 3-way: blended as a
vector, no Platt (multiclass) — the one stated deviation.

    python3 scripts/first_half_markets_test.py
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

from scripts.residual_test_ou import fit_platt, sig  # noqa: E402
from workers.model.devig import devig  # noqa: E402

load_dotenv()

MARKETS = {  # market -> (selections in fixed order, backtest?)
    "1x2_1h": (("home", "draw", "away"), True),
    "team_total_1h_home_05": (("over", "under"), True),
    "team_total_1h_away_05": (("over", "under"), True),
    "over_under_1h_05": (("over", "under"), False),
    "over_under_1h_15": (("over", "under"), False),
}
HOLM_M = 10
FLOORS = (0.03, 0.05, 0.08)
DECISION_MIN = 120
MAXG = 10


def q(sql, params=()):
    with psycopg2.connect(os.getenv("DATABASE_URL")) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ─── Poisson pricing ─────────────────────────────────────────────────────────

def pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def one_x_two(lh, la):
    ph = pd = pa = 0.0
    for i in range(MAXG):
        for j in range(MAXG):
            p = pmf(i, lh) * pmf(j, la)
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    s = ph + pd + pa
    return [ph / s, pd / s, pa / s]


def price(market, lh, la):
    """Model probabilities in the market's selection order."""
    if market == "1x2_1h":
        return one_x_two(lh, la)
    if market == "team_total_1h_home_05":
        p = 1 - math.exp(-lh)
    elif market == "team_total_1h_away_05":
        p = 1 - math.exp(-la)
    elif market == "over_under_1h_05":
        p = 1 - math.exp(-(lh + la))
    else:  # over_under_1h_15
        t = lh + la
        p = 1 - math.exp(-t) * (1 + t)
    return [p, 1 - p]


def solve_ft(p1x2, p_over25):
    """lambda_home, lambda_away reproducing de-vigged FT 1x2 + O/U 2.5."""
    def loss(x):
        lh, la = math.exp(x[0]), math.exp(x[1])
        m = one_x_two(lh, la)
        t = lh + la
        po = 1 - math.exp(-t) * (1 + t + t * t / 2)
        return (m[0] - p1x2[0]) ** 2 + (m[2] - p1x2[2]) ** 2 + (po - p_over25) ** 2
    r = minimize(loss, [math.log(1.4), math.log(1.1)], method="Nelder-Mead",
                 options={"xatol": 1e-4, "fatol": 1e-9, "maxiter": 400})
    return math.exp(r.x[0]), math.exp(r.x[1])


def outcome(market, hth, hta):
    if market == "1x2_1h":
        return 0 if hth > hta else (1 if hth == hta else 2)
    if market == "team_total_1h_home_05":
        return 0 if hth > 0.5 else 1
    if market == "team_total_1h_away_05":
        return 0 if hta > 0.5 else 1
    if market == "over_under_1h_05":
        return 0 if hth + hta > 0.5 else 1
    return 0 if hth + hta > 1.5 else 1


# ─── data ────────────────────────────────────────────────────────────────────

def snapshots(market_list, min_minutes=None):
    """{(match_id, market, bookmaker): {selection: odds}} — last complete pre-KO
    quote per book, optionally only quotes >= min_minutes before kickoff."""
    cond = "AND o.minutes_to_kickoff >= %s" if min_minutes else \
           "AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)"
    params = [list(market_list)] + ([min_minutes] if min_minutes else [])
    rows = q(f"""
        SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
               o.match_id::text mid, o.market, o.bookmaker, o.selection, o.odds::float od
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.market = ANY(%s) AND o.is_live IS NOT TRUE AND o.is_closing = false
           AND o.odds > 1.01 AND m.status = 'finished' AND m.date >= '2026-08-29'
           {cond}
         ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o.timestamp DESC""", params)
    out = defaultdict(dict)
    for r in rows:
        out[(r["mid"], r["market"], r["bookmaker"])][r["selection"]] = r["od"]
    return out


def pin_probs(snap, mid, market, sels):
    s = snap.get((mid, market, "Pinnacle"))
    if not s or any(x not in s for x in sels):
        return None
    return devig([s[x] for x in sels])


def ll_vec(p, y):
    return -math.log(max(min(p[y], 1 - 1e-9), 1e-9))


def main() -> int:
    ft_markets = ["1x2", "over_under_25"]
    closing = snapshots(list(MARKETS) + ft_markets)
    decision = snapshots(list(MARKETS) + ft_markets, DECISION_MIN)
    meta = {r["mid"]: r for r in q("""
        SELECT m.id::text mid, m.date, m.ht_score_home::int hth, m.ht_score_away::int hta,
               f.ht_expected_total t, f.ht_expected_diff d
          FROM matches m LEFT JOIN match_feature_vectors f ON f.match_id = m.id
         WHERE m.status='finished' AND m.date >= '2026-08-29' AND m.ht_score_home IS NOT NULL""")}
    sh = q("""SELECT sum(ht_score_home + ht_score_away)::float h, sum(score_home + score_away)::float f
                FROM matches WHERE status='finished' AND date < '2026-08-29'
                 AND ht_score_home IS NOT NULL AND score_home >= ht_score_home
                 AND score_away >= ht_score_away""")[0]
    s_share = sh["h"] / sh["f"]
    print(f"first-half share of goals before 2026-08-29: s = {s_share:.4f}\n")

    def lam(arm, mid, snap):
        m = meta[mid]
        if arm == "R":
            if m["t"] is None or m["d"] is None:
                return None
            t, d = float(m["t"]), float(m["d"])
            return max((t + d) / 2, 0.01), max((t - d) / 2, 0.01)
        p1 = pin_probs(snap, mid, "1x2", ("home", "draw", "away"))
        po = pin_probs(snap, mid, "over_under_25", ("over", "under"))
        if not p1 or not po:
            return None
        lh, la = solve_ft(p1, po[0])
        return s_share * lh, s_share * la

    results, bt_rows = {}, []
    print(f"{'market':24}{'arm':4}{'n':>6}{'alpha':>8}{'mkt LL':>9}{'model LL':>10}"
          f"{'blend LL':>10}{'p':>8}")
    for market, (sels, do_bt) in MARKETS.items():
        mids = sorted({k[0] for k in closing if k[1] == market},
                      key=lambda x: (meta[x]["date"] if x in meta else datetime.max))
        for arm in ("R", "F"):
            U = []
            for mid in mids:
                if mid not in meta:
                    continue
                pk = pin_probs(closing, mid, market, sels)
                L = lam(arm, mid, closing)
                if not pk or not L:
                    continue
                U.append((mid, pk, price(market, *L), outcome(market, meta[mid]["hth"], meta[mid]["hta"])))
            if len(U) < 200:
                print(f"{market:24}{arm:4}{len(U):6d}  too few")
                continue
            cut = len(U) // 2
            fit, te = U[:cut], U[cut:]
            if len(sels) == 2:
                a, b = fit_platt([(pm[0], 1.0 - y) for _, _, pm, y in fit])
                cal = lambda pm: [sig(a * pm[0] + b), 1 - sig(a * pm[0] + b)]  # noqa: E731
            else:
                cal = lambda pm: pm  # noqa: E731
            best = (1e9, 0.0)
            for i in range(201):
                al = i / 200
                v = mean(ll_vec([al * x + (1 - al) * k for x, k in zip(cal(pm), pk)], y)
                         for _, pk, pm, y in fit)
                best = min(best, (v, al))
            alpha = best[1]
            d = []
            for _, pk, pm, y in te:
                bl = [alpha * x + (1 - alpha) * k for x, k in zip(cal(pm), pk)]
                d.append(ll_vec(pk, y) - ll_vec(bl, y))
            l_mkt = mean(ll_vec(pk, y) for _, pk, _, y in te)
            l_mod = mean(ll_vec(cal(pm), y) for _, _, pm, y in te)
            l_bl = l_mkt - mean(d)
            z = mean(d) / (stdev(d) / math.sqrt(len(d))) if stdev(d) > 0 else 0.0
            p = 0.5 * math.erfc(z / math.sqrt(2)) if alpha > 0 else 1.0
            results[(market, arm)] = dict(n=len(te), alpha=alpha, l_mkt=l_mkt, l_mod=l_mod,
                                          l_bl=l_bl, p=p)
            print(f"{market:24}{arm:4}{len(te):6d}{alpha:8.3f}{l_mkt:9.4f}{l_mod:10.4f}"
                  f"{l_bl:10.4f}{p:8.3f}")
            if do_bt:
                bt_rows.append((market, arm, sels, te, alpha, cal))

    order = sorted(results, key=lambda k: results[k]["p"])
    adj, run = {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (HOLM_M - i) * results[k]["p"]))
        adj[k] = run
    print(f"\nVERDICT (alpha > 0.02 AND blend < market AND Holm p < 0.05, m={HOLM_M}):")
    for k, r in results.items():
        ok = r["alpha"] > 0.02 and r["l_bl"] < r["l_mkt"] and adj[k] < 0.05
        print(f"  {k[0]:24} {k[1]}  {'PASS' if ok else 'FAIL'}  alpha {r['alpha']:.3f}  Holm {adj[k]:.3f}")

    # ── backtest: T-2h, best price across ALL books, CLV vs Pinnacle close ────
    by_mm = defaultdict(list)                      # (match, market) -> [(book, quotes)]
    for (m_, mk, bk), v in decision.items():
        if bk != "Pinnacle":
            by_mm[(m_, mk)].append(v)
    print(f"\nBACKTEST — held-out half, decision T-{DECISION_MIN}min, best price across all "
          f"books, CLV vs de-vigged Pinnacle close")
    print(f"  {'market':24}{'strategy':10}{'floor':>6}{'bets':>6}{'/day':>6}{'CLV':>8}{'t':>6}{'ROI':>8}")
    for market, arm, sels, te, alpha, cal in bt_rows:
        for strat in ("MARKET", f"BLEND-{arm}"):
            if strat == "MARKET" and arm != "R":
                continue
            days = max(1, len({str(meta[mid]["date"])[:10] for mid, _, _, _ in te}))
            for fl in FLOORS:
                clv, roi = [], []
                for mid, pclose, pm, y in te:
                    pk = pin_probs(decision, mid, market, sels)
                    if not pk:
                        continue
                    if strat == "MARKET":
                        pr = pk
                    else:
                        L = lam(arm, mid, decision)
                        if not L:
                            continue
                        pr = [alpha * x + (1 - alpha) * k for x, k in zip(cal(price(market, *L)), pk)]
                    for i, sel in enumerate(sels):
                        quotes = [v[sel] for v in by_mm[(mid, market)] if sel in v]
                        if not quotes:
                            continue
                        od = max(quotes)
                        if pr[i] * od - 1 >= fl:
                            clv.append(pclose[i] * od - 1)
                            roi.append(od - 1 if y == i else -1.0)
                n = len(clv)
                t = mean(clv) / (stdev(clv) / math.sqrt(n)) if n > 2 and stdev(clv) > 0 else 0.0
                print(f"  {market:24}{strat:10}{fl:6.0%}{n:6d}{n/days:6.1f}"
                      f"{100*mean(clv) if n else 0:+8.2f}%{t:6.1f}{100*mean(roi) if n else 0:+7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
