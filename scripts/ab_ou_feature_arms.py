#!/usr/bin/env python3
"""O/U FEATURE-SET ARMS ([[#089]]) — does a SMALL, SUM-shaped vector beat the 52?

Pre-registration: `dev/active/per-market-feature-sets-design.md`, section
"Pre-registration — O/U arms (2026-09-23)". Read it before reading a number
this prints. The pass bar, the family, the correction and the expected result
are all written there BEFORE the first run, and this script implements that
text — not the other way round.

THE ARMS
--------
    A   the shipped 52-feature vector (control; carries market prices)
    A0  A minus every market-derived input — isolates the "no prices in the
        model we test" rule from the reshaping, so C is not credited with an
        effect that is really just dropping Pinnacle's own price
    C   ~11 SUM-shaped features, goals-fed, no market input
    CM  C + Pinnacle's own prices — the owner's question, asked on the SMALL
        vector: does handing the model the market price make it better or
        just make it a copy of the market? (A vs A0 asks the same on the 52.)
    D   shots-fed sum-shaped set — NOT RUN until the [[#078]] columns backfill
        finishes (inside/outside-box split); running it twice would cost more
        than waiting
    E   C with D's shots terms where shots exist — same gate as D

The family is FIXED at all six arms from the first run, so the Holm correction
(m = 6) does not get easier as arms are added later.

TWO QUESTIONS, TWO MEASUREMENTS — AND WHY α ALONE CANNOT SETTLE "PRICES IN?"
------------------------------------------------------------------------------
(a) IS THE MODEL CORRECT — does it know something the market does not? That is
    α (blend weight vs de-vigged Pinnacle), plus model-alone log-loss. For an
    arm that was HANDED Pinnacle's price (A, CM), α is partly the market
    scoring itself, so read A-vs-A0 and CM-vs-C side by side rather than any
    one α in isolation.
(b) DOES IT MAKE MONEY — a betting backtest on the held-out half, built the
    way we would actually bet (see `backtest()`):
      * decision at T-2h: Pinnacle's and Coolbet's last price >= 120 min
        before kickoff — never a price we could not have seen
      * executable price = COOLBET ONLY (ANALYSIS_GOTCHAS §55: best-of-books
        re-creates the line-shop artifact and biases ROI)
      * judged on de-vigged Pinnacle CLOSING-line value (§8: ~222x fewer bets
        than ROI for the same precision), ROI and bets/day reported beside it
      * three strategies per arm: BLEND (α·model + (1-α)·market — what the
        pre-registration would ship), RAW (model alone — what today's
        v10_ou-style bot does), and MARKET (α = 0, the sharp-anchor baseline
        every model strategy must beat)
    ⚠️ Market-price FEATURES on A/CM come from `match_signals` captured at an
    unrecorded time, possibly after T-2h — so A/CM's backtest may be
    optimistic. That caveat is printed, not buried.

WHY THE SCORING IS IN THIS SCRIPT RATHER THAN A SUBPROCESS
----------------------------------------------------------
`residual_test_ou.py` reads features straight from `match_feature_vectors`.
Arm C's inputs are DERIVED (a walk-forward league goal rate, lambdas split out
of the stored half-time ratings) and have no column there. So this script
scores in-process with the harness's OWN functions (ll, auc, fit_platt,
fit_alpha, devig_two_way, shin2, ou_target, POST_HOC) on the harness's OWN
universe query — and PROVES it is the same harness before trusting it:

    CHECK R  arm A scored in-process with the harness's zero-fill must reproduce
             `residual_test_ou.py --bundle <A>` run as a subprocess, to 4 dp on
             alpha, market log-loss and model log-loss. If it does not, nothing
             below is comparable to any earlier α and the run aborts.

THE ONE DELIBERATE DIFFERENCE FROM THE HARNESS — IMPUTATION
-----------------------------------------------------------
`residual_test_ou.build_X` fills a missing feature with 0.0. Training fills it
with the league/global MEAN. For the 52-vector that mismatch was inherited and
survivable; for arm C it is fatal — `exp_total = 0` reads as "a match nobody
scores in", and 29% of rows lack the rating. Scoring C with zero-fill would
measure the imputation, not the feature set. So the DECIDING numbers use the
training mean for EVERY arm (saved into each bundle), and arm A is also
reported with zero-fill so the size of that choice is visible, not hidden.

Usage:
    python3 scripts/ab_ou_feature_arms.py --cutoff 2026-08-20
    python3 scripts/ab_ou_feature_arms.py --cutoff 2026-08-20 --arms A,A0,C
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from scripts.residual_test_ou import (  # noqa: E402
    POST_HOC, auc, devig_two_way, fit_alpha, fit_platt, ll, ou_target, shin2, sig,
)
from workers.model.train import (  # noqa: E402
    FEATURE_COLS, HALFTIME_FEATURE_COLS, OU_MARKET_FEATURE_COLS,
    PINNACLE_FEATURE_COLS, load_training_data, train_over25_model,
)

load_dotenv()

ROOT = Path("data/models/soccer")
SHIPPED = "data/models/soccer/v20260914_clean_cut0820"
FAMILY = ("A", "A0", "C", "CM", "D", "E")          # fixed — see module docstring
HOLM_M = len(FAMILY)
ALPHA_BAR = 0.02
P_BAR = 0.05
LEAGUE_HALF_LIFE = 300.0                      # a choice; W&S 2021's 300 d is a shot-conversion decay, not this

# Every input that IS a market price or is computed from one. Dropped for A0.
MARKET_DERIVED = (
    ["opening_implied_home", "opening_implied_draw", "opening_implied_away",
     "bookmaker_disagreement", "line_velocity", "league_clv_efficiency"]
    + PINNACLE_FEATURE_COLS + OU_MARKET_FEATURE_COLS
)

ARM_C = [
    "exp_total",          # lam_home + lam_away — THE sum term the vector never had
    "ht_share_expected",  # when the goals arrive (stored, walk-forward, #084)
    "lam_min", "lam_max", # BTTS-ish shape of the total; min = the weaker attack
    "elo_sum", "elo_absdiff",  # lopsided matches score differently, sign-free
    "league_goals_wf", "league_over25_wf",  # walk-forward, decayed, per league
    "rest_days_home", "rest_days_away",
    "league_tier",
]


SHOT_STATS = {   # feature -> (home column, away column) in match_stats
    "exp_shots_total":     ("shots_home", "shots_away"),
    "exp_sot_total":       ("shots_on_target_home", "shots_on_target_away"),
    "exp_offtarget_total": ("shots_off_target_home", "shots_off_target_away"),
    "exp_insidebox_total": ("shots_insidebox_home", "shots_insidebox_away"),
    "exp_corners_total":   ("corners_home", "corners_away"),
}
SHOT_COLS = list(SHOT_STATS)
ARM_D = SHOT_COLS + ["league_goals_wf", "league_over25_wf",
                     "rest_days_home", "rest_days_away", "league_tier"]
ARM_E = list(ARM_C) + SHOT_COLS
SEED_DAYS = 180
MIN_TEAM_MATCHES = 5


# ─── derived features ────────────────────────────────────────────────────────

def shots_walk_forward(half_life: float = LEAGUE_HALF_LIFE) -> pd.DataFrame:
    """Per-match expected TOTALS of shots / SOT / off target / inside box / corners
    (λ_home + λ_away), leak-free: `HalfRatings` (the #084 construction — IPF seed
    fit on the oldest SEED_DAYS of stats, then predict-then-update in date order),
    one rating per statistic. Every finished match gets a prediction when both
    teams have >= MIN_TEAM_MATCHES stats matches behind them; only matches WITH
    stats update the ratings. Same-date matches are all predicted before any of
    them updates (the Saturday leak)."""
    from scripts.build_half_time_ratings import HalfRatings
    stats = {r["id"]: r for r in _q("""
        SELECT m.id::text id, ms.* FROM match_stats ms JOIN matches m ON m.id = ms.match_id
         WHERE m.status = 'finished'""")}
    ms = _q("""SELECT id::text id, date::date d, home_team_id::text h, away_team_id::text a
                 FROM matches WHERE status = 'finished' AND score_home IS NOT NULL
                ORDER BY date, id""")
    if not stats or not ms:
        return pd.DataFrame(columns=["match_id"] + SHOT_COLS)
    first = min(r["d"] for r in ms if r["id"] in stats)
    from datetime import timedelta as _td
    seed_end = first + _td(days=SEED_DAYS)
    out = {r["id"]: {} for r in ms}
    for feat, (kh, ka) in SHOT_STATS.items():
        seed = [dict(id=r["id"], d=r["d"], h=r["h"], a=r["a"],
                     x=float(stats[r["id"]][kh]), y=float(stats[r["id"]][ka]))
                for r in ms if r["d"] < seed_end and r["id"] in stats
                and stats[r["id"]].get(kh) is not None and stats[r["id"]].get(ka) is not None]
        R = HalfRatings(feat)
        R.fit(seed, "x", "y", half_life)
        seen = defaultdict(int)
        for r in seed:
            seen[r["h"]] += 1; seen[r["a"]] += 1
        day = [r for r in ms if r["d"] >= seed_end]
        i = 0
        while i < len(day):
            j = i
            while j < len(day) and day[j]["d"] == day[i]["d"]:
                j += 1
            batch = day[i:j]
            for r in batch:                                   # 1. predict
                if seen[r["h"]] >= MIN_TEAM_MATCHES and seen[r["a"]] >= MIN_TEAM_MATCHES:
                    eh, ea = R.predict(r["h"], r["a"])
                    out[r["id"]][feat] = eh + ea
            for r in batch:                                   # 2. THEN update
                st = stats.get(r["id"])
                if st and st.get(kh) is not None and st.get(ka) is not None:
                    R.update(r["h"], r["a"], float(st[kh]), float(st[ka]))
                    seen[r["h"]] += 1; seen[r["a"]] += 1
            i = j
    return pd.DataFrame([{"match_id": k, **v} for k, v in out.items()],
                        columns=["match_id"] + SHOT_COLS)


def league_walk_forward(half_life: float = LEAGUE_HALF_LIFE) -> pd.DataFrame:
    """Per-match decayed league mean of total goals and over-2.5 rate, built
    ONLY from that league's matches on EARLIER dates.

    Same-date matches are all READ before any of them is ADDED, so a Saturday
    fixture is never scored by another Saturday result. That is the leak a
    naive row-by-row walk would carry.
    """
    rows = _q("""SELECT m.id, m.league_id, m.date::date d,
                        (m.score_home + m.score_away)::float tg
                   FROM matches m
                  WHERE m.status='finished' AND m.score_home IS NOT NULL
                    AND m.score_away IS NOT NULL AND m.league_id IS NOT NULL
                  ORDER BY m.date, m.id""")
    S, O, W, last = defaultdict(float), defaultdict(float), defaultdict(float), {}
    out = []
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]["d"] == rows[i]["d"]:
            j += 1
        day = rows[i:j]
        for r in day:                                   # 1. decay + READ
            lg = r["league_id"]
            if lg in last:
                f = 0.5 ** ((r["d"] - last[lg]).days / half_life)
                S[lg] *= f; O[lg] *= f; W[lg] *= f
                last[lg] = r["d"]
            ok = W[lg] >= 5.0                           # ~5 effective matches
            out.append((r["id"], S[lg] / W[lg] if ok else None,
                        O[lg] / W[lg] if ok else None))
        for r in day:                                   # 2. THEN add
            lg = r["league_id"]
            last.setdefault(lg, r["d"])
            S[lg] += r["tg"]; O[lg] += 1.0 if r["tg"] > 2.5 else 0.0; W[lg] += 1.0
        i = j
    return pd.DataFrame(out, columns=["match_id", "league_goals_wf", "league_over25_wf"])


def add_derived(df: pd.DataFrame, lwf: pd.DataFrame, swf: pd.DataFrame | None = None) -> pd.DataFrame:
    """Arm C's columns from stored columns. `df` must carry match_id.

    lam_home/lam_away are recovered EXACTLY from the stored #084 ratings:
        sum  = ht_expected_total + h2_expected_total = lam_h + lam_a
        diff = ht_expected_diff  + h2_expected_diff  = lam_h - lam_a
    Reusing them rather than refitting is deliberate — a second implementation
    of a rating is a second definition of it (build_half_time_ratings.py).
    """
    d = df.copy()
    s = pd.to_numeric(d["ht_expected_total"], errors="coerce") + \
        pd.to_numeric(d["h2_expected_total"], errors="coerce")
    df_ = pd.to_numeric(d["ht_expected_diff"], errors="coerce") + \
        pd.to_numeric(d["h2_expected_diff"], errors="coerce")
    lh, la = (s + df_) / 2, (s - df_) / 2
    d["exp_total"] = s
    d["lam_min"] = np.minimum(lh, la)
    d["lam_max"] = np.maximum(lh, la)
    eh = pd.to_numeric(d["elo_home"], errors="coerce")
    ea = pd.to_numeric(d["elo_away"], errors="coerce")
    d["elo_sum"] = eh + ea
    d["elo_absdiff"] = (eh - ea).abs()
    d = d.drop(columns=[c for c in ("league_goals_wf", "league_over25_wf") if c in d])
    d = d.merge(lwf, on="match_id", how="left")
    if swf is not None:
        d = d.drop(columns=[c for c in SHOT_COLS if c in d]).merge(swf, on="match_id", how="left")
    return d


# ─── training ────────────────────────────────────────────────────────────────

def arm_cols(arm: str) -> list[str]:
    a = FEATURE_COLS + PINNACLE_FEATURE_COLS + OU_MARKET_FEATURE_COLS
    if arm == "A":
        return a
    if arm == "A0":
        return [c for c in a if c not in MARKET_DERIVED]
    if arm == "C":
        return list(ARM_C)
    if arm == "CM":
        return list(ARM_C) + PINNACLE_FEATURE_COLS + ["pinnacle_implied_over25",
                                                      "pinnacle_implied_under25"]
    if arm == "D":
        return list(ARM_D)
    if arm == "E":
        return list(ARM_E)
    raise SystemExit(f"unknown arm {arm}")


def train_arm(arm, feats, targs, tag):
    cols = arm_cols(arm)
    out = ROOT / f"{tag}_{arm}"
    # HARNESS REPAIR (#089, 2026-09-23): train on EVERY country. The default
    # excludes OU_EXCLUDED_LEAGUE_COUNTRIES (train.py), but the arms are SCORED on
    # all fixtures — 27% of the test set came from countries no arm trained on.
    model = train_over25_model(feats[cols].copy(), targs.copy(), out,
                               exclude_tier_c_countries=False)
    fcols = list(model.get_booster().feature_names)
    means = {c: float(pd.to_numeric(feats[c], errors="coerce").mean()) for c in cols}
    joblib.dump(fcols, out / "feature_cols.pkl")
    joblib.dump(means, out / "impute_means.pkl")
    return out


# ─── scoring (the harness, in-process) ───────────────────────────────────────

def _q(sql, params=()):
    with psycopg2.connect(os.getenv("DATABASE_URL")) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def universe(cutoff: str, feature_names: list[str]) -> list[dict]:
    """residual_test_ou.py's query, verbatim in its filters, plus m.id."""
    rows = _q("SELECT column_name FROM information_schema.columns "
              "WHERE table_name='match_feature_vectors'")
    have = {r["column_name"] for r in rows}
    real = sorted({x for x in feature_names if x in have} | {"match_id"})
    return _q(f"""
        WITH pin AS (SELECT DISTINCT ON (o.match_id, o.selection)
                            o.match_id, o.selection, o.odds::float od
                       FROM odds_snapshots o
                      WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
                        AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
                        AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT {", ".join(f'mfv."{x}"' for x in real)},
               (m.score_home + m.score_away) AS total_goals, m.date,
               po.od po, pu.od pu,
               sh.v AS pinnacle_implied_home, sd.v AS pinnacle_implied_draw,
               sa.v AS pinnacle_implied_away
          FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_home'
                              ORDER BY captured_at DESC LIMIT 1) sh ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_draw'
                              ORDER BY captured_at DESC LIMIT 1) sd ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_away'
                              ORDER BY captured_at DESC LIMIT 1) sa ON true
          JOIN pin po ON po.match_id = m.id AND po.selection='over'
          JOIN pin pu ON pu.match_id = m.id AND pu.selection='under'
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.score_away IS NOT NULL AND m.date >= %s
         ORDER BY m.date""", (cutoff,))


def build_X(rows, fcols, null_post_hoc, fill, means):
    """`fill='zero'` is residual_test_ou.build_X exactly (CHECK R);
    `fill='mean'` differs ONLY in what a missing value becomes."""
    def val(r, cl):
        if cl.endswith("_missing"):
            base = cl[:-8]
            if null_post_hoc and base in POST_HOC:
                return 1.0
            return 1.0 if r.get(base) is None else 0.0
        v = None if (null_post_hoc and cl in POST_HOC) else r.get(cl)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0 if fill == "zero" else float(means.get(cl, 0.0) or 0.0)
        return float(v)
    return np.array([[val(r, cl) for cl in fcols] for r in rows])


def score(bundle: Path, rows, fill: str):
    fcols = joblib.load(bundle / "feature_cols.pkl")
    model = joblib.load(bundle / "over_under.pkl")
    means = joblib.load(bundle / "impute_means.pkl") if (bundle / "impute_means.pkl").exists() else {}
    idx = list(model.classes_).index(1)
    ys = [ou_target(r["total_goals"], 2.5) for r in rows]
    pk = [devig_two_way(r["po"], r["pu"]) for r in rows]
    pk_shin = [shin2(r["po"], r["pu"]) for r in rows]
    cut = len(rows) // 2
    out = {}
    for arm, null_ph, mkt in (("OPTIMISTIC", False, pk), ("REALISTIC", True, pk),
                              ("REALISTIC+SHIN", True, pk_shin)):
        raw = [float(p[idx]) for p in model.predict_proba(
            build_X(rows, fcols, null_ph, fill, means))]
        a, b = fit_platt(list(zip(raw[:cut], [float(y) for y in ys[:cut]])))
        pm = [sig(a * p + b) for p in raw]
        alpha = fit_alpha(pm[:cut], mkt[:cut], ys[:cut])
        te = slice(cut, None)
        bl = [alpha * m + (1 - alpha) * k for m, k in zip(pm[te], mkt[te])]
        # per-fixture log-loss improvement of blend over market -> one-sided p
        e = 1e-9
        def l1(p, y):
            p = max(min(p, 1 - e), e)
            return -(y * math.log(p) + (1 - y) * math.log(1 - p))
        d = [l1(k, y) - l1(q, y) for k, q, y in zip(mkt[te], bl, ys[te])]
        md, sd = mean(d), (np.std(d, ddof=1) if len(d) > 1 else 0.0)
        z = md / (sd / math.sqrt(len(d))) if sd > 0 else 0.0
        p_one = 0.5 * math.erfc(z / math.sqrt(2))
        out[arm] = dict(alpha=alpha, l_mkt=ll(mkt[te], ys[te]), l_mod=ll(pm[te], ys[te]),
                        l_bl=ll(bl, ys[te]), auc_mod=auc(pm[te], ys[te]),
                        auc_mkt=auc(mkt[te], ys[te]),
                        resid=auc([m - k for m, k in zip(pm[te], mkt[te])], ys[te]),
                        p=p_one if alpha > 0 else 1.0, platt=(alpha, a, b),
                        ece=ece(pm[te], ys[te]))
    return out


def ece(ps, ys, bins: int = 10) -> float:
    """Expected calibration error, 10 equal-width bins (reported, not a bar)."""
    tot, n = 0.0, len(ps)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            tot += len(idx) / n * abs(mean(ps[i] for i in idx) - mean(ys[i] for i in idx))
    return tot


def harness_subprocess(bundle: Path, cutoff: str) -> dict:
    """Run residual_test_ou.py itself and parse the REALISTIC arm."""
    txt = subprocess.run([sys.executable, "scripts/residual_test_ou.py", "--line", "25",
                          "--cutoff", cutoff, "--bundle", str(bundle)],
                         capture_output=True, text=True).stdout
    blk = txt.split("REALISTIC  (post-hoc NULLed)")[1].split("-- REALISTIC + SHIN")[0]
    g = lambda pat: float(re.search(pat, blk).group(1))  # noqa: E731
    return dict(alpha=g(r"fitted alpha = ([\d.]+)"),
                l_mkt=g(r"market alone\s+log-loss ([\d.]+)"),
                l_mod=g(r"model alone\s+log-loss ([\d.]+)"))


DECISION_MIN = 120
FLOORS = (0.03, 0.05, 0.08)


PIN_FRESH_MIN = 30      # Pinnacle's decision price no older than this vs the Coolbet quote


def decision_prices(match_ids) -> dict:
    """{match_id: {pin_o, pin_u, cb_o, cb_u, close_o, close_u}} — the decision at
    >= DECISION_MIN before kickoff, and Pinnacle's close.

    HARNESS REPAIR (#089, 2026-09-23, internal review):
      * the close is STRICTLY before kickoff — `is_closing` rows used to bypass the
        pre-kickoff check, and 40% of them are stamped at/after kickoff, so 11% of
        closes were in-play prices;
      * the decision is a COOLBET quote, and Pinnacle's price must be within
        PIN_FRESH_MIN of it — "latest Pinnacle >= 120 min out" was a median ~11 h
        old, because Pinnacle O/U is captured ~4 times per match."""
    ids = list(match_ids)
    rows = _q("""
        WITH cb AS (SELECT DISTINCT ON (o.match_id, o.selection)
                           o.match_id, o.selection, o.odds::float od, o.timestamp ts
                      FROM odds_snapshots o
                     WHERE o.match_id = ANY(%s::uuid[]) AND o.market='over_under_25'
                       AND o.bookmaker='Coolbet' AND o.is_live IS NOT TRUE
                       AND o.odds > 1.01 AND o.minutes_to_kickoff >= %s
                     ORDER BY o.match_id, o.selection, o.timestamp DESC),
             pin AS (SELECT o.match_id, o.selection, o.odds::float od, o.timestamp ts
                       FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                      WHERE o.match_id = ANY(%s::uuid[]) AND o.market='over_under_25'
                        AND o.bookmaker='Pinnacle' AND o.is_live IS NOT TRUE
                        AND o.odds > 1.01 AND o.timestamp < m.date),
             d AS (SELECT DISTINCT ON (cb.match_id, cb.selection)
                          cb.match_id, cb.selection, cb.od cb_od, p.od pin_od
                     FROM cb JOIN pin p ON p.match_id = cb.match_id AND p.selection = cb.selection
                      AND p.ts <= cb.ts AND cb.ts - p.ts <= make_interval(mins => %s)
                    ORDER BY cb.match_id, cb.selection, p.ts DESC),
             c AS (SELECT DISTINCT ON (match_id, selection) match_id, selection, od
                     FROM pin ORDER BY match_id, selection, ts DESC)
        SELECT 'd' k, match_id::text m, selection s, cb_od, pin_od, NULL::float od FROM d
        UNION ALL SELECT 'c', match_id::text, selection, NULL, NULL, od FROM c""",
             (ids, DECISION_MIN, ids, PIN_FRESH_MIN))
    out = defaultdict(dict)
    for r in rows:
        sfx = "_o" if r["s"] == "over" else "_u"
        if r["k"] == "d":
            out[r["m"]]["cb" + sfx] = r["cb_od"]
            out[r["m"]]["pin" + sfx] = r["pin_od"]
        else:
            out[r["m"]]["close" + sfx] = r["od"]
    need = ("pin_o", "pin_u", "cb_o", "cb_u", "close_o", "close_u")
    return {m: v for m, v in out.items() if all(k in v for k in need)}


def backtest(bundle: Path, rows, prices, alpha_platt):
    """Held-out half only. alpha_platt = (alpha, a, b) fitted on the FIRST half
    by score() — nothing here is fitted on the rows it bets."""
    alpha, pa, pb = alpha_platt
    fcols = joblib.load(bundle / "feature_cols.pkl")
    model = joblib.load(bundle / "over_under.pkl")
    means = joblib.load(bundle / "impute_means.pkl")
    idx = list(model.classes_).index(1)
    test = rows[len(rows) // 2:]
    test = [r for r in test if str(r["match_id"]) in prices]
    if not test:
        return {}
    raw = model.predict_proba(build_X(test, fcols, True, "mean", means))[:, idx]
    days = max(1, len({str(r["date"])[:10] for r in test}))
    res = {}
    for strat in ("BLEND", "RAW", "MARKET"):
        for fl in FLOORS:
            clv, roi = [], []
            for r, rp in zip(test, raw):
                px = prices[str(r["match_id"])]
                pm = sig(pa * float(rp) + pb)
                pk = devig_two_way(px["pin_o"], px["pin_u"])
                p = {"BLEND": alpha * pm + (1 - alpha) * pk, "RAW": pm, "MARKET": pk}[strat]
                pc = devig_two_way(px["close_o"], px["close_u"])
                won_over = ou_target(r["total_goals"], 2.5) == 1
                for sel, ps, odd, pcl, won in (("o", p, px["cb_o"], pc, won_over),
                                               ("u", 1 - p, px["cb_u"], 1 - pc, not won_over)):
                    if ps * odd - 1 >= fl:
                        clv.append(pcl * odd - 1)
                        roi.append(odd - 1 if won else -1.0)
            n = len(clv)
            t = (mean(clv) / (np.std(clv, ddof=1) / math.sqrt(n))) if n > 2 and np.std(clv) > 0 else 0.0
            res[(strat, fl)] = dict(n=n, per_day=n / days, clv=mean(clv) if n else 0.0,
                                    t=t, roi=mean(roi) if n else 0.0)
    return res


def holm(ps: dict[str, float], m: int) -> dict[str, float]:
    """Holm step-down adjusted p over a family of size m (untested arms count)."""
    order = sorted(ps.items(), key=lambda kv: kv[1])
    adj, run = {}, 0.0
    for i, (k, p) in enumerate(order):
        run = max(run, min(1.0, (m - i) * p))
        adj[k] = run
    return adj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-08-20")
    ap.add_argument("--arms", default="A,A0,C,CM,D,E")
    ap.add_argument("--tag", default="ab_ou")
    a = ap.parse_args()
    arms = [x.strip() for x in a.arms.split(",")]
    assert set(arms) <= set(FAMILY), f"arms outside the pre-registered family: {arms}"

    print(f"[[#089]] O/U FEATURE-SET ARMS — cutoff {a.cutoff}, arms {arms}, "
          f"family m={HOLM_M} (Holm)\n")

    # ── load once, derive once, train every arm in the same minute ──────────
    feats, targs = load_training_data(include_pinnacle=True, include_ou_market=True,
                                      include_halftime=True, cutoff_date=a.cutoff)
    assert "match_id" in targs, "load_training_data must carry match_id (see train.py)"
    lwf = league_walk_forward()
    swf = shots_walk_forward()
    feats = add_derived(feats.assign(match_id=targs["match_id"].values), lwf, swf)

    # CHECK 1: arm A reproduces the shipped vector
    shipped = {c for c in joblib.load(f"{SHIPPED}/feature_cols.pkl") if not c.endswith("_missing")}
    assert set(arm_cols("A")) == shipped, f"arm A != shipped: {set(arm_cols('A')) ^ shipped}"
    # CHECK 2: A0 carries no market input; C carries none either
    for arm in ("A0", "C"):
        leak = set(arm_cols(arm)) & set(MARKET_DERIVED)
        assert not leak, f"arm {arm} carries market inputs {leak}"
    # CHECK 3: every arm-C column is alive in the training window
    for c in ARM_C:
        col = pd.to_numeric(feats[c], errors="coerce")
        assert col.notna().sum() > 10000 and (col.std() or 0) > 1e-6, f"{c} is dead"
    for c in SHOT_COLS:
        col = pd.to_numeric(feats[c], errors="coerce")
        assert col.notna().sum() > 10000 and (col.std() or 0) > 1e-6, f"{c} is dead"
    for arm in ("D", "E"):
        leak = set(arm_cols(arm)) & set(MARKET_DERIVED)
        assert not leak, f"arm {arm} carries market inputs {leak}"
    print("  checks 1-3 ✓  (A = shipped 52; A0/C/D/E market-free; C and shot columns alive)")
    for c in ARM_C + SHOT_COLS:
        col = pd.to_numeric(feats[c], errors="coerce")
        print(f"     {c:18s} fill {col.notna().mean():6.1%}  mean {col.mean():9.3f}  sd {col.std():8.3f}")

    bundles = {arm: train_arm(arm, feats, targs, a.tag) for arm in arms}

    # ── score ───────────────────────────────────────────────────────────────
    all_cols = set(arm_cols("A")) | set(HALFTIME_FEATURE_COLS) | {"elo_home", "elo_away"}
    rows = universe(a.cutoff, sorted(all_cols))
    udf = add_derived(pd.DataFrame(rows), lwf, swf)
    rows = [{k: (None if (isinstance(v, float) and math.isnan(v)) else v)
             for k, v in r.items()} for r in udf.to_dict("records")]
    print(f"\n  universe: n = {len(rows):,} fixtures on/after {a.cutoff} with Pinnacle O/U 2.5")

    # CHECK R: in-process zero-fill scoring of arm A == the harness subprocess
    if "A" in bundles:
        mine = score(bundles["A"], rows, "zero")["REALISTIC"]
        ref = harness_subprocess(bundles["A"], a.cutoff)
        for k in ("alpha", "l_mkt", "l_mod"):
            assert abs(mine[k] - ref[k]) < 6e-5, (
                f"CHECK R FAILED on {k}: in-process {mine[k]:.5f} vs harness {ref[k]:.5f} "
                f"— this script is NOT the same harness; aborting")
        print("  CHECK R ✓  in-process scoring reproduces residual_test_ou.py on arm A")
        za = mine
    covered = [r for r in rows if r.get("exp_sot_total") is not None]
    print(f"  shots-covered subset: n = {len(covered):,} ({len(covered)/len(rows):.0%})")
    full_arms = [x for x in bundles if x != "D"]
    res_full = {arm: score(bundles[arm], rows, "mean") for arm in full_arms}
    res_cov = {arm: score(b, covered, "mean") for arm, b in bundles.items()}

    def table(title, res):
        print(f"\n  {title}")
        print(f"  {'arm':5}{'harness arm':16}{'alpha':>8}{'mkt LL':>9}{'model LL':>10}"
              f"{'blend LL':>10}{'mod AUC':>9}{'resid AUC':>11}{'ECE':>7}{'p':>8}")
        for arm, r in res.items():
            for h in ("OPTIMISTIC", "REALISTIC"):
                x = r[h]
                dec = "  <- DECIDES" if h == "REALISTIC" else ""
                print(f"  {arm:5}{h:16}{x['alpha']:8.4f}{x['l_mkt']:9.4f}{x['l_mod']:10.4f}"
                      f"{x['l_bl']:10.4f}{x['auc_mod']:9.4f}{x['resid']:11.4f}{x['ece']:7.4f}"
                      f"{x['p']:8.3f}{dec}")
    table(f"FULL UNIVERSE (n={len(rows):,}) — A, A0, C, CM, E", res_full)
    table(f"SHOTS-COVERED SUBSET (n={len(covered):,}) — all arms; D and E are decided here", res_cov)
    if "A" in bundles:
        print(f"  {'A':5}{'REAL zero-fill':16}{za['alpha']:8.4f}{za['l_mkt']:9.4f}"
              f"{za['l_mod']:10.4f}{za['l_bl']:10.4f}  (harness imputation, full universe, for scale)")

    # decision p per arm: D/E on the covered subset, the rest on the full universe
    dec = {arm: (res_cov[arm] if arm in ("D", "E") else res_full[arm])["REALISTIC"] for arm in bundles}
    adj = holm({arm: x["p"] for arm, x in dec.items()}, HOLM_M)
    print("\n  VERDICT (pre-registered: REALISTIC alpha > 0.02 AND blend < market "
          f"AND Holm p < {P_BAR}, m={HOLM_M}; D/E decided on the covered subset)")
    for arm, x in dec.items():
        ok = x["alpha"] > ALPHA_BAR and x["l_bl"] < x["l_mkt"] and adj[arm] < P_BAR
        print(f"     {arm:3}  {'PASS' if ok else 'FAIL'}   alpha {x['alpha']:.4f}  Holm p {adj[arm]:.3f}")
    if "C" in res_cov and "D" in res_cov:
        c, d = res_cov["C"]["REALISTIC"], res_cov["D"]["REALISTIC"]
        print(f"\n  BEFORE → AFTER on the same covered fixtures (model alone, REALISTIC):"
              f"\n     C (goals-fed) LL {c['l_mod']:.4f} AUC {c['auc_mod']:.4f} ECE {c['ece']:.4f}"
              f"\n     D (shots-fed) LL {d['l_mod']:.4f} AUC {d['auc_mod']:.4f} ECE {d['ece']:.4f}"
              f"\n     market        LL {d['l_mkt']:.4f} AUC {d['auc_mkt']:.4f}")

    # money, on the covered subset so every arm (incl. D) is judged on the same fixtures
    prices = decision_prices({str(r["match_id"]) for r in covered[len(covered) // 2:]})
    print(f"\n  BACKTEST — covered subset, held-out half, decision T-{DECISION_MIN}min, Coolbet price, "
          f"CLV vs de-vigged Pinnacle close  ({len(prices):,} fixtures priced)")
    print("  ⚠️ A/CM carry market-price features of unrecorded capture time — "
          "their rows may be optimistic")
    print(f"  {'arm':5}{'strategy':8}{'floor':>6}{'bets':>7}{'/day':>7}{'CLV':>8}{'t':>7}{'ROI':>8}")
    for arm, b in bundles.items():
        bt = backtest(b, covered, prices, res_cov[arm]["REALISTIC"]["platt"])
        for (strat, fl), v in bt.items():
            if strat == "MARKET" and arm != arms[0]:
                continue
            print(f"  {arm if strat != 'MARKET' else '—':5}{strat:8}{fl:6.0%}{v['n']:7d}"
                  f"{v['per_day']:7.1f}{v['clv']:+8.2%}{v['t']:7.1f}{v['roi']:+8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
