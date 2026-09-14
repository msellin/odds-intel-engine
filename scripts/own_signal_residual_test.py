#!/usr/bin/env python3
"""Does ANY stored signal add to the OWN-BOOK market price, out of sample?

The 2026-09-14 residual test found alpha = 0.0000 for the 1X2 model. Its scope
was the MODEL'S BLENDED OUTPUT on 1X2 against Pinnacle. This script tests the
complementary question the brief leaves open: do the INDIVIDUAL signals we
store — injuries, lineups, rest, travel, xG, ELO, form, H2H — add anything to
the price at the book we would actually bet at?

BENCHMARK. The de-vigged (Shin) CLOSING price at one of the three EMTA-legal
self-scraped books, not Pinnacle. That is the strictest available own-book
benchmark: if a signal cannot beat the close it cannot beat the earlier price we
would trade against either, since the close is the more informative of the two.
A signal that DOES beat the close is then worth re-testing at decision time.

METHOD, per signal (market-anchored incremental test):
  TRAIN   fit the offset logistic  logit(p) = logit(p_market) + c*x
          — only c is free, so c is by construction the information the signal
          adds ON TOP OF the price, not information it shares with it.
  TEST    build p_aug = sigmoid(logit(p_market) + c_hat*x) on untouched later
          fixtures, then fit the blend weight alpha of
              alpha * p_aug + (1 - alpha) * p_market
          by log-loss, with a PROFILE likelihood CI on alpha. alpha = 0 is "adds
          nothing". Reported against the MARKET, never against a constant.

CONTROLS, run through the identical machinery:
  noise_gauss   a standard normal draw. Must come back at alpha ~ 0.
  market_self   the market's own implied probability as the signal. Must come
                back saturated — if it does not, the harness is broken.
  pseudo_clv_home   LEAKAGE CANARY. It is derived after the fact. If it lights
                up, the feature store is contaminated, and that is the finding.

    python3 scripts/own_signal_residual_test.py --book Coolbet
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from workers.api_clients.db import execute_query  # noqa: E402

EPS = 1e-9


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sig(z):
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def ll(ps, ys):
    return -sum(y * math.log(max(p, EPS)) + (1 - y) * math.log(max(1 - p, EPS))
                for p, y in zip(ps, ys)) / len(ys)


def shin(odds_list):
    q = [1.0 / o for o in odds_list]
    t = sum(q)
    z = 0.0
    for _ in range(80):
        zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
              for qi in q]
        s_ = sum(zs)
        z = z + (s_ - 1) * 0.5 if s_ > 1 else z - (1 - s_) * 0.5
        z = max(0.0, min(0.5, z))
    zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
          for qi in q]
    tot = sum(zs)
    return [x / tot for x in zs]


def fit_offset_c(xs, offs, ys, iters=200):
    """Newton on the single free coefficient of logit(p) = off + c*x."""
    c = 0.0
    for _ in range(iters):
        g = h = 0.0
        for x, o, y in zip(xs, offs, ys):
            p = sig(o + c * x)
            g += (y - p) * x
            h += p * (1 - p) * x * x
        if h <= 1e-12:
            break
        step = g / h
        c += step
        if abs(step) < 1e-10:
            break
    return c


def fit_alpha_profile(p_aug, p_mkt, ys, level=1.92):
    """alpha minimising test log-loss, with a PROFILE likelihood interval
    (the set of alpha whose total log-likelihood is within `level` = 1.92 nats,
    i.e. a chi-square(1) 95pct region, of the optimum)."""
    grid = [i / 400.0 for i in range(-400, 801)]      # alpha in [-1, 2]
    n = len(ys)
    best = (1e18, 0.0)
    curve = []
    for a in grid:
        ps = [min(max(a * pa + (1 - a) * pm, 1e-6), 1 - 1e-6)
              for pa, pm in zip(p_aug, p_mkt)]
        v = ll(ps, ys) * n
        curve.append((a, v))
        if v < best[0]:
            best = (v, a)
    lo = min((a for a, v in curve if v <= best[0] + level), default=float("nan"))
    hi = max((a for a, v in curve if v <= best[0] + level), default=float("nan"))
    return best[1], lo, hi, best[0] / n


SIGNALS_1X2 = {
    "elo_diff": lambda r: r["elo_diff"],
    "form_ppg_diff": lambda r: _d(r["form_ppg_home"], r["form_ppg_away"]),
    "form_momentum_diff": lambda r: _d(r["form_momentum_home"], r["form_momentum_away"]),
    "injury_count_diff": lambda r: _d(r["injury_count_home"], r["injury_count_away"]),
    "injury_severity_diff": lambda r: _d(r["injury_severity_home"], r["injury_severity_away"]),
    "injury_severity_score_diff": lambda r: _d(r["injury_severity_score_home"], r["injury_severity_score_away"]),
    "rest_days_diff": lambda r: _d(r["rest_days_home"], r["rest_days_away"]),
    "travel_km": lambda r: r["travel_km"],
    "xg_overperf_diff": lambda r: _d(r["xg_overperf_home"], r["xg_overperf_away"]),
    "player_rating_diff": lambda r: _d(r["team_avg_player_rating_home"], r["team_avg_player_rating_away"]),
    "league_position_diff": lambda r: _d(r["league_position_away"], r["league_position_home"]),
    "h2h_win_pct": lambda r: r["h2h_win_pct"],
    "lineup_confirmed": lambda r: (None if r["lineup_confirmed"] is None
                                   else (1.0 if r["lineup_confirmed"] else 0.0)),
    "news_impact_score": lambda r: r["news_impact_score"],
    "fixture_importance": lambda r: r["fixture_importance"],
    "bookmaker_disagreement": lambda r: r["bookmaker_disagreement"],
    "model_ensemble_home": lambda r: r["ensemble_prob_home"],
    "CANARY_pseudo_clv_home": lambda r: r["pseudo_clv_home"],
}

SIGNALS_OU = {
    "goals_for_sum": lambda r: _s(r["goals_for_avg_home"], r["goals_for_avg_away"]),
    "goals_against_sum": lambda r: _s(r["goals_against_avg_home"], r["goals_against_avg_away"]),
    "injury_count_total": lambda r: _s(r["injury_count_home"], r["injury_count_away"]),
    "rest_days_min": lambda r: (None if r["rest_days_home"] is None
                                or r["rest_days_away"] is None
                                else min(float(r["rest_days_home"]), float(r["rest_days_away"]))),
    "weather_rain_mm": lambda r: r["weather_rain_mm"],
    "weather_wind_kmh": lambda r: r["weather_wind_kmh"],
    "weather_temp_c": lambda r: r["weather_temp_c"],
    "referee_over25_pct": lambda r: r["referee_over25_pct"],
    "league_draw_rate_ytd": lambda r: r["league_draw_rate_ytd"],
    "ou25_bookmaker_disagreement": lambda r: r["ou25_bookmaker_disagreement"],
    "elo_gap_abs": lambda r: (None if r["elo_diff"] is None else abs(float(r["elo_diff"]))),
    "travel_km": lambda r: r["travel_km"],
    "lineup_confirmed": lambda r: (None if r["lineup_confirmed"] is None
                                   else (1.0 if r["lineup_confirmed"] else 0.0)),
    "model_ensemble_over25": lambda r: r["pinnacle_implied_over25"],
}


def _d(a, b):
    return None if a is None or b is None else float(a) - float(b)


def _s(a, b):
    return None if a is None or b is None else float(a) + float(b)


def haversine(a_lat, a_lng, b_lat, b_lng):
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    r = 6371.0
    p1, p2 = math.radians(float(a_lat)), math.radians(float(b_lat))
    dl = math.radians(float(b_lng) - float(a_lng))
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def load(book, market):
    sides = ("home", "draw", "away") if market == "1x2" else ("over", "under")
    rows = execute_query(
        """
        WITH cl AS (
          SELECT DISTINCT ON (o.match_id, o.selection)
                 o.match_id, o.selection, o.odds::float AS od
            FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
           WHERE o.bookmaker = %s AND o.market = %s
             AND COALESCE(o.is_live, FALSE) = FALSE
             AND o.timestamp <= m.date AND o.odds > 1.01
             AND m.status = 'finished' AND m.score_home IS NOT NULL
           ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT m.id::text AS match_id, m.date, m.score_home, m.score_away,
               ht.stadium_lat AS h_lat, ht.stadium_lng AS h_lng,
               at.stadium_lat AS a_lat, at.stadium_lng AS a_lng,
               f.*,
               (SELECT json_object_agg(selection, od) FROM cl
                 WHERE cl.match_id = m.id) AS quotes
          FROM matches m
          JOIN match_feature_vectors f ON f.match_id = m.id
          LEFT JOIN teams ht ON ht.id = m.home_team_id
          LEFT JOIN teams at ON at.id = m.away_team_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND EXISTS (SELECT 1 FROM cl WHERE cl.match_id = m.id)
         ORDER BY m.date
        """,
        (book, market),
    )
    out = []
    for r in rows:
        q = r["quotes"] or {}
        if not all(s in q and q[s] and q[s] > 1.01 for s in sides):
            continue
        ps = shin([q[s] for s in sides])
        r["p_market"] = ps[0]           # home  /  over
        r["y"] = (1 if (market == "1x2" and r["score_home"] > r["score_away"])
                  else 1 if (market != "1x2"
                             and (r["score_home"] + r["score_away"]) > 2.5)
                  else 0)
        r["travel_km"] = haversine(r["h_lat"], r["h_lng"], r["a_lat"], r["a_lng"])
        out.append(r)
    return out


def bh_threshold(ps, q=0.10):
    s = sorted(p for p in ps if p == p)
    thr = 0.0
    for i, p in enumerate(s, 1):
        if p <= q * i / len(s):
            thr = p
    return thr


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="Coolbet")
    ap.add_argument("--train-frac", type=float, default=0.65)
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    for market, sigs, tgt in (("1x2", SIGNALS_1X2, "home win"),
                              ("over_under_25", SIGNALS_OU, "over 2.5")):
        rows = load(a.book, market)
        if len(rows) < 200:
            print(f"\n{a.book} {market}: n={len(rows)} — too few, skipped")
            continue
        for r in rows:
            r["noise_gauss"] = rnd.gauss(0, 1)
        cut = int(len(rows) * a.train_frac)
        tr, te = rows[:cut], rows[cut:]
        ds = [r["date"].date() for r in rows]
        print(f"\n{'='*100}")
        print(f"{a.book} — {market} — target: {tgt}")
        print(f"  n = {len(rows)} fixtures   DATE SPAN {ds[0]} .. {ds[-1]}")
        print(f"  TRAIN {ds[0]}..{tr[-1]['date'].date()} (n={len(tr)})   "
              f"TEST {te[0]['date'].date()}..{ds[-1]} (n={len(te)})")
        base = mean_y = sum(r["y"] for r in rows) / len(rows)
        mk_te = [r["p_market"] for r in te]
        ys_te = [r["y"] for r in te]
        print(f"  base rate {base:.4f}   market log-loss on TEST "
              f"{ll(mk_te, ys_te):.5f}")
        print(f"\n  {'signal':32s} {'cov':>6s} {'c_hat':>9s} {'alpha_oos':>9s} "
              f"{'profile 95pct CI':>20s} {'dLL_fixed':>10s} {'dLL_fit':>9s} "
              f"{'p_IS':>8s}")

        allsig = dict(sigs)
        allsig["noise_gauss"] = lambda r: r["noise_gauss"]
        allsig["market_self"] = lambda r: logit(r["p_market"])
        results = []
        for name, fn in allsig.items():
            trv = [(fn(r), r) for r in tr]
            trv = [(float(v), r) for v, r in trv if v is not None]
            tev = [(fn(r), r) for r in te]
            tev = [(float(v), r) for v, r in tev if v is not None]
            cov = len(trv) / len(tr) if tr else 0
            if len(trv) < 150 or len(tev) < 100:
                print(f"  {name:32s} {cov*100:5.0f}%   -- coverage too thin")
                continue
            mu = sum(v for v, _ in trv) / len(trv)
            sd = (sum((v - mu) ** 2 for v, _ in trv) / len(trv)) ** 0.5 or 1.0
            xs = [(v - mu) / sd for v, _ in trv]
            offs = [logit(r["p_market"]) for _, r in trv]
            ys = [r["y"] for _, r in trv]
            c = fit_offset_c(xs, offs, ys)
            # in-sample Wald p on c
            h = sum(sig(o + c * x) * (1 - sig(o + c * x)) * x * x
                    for x, o in zip(xs, offs))
            se_c = (1.0 / h) ** 0.5 if h > 0 else float("nan")
            p_is = 2 * (1 - norm_cdf(abs(c / se_c))) if se_c == se_c and se_c else float("nan")
            xte = [(v - mu) / sd for v, _ in tev]
            pm = [r["p_market"] for _, r in tev]
            yte = [r["y"] for _, r in tev]
            paug = [sig(logit(p) + c * x) for p, x in zip(pm, xte)]
            al, alo, ahi, llb = fit_alpha_profile(paug, pm, yte)
            # `d` fits alpha ON the test set, so it can never be negative and is
            # NOT the out-of-sample gain. `d_fixed` is: c comes from TRAIN and
            # alpha is pinned at 1, so nothing is fitted on the test fixtures.
            # Read d_fixed for the verdict; alpha and its profile CI answer the
            # brief's "fit a blend weight against the market" separately.
            d = ll(pm, yte) - llb
            d_fixed = ll(pm, yte) - ll(paug, yte)
            results.append((name, cov, c, al, alo, ahi, d, p_is, d_fixed))
            print(f"  {name:32s} {cov*100:5.0f}% {c:+9.4f} {al:+9.3f} "
                  f"[{alo:+6.2f},{ahi:+6.2f}] {d_fixed:+10.5f} {d:+9.5f} {p_is:8.4f}")

        real = [r for r in results if not r[0].startswith(("noise", "market_self"))]
        thr = bh_threshold([r[7] for r in real])
        print(f"\n  signals tested (excluding controls): {len(real)}   "
              f"BH-FDR q=0.10 threshold p<={thr:.5f}")
        win = [r for r in real if r[3] > 0.02 and r[4] > 0 and r[8] > 0]
        print(f"  ADDS OOS (alpha>0.02, profile CI excludes 0, and dLL_fixed>0 "
              f"-- nothing fitted on test): {len(win)}")
        for r in win:
            print(f"     {r[0]:32s} alpha={r[3]:+.3f} [{r[4]:+.2f},{r[5]:+.2f}] "
                  f"dLL_fixed={r[8]:+.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
