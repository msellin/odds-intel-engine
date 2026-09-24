#!/usr/bin/env python3
"""1X2 RATING ARMS ([[#141]]) — can a small, full-coverage rating model beat the
shipped 1X2 head, and does it know anything de-vigged Pinnacle does not?

Pre-registration: `dev/active/1x2-model-rebuild-plan.md` §2, committed in
bf7f1d5a BEFORE the first run. Read it before reading a number this prints.

WHY RATINGS FROM SCORES, NOT match_feature_vectors
--------------------------------------------------
The shipped head reads team strength out of `match_feature_vectors`, whose
columns were populated for only part of history: 53% of its ~175k training rows
have neither Elo/form nor a market price. Final scores exist for 100% of finished
matches and half-time scores for 98%, and every strong published 1X2 input
(Elo — Hvattum & Arntzen 2010; pi-ratings — Constantinou & Fenton 2013, the core
of the 2017 Soccer Prediction Challenge winner; dynamic Poisson — Maher 1982,
Dixon & Coles 1997) is a walk-forward rating computed from exactly those scores.
So this harness computes them itself, in one chronological pass.

LEAK GUARD
----------
Matches are processed one kickoff DATE at a time: every match on a date reads
its features from the state as it stood at the end of the previous date, and
only then do that date's results update the state. `--selfcheck` asserts it.

THE TWO QUESTIONS (plan §2.3)
-----------------------------
Q1  better than production?  train <= 2026-08-30 (what v20260830 knew), test
    2026-08-31.. ; 3-way log-loss / RPS on the same rows vs PROD (v20260830 raw
    head, zero-filled exactly as served), paired bootstrap CI.
Q2  alpha vs Pinnacle?  train <= 2026-05-31, test 2026-06-01.. on rows with a
    strictly pre-kickoff Pinnacle triple; alpha fitted on the first
    chronological half, scored on the second; PASS = blend LL < market LL AND
    alpha > 0.02 AND Holm(m=6) p < 0.05.

Usage:
    python3 scripts/ab_1x2_rating_arms.py --refresh-cache      # pull DB -> parquet
    python3 scripts/ab_1x2_rating_arms.py --selfcheck
    python3 scripts/ab_1x2_rating_arms.py --tune
    python3 scripts/ab_1x2_rating_arms.py --q1
    python3 scripts/ab_1x2_rating_arms.py --q2
Read-only against the database.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "models" / "_research" / "1x2"   # gitignored via data/models/

ALPHA_FAMILY = ["E", "PI", "DP", "D8", "DX", "DXS"]     # fixed at 6 — Holm m=6
MIN_HISTORY = 8                                           # plan §1.5, pre-set


# ───────────────────────────── data cache ─────────────────────────────
def _conn():
    from dotenv import load_dotenv
    import psycopg2
    load_dotenv(ROOT / ".env")
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.cursor().execute("SET statement_timeout='900s'")
    return c


def refresh_cache() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    c = _conn()
    m = pd.read_sql("""
        SELECT m.id::text match_id, m.date kickoff, m.league_id::text league_id,
               coalesce(l.tier, 9) tier, l.country,
               m.home_team_id::text home, m.away_team_id::text away,
               m.score_home gh, m.score_away ga, m.ht_score_home hh, m.ht_score_away ha
          FROM matches m LEFT JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
    """, c)
    m.to_parquet(CACHE / "matches.parquet")
    print(f"matches: {len(m):,}")

    s = pd.read_sql("""
        SELECT match_id::text match_id, shots_on_target_home sot_h, shots_on_target_away sot_a,
               shots_home sh_h, shots_away sh_a, xg_home::float xg_h, xg_away::float xg_a
          FROM match_stats
    """, c)
    s.to_parquet(CACHE / "stats.parquet")
    print(f"stats: {len(s):,}")

    # Pinnacle 1X2, strictly before kickoff (timestamp < kickoff; the
    # minutes_to_kickoff sign convention is documented as inconsistent — see
    # residual_test.py — so the kickoff timestamp is the bound used here).
    o = pd.read_sql("""
        SELECT o.match_id::text match_id, o.selection, o.odds::float odds, o."timestamp" ts
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = 'Pinnacle' AND o.market = '1x2' AND o.is_live IS NOT TRUE
           AND o.odds > 1.01 AND o."timestamp" < m.date
    """, c)
    o.to_parquet(CACHE / "pinnacle_raw.parquet")
    print(f"pinnacle rows: {len(o):,}")
    c.close()


def load_pinnacle(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per match: close (last complete pre-KO triple), decision (last
    complete triple >= 2h before KO) and open (first complete triple)."""
    o = pd.read_parquet(CACHE / "pinnacle_raw.parquet")
    w = o.pivot_table(index=["match_id", "ts"], columns="selection", values="odds", aggfunc="last")
    w = w.dropna(subset=["home", "draw", "away"]).reset_index().sort_values(["match_id", "ts"])
    ko = matches.set_index("match_id")["kickoff"]
    w["ko"] = w["match_id"].map(ko)
    out = {}
    for tag, frame in (("close", w.groupby("match_id").tail(1)),
                       ("open", w.groupby("match_id").head(1)),
                       ("dec", w[w["ts"] <= w["ko"] - pd.Timedelta(hours=2)].groupby("match_id").tail(1))):
        f = frame.set_index("match_id")[["home", "draw", "away"]]
        inv = 1 / f
        p = inv.div(inv.sum(axis=1), axis=0)
        out[tag] = pd.DataFrame({f"pin_{tag}_h": p["home"], f"pin_{tag}_d": p["draw"],
                                 f"pin_{tag}_a": p["away"], f"pin_{tag}_over": inv.sum(axis=1)})
        if tag == "close":
            out["close_odds"] = f.rename(columns={"home": "odds_h", "draw": "odds_d", "away": "odds_a"})
    return pd.concat(out.values(), axis=1)


# ───────────────────────────── ratings pass ─────────────────────────────
def _skellam_1x2(lh: float, la: float, n: int = 11):
    k = np.arange(n)
    fk = np.array([math.factorial(int(i)) for i in k], float)
    ph = np.exp(-lh) * lh ** k / fk
    pa = np.exp(-la) * la ** k / fk
    mat = np.outer(ph, pa)
    return float(np.tril(mat, -1).sum()), float(np.trace(mat)), float(np.triu(mat, 1).sum())


DEFAULT_PARAMS = dict(elo_k=20.0, elo_hfa=60.0, pi_lr=0.035, pi_gamma=0.7,
                      dp_lr=0.06, dp_league_lr=0.01, ht_lr=0.06, sot_lr=0.04,
                      form_hl_days=60.0, league_hl_days=365.0)


def _clip(d: dict, *keys, lim: float = 2.0) -> None:
    """Online SGD on a sparse schedule can run away for a team with a handful of
    freak scores (dp_diff reached ±31 goals unclamped). ±2 on the log scale is
    already a 7x multiplier."""
    for k in keys:
        d[k] = max(-lim, min(lim, d[k]))


def build_features(m: pd.DataFrame, stats: pd.DataFrame | None, P: dict | None = None) -> pd.DataFrame:
    """Walk-forward pre-match features for every match. Date-batched (leak guard)."""
    P = {**DEFAULT_PARAMS, **(P or {})}
    m = m.sort_values("kickoff").reset_index(drop=True)
    m["day"] = m["kickoff"].dt.tz_convert("UTC").dt.date if m["kickoff"].dt.tz is not None else m["kickoff"].dt.date
    if stats is not None:
        m = m.merge(stats[["match_id", "sot_h", "sot_a"]].drop_duplicates("match_id"), on="match_id", how="left")
    else:
        m = m.assign(sot_h=np.nan, sot_a=np.nan)

    elo = {}
    league_elo = defaultdict(lambda: [0.0, 0])          # sum, n  -> newcomer start
    pi_h, pi_a = defaultdict(float), defaultdict(float)
    att, dfn = defaultdict(float), defaultdict(float)
    hatt, hdfn = defaultdict(float), defaultdict(float)
    satt, sdfn = defaultdict(float), defaultdict(float)
    lmu = defaultdict(lambda: math.log(1.3)); lhfa = defaultdict(lambda: 0.25)
    hmu = defaultdict(lambda: math.log(0.58)); hhfa = defaultdict(lambda: 0.2)
    smu = defaultdict(lambda: math.log(4.2)); shfa = defaultdict(lambda: 0.15)
    form = {}                                           # team -> (ew_points, ew_weight, last_day)
    nplayed = defaultdict(int)
    last_day = {}
    lg = defaultdict(lambda: [0.45, 0.26, 0.0, None])   # ew home-win, draw, weight, last_day

    def elo_of(t, league):
        if t not in elo:
            s, n = league_elo[league]
            elo[t] = (s / n - 50.0) if n >= 5 else 1500.0   # newcomers start a bit below league mean
        return elo[t]

    def pi_exp(r):
        return math.copysign(10 ** (abs(r) / 3.0) - 1, r)

    rows = []
    for day, grp in m.groupby("day", sort=True):
        feats = []
        for r in grp.itertuples(index=False):
            h, a, L = r.home, r.away, r.league_id
            eh, ea = elo_of(h, L), elo_of(a, L)
            lh = math.exp(lmu[L] + lhfa[L] + att[h] - dfn[a]); la = math.exp(lmu[L] + att[a] - dfn[h])
            hlh = math.exp(hmu[L] + hhfa[L] + hatt[h] - hdfn[a]); hla = math.exp(hmu[L] + hatt[a] - hdfn[h])
            slh = math.exp(smu[L] + shfa[L] + satt[h] - sdfn[a]); sla = math.exp(smu[L] + satt[a] - sdfn[h])
            dp = _skellam_1x2(lh, la)

            def fppg(t):
                if t not in form: return np.nan
                p, w, ld = form[t]
                return p / w if w > 0 else np.nan

            lw = lg[L]
            feats.append(dict(
                match_id=r.match_id,
                elo_diff=eh - ea,
                pi_diff=pi_exp(pi_h[h]) - pi_exp(pi_a[a]),
                dp_lh=lh, dp_la=la, dp_diff=lh - la,
                dp_ph=dp[0], dp_pd=dp[1], dp_pa=dp[2],
                ht_diff=hlh - hla, sot_diff=slh - sla,
                form_diff=fppg(h) - fppg(a),
                rest_diff=(min((day - last_day[h]).days, 21) if h in last_day else np.nan)
                          - (min((day - last_day[a]).days, 21) if a in last_day else np.nan),
                lg_home=lw[0], lg_draw=lw[1],
                n_home=nplayed[h], n_away=nplayed[a],
            ))
        rows.extend(feats)

        # ── update state with this date's results ──
        for r in grp.itertuples(index=False):
            h, a, L = r.home, r.away, r.league_id
            gd = r.gh - r.ga
            # Elo (World-Football-Elo margin multiplier)
            eh, ea = elo[h], elo[a]
            we = 1 / (1 + 10 ** (-(eh - ea + P["elo_hfa"]) / 400))
            res = 1.0 if gd > 0 else 0.5 if gd == 0 else 0.0
            g = 1.0 if abs(gd) <= 1 else 1.5 if abs(gd) == 2 else (11 + abs(gd)) / 8
            d = P["elo_k"] * g * (res - we)
            elo[h] = eh + d; elo[a] = ea - d
            # pi-ratings
            exp_gd = pi_exp(pi_h[h]) - pi_exp(pi_a[a])
            err = gd - exp_gd
            psi = math.copysign(3 * math.log10(1 + abs(err)), err)
            dh = psi * P["pi_lr"]
            pi_h[h] += dh; pi_a[h] += dh * P["pi_gamma"]
            pi_a[a] -= dh; pi_h[a] -= dh * P["pi_gamma"]
            # dynamic Poisson, FT goals
            lh = math.exp(lmu[L] + lhfa[L] + att[h] - dfn[a]); la = math.exp(lmu[L] + att[a] - dfn[h])
            eh_, ea_ = r.gh - lh, r.ga - la
            att[h] += P["dp_lr"] * eh_; dfn[a] -= P["dp_lr"] * eh_
            att[a] += P["dp_lr"] * ea_; dfn[h] -= P["dp_lr"] * ea_
            _clip(att, h, a); _clip(dfn, h, a)
            lmu[L] = max(-1.0, min(1.5, lmu[L])); lhfa[L] = max(-0.3, min(0.8, lhfa[L]))
            lmu[L] += P["dp_league_lr"] * (eh_ + ea_) / 2; lhfa[L] += P["dp_league_lr"] * (eh_ - ea_) / 2
            # dynamic Poisson, HT goals
            if not (pd.isna(r.hh) or pd.isna(r.ha)):
                hlh = math.exp(hmu[L] + hhfa[L] + hatt[h] - hdfn[a]); hla = math.exp(hmu[L] + hatt[a] - hdfn[h])
                e1, e2 = r.hh - hlh, r.ha - hla
                hatt[h] += P["ht_lr"] * e1; hdfn[a] -= P["ht_lr"] * e1
                hatt[a] += P["ht_lr"] * e2; hdfn[h] -= P["ht_lr"] * e2
                _clip(hatt, h, a); _clip(hdfn, h, a)
                hmu[L] += P["dp_league_lr"] * (e1 + e2) / 2; hhfa[L] += P["dp_league_lr"] * (e1 - e2) / 2
            # dynamic Poisson, shots on target (only where recorded)
            if True:
                if not (pd.isna(r.sot_h) or pd.isna(r.sot_a)):
                    slh = math.exp(smu[L] + shfa[L] + satt[h] - sdfn[a]); sla = math.exp(smu[L] + satt[a] - sdfn[h])
                    e1, e2 = r.sot_h - slh, r.sot_a - sla
                    satt[h] += P["sot_lr"] * e1 / 4; sdfn[a] -= P["sot_lr"] * e1 / 4
                    satt[a] += P["sot_lr"] * e2 / 4; sdfn[h] -= P["sot_lr"] * e2 / 4
                    _clip(satt, h, a); _clip(sdfn, h, a)
                    smu[L] += P["dp_league_lr"] * (e1 + e2) / 8; shfa[L] += P["dp_league_lr"] * (e1 - e2) / 8
            # form: exponentially-decayed points per game
            for t, pts in ((h, 3 if gd > 0 else 1 if gd == 0 else 0), (a, 3 if gd < 0 else 1 if gd == 0 else 0)):
                if t in form:
                    p, w, ld = form[t]
                    dec = 0.5 ** ((day - ld).days / P["form_hl_days"])
                    form[t] = (p * dec + pts, w * dec + 1, day)
                else:
                    form[t] = (pts, 1.0, day)
                nplayed[t] += 1
                last_day[t] = day
            # league outcome rates
            lw = lg[L]
            dec = 0.5 ** (((day - lw[3]).days if lw[3] else 0) / P["league_hl_days"])
            wprev = lw[2] * dec
            lg[L] = [(lw[0] * wprev + (gd > 0)) / (wprev + 1), (lw[1] * wprev + (gd == 0)) / (wprev + 1),
                     min(wprev + 1, 200.0), day]
        # league Elo means (for newcomers) refreshed after the day's updates
        for L in set(grp.league_id):
            teams = set(grp[grp.league_id == L].home) | set(grp[grp.league_id == L].away)
            vals = [elo[t] for t in teams if t in elo]
            if vals:
                s, n = league_elo[L]
                league_elo[L] = [s + sum(vals), n + len(vals)] if n < 400 else \
                    [s * 0.95 + sum(vals), n * 0.95 + len(vals)]

    f = pd.DataFrame(rows)
    out = m.merge(f, on="match_id", how="left")
    out["y"] = np.where(out.gh > out.ga, 0, np.where(out.gh == out.ga, 1, 2))
    return out


# ───────────────────────────── metrics ─────────────────────────────
def per_match_ll(P: np.ndarray, y: np.ndarray) -> np.ndarray:
    P = np.clip(P, 1e-9, 1); P = P / P.sum(1, keepdims=True)
    return -np.log(P[np.arange(len(y)), y])


def rps(P: np.ndarray, y: np.ndarray) -> float:
    oh = np.eye(3)[y]
    return float(np.mean(((np.cumsum(P, 1) - np.cumsum(oh, 1))[:, :2] ** 2).sum(1) / 2))


def boot_ci(d: np.ndarray, n: int = 2000, seed: int = 0):
    rng = np.random.default_rng(seed)
    bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)])
    p_le0 = float((bs <= 0).mean())
    return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)), p_le0


# ───────────────────────────── arms ─────────────────────────────
ARM_FEATURES = {
    "E":   ["elo_diff"],
    "PI":  ["pi_diff"],
    "D8":  ["elo_diff", "pi_diff", "dp_diff", "ht_diff", "form_diff", "rest_diff", "lg_home", "lg_draw"],
    "DX":  ["elo_diff", "pi_diff", "dp_diff", "ht_diff", "form_diff", "rest_diff", "lg_home", "lg_draw"],
    "DXS": ["elo_diff", "pi_diff", "dp_diff", "ht_diff", "form_diff", "rest_diff", "lg_home", "lg_draw",
            "sot_diff"],
    "DXM": ["elo_diff", "pi_diff", "dp_diff", "ht_diff", "form_diff", "rest_diff", "lg_home", "lg_draw",
            "pin_dec_h", "pin_dec_a"],
}


def fit_predict(arm: str, tr: pd.DataFrame, te: pd.DataFrame, val_days: int = 60) -> np.ndarray:
    if arm == "DP":
        return te[["dp_ph", "dp_pd", "dp_pa"]].to_numpy()
    cols = ARM_FEATURES[arm]
    if arm in ("E", "PI", "D8"):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import make_pipeline
        Xtr = tr[cols].fillna(tr[cols].median()); Xte = te[cols].fillna(tr[cols].median())
        mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        mdl.fit(Xtr, tr.y)
        return mdl.predict_proba(Xte)
    import xgboost as xgb
    cut = tr.kickoff.max() - pd.Timedelta(days=val_days)
    a, v = tr[tr.kickoff <= cut], tr[tr.kickoff > cut]
    mdl = xgb.XGBClassifier(objective="multi:softprob", n_estimators=3000, max_depth=3,
                            learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
                            min_child_weight=50, reg_lambda=5.0, eval_metric="mlogloss",
                            early_stopping_rounds=100, tree_method="hist", verbosity=0)
    mdl.fit(a[cols], a.y, eval_set=[(v[cols], v.y)], verbose=False)
    return mdl.predict_proba(te[cols])


def gated(df: pd.DataFrame, n: int = MIN_HISTORY) -> pd.Series:
    return (df.n_home >= n) & (df.n_away >= n)


# ───────────────────────────── PROD reference ─────────────────────────────
def prod_reference(match_ids: list[str], version: str = "v20260830") -> pd.DataFrame:
    """v20260830 raw 1X2 head exactly as served on 2026-09-24: MFV row, zero-fill
    (no bundle carries feature_fill_values). Draw shrink / Platt / Pinnacle
    shrinkage are applied downstream in production and are NOT part of the model."""
    import joblib
    import psycopg2.extras
    from scripts.weekly_eval_and_compare import _build_row, MODELS_DIR
    fc = joblib.load(MODELS_DIR / version / "feature_cols.pkl")
    mdl = joblib.load(MODELS_DIR / version / "result_1x2.pkl")
    c = _conn(); d = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    d.execute("""SELECT mfv.*, l.tier FROM match_feature_vectors mfv JOIN matches m ON m.id = mfv.match_id
                 LEFT JOIN leagues l ON l.id = m.league_id WHERE mfv.match_id::text = ANY(%s)""", (match_ids,))
    rows = d.fetchall(); c.close()
    X = np.array([[_build_row(dict(r), fc, r.get("tier") or 1)[k] for k in fc] for r in rows], float)
    pr = mdl.predict_proba(X); cl = list(mdl.classes_)
    ih, idd, ia = (cl.index("H"), cl.index("D"), cl.index("A")) if "H" in cl else (0, 1, 2)
    return pd.DataFrame({"match_id": [str(r["match_id"]) for r in rows],
                         "prod_ph": pr[:, ih], "prod_pd": pr[:, idd], "prod_pa": pr[:, ia]})



TUNE_GRID = {                    # param -> (candidates, the feature whose validation LL judges it)
    "elo_k":        ([12.0, 20.0, 30.0, 45.0], ["elo_diff"]),
    "elo_hfa":      ([40.0, 60.0, 90.0], ["elo_diff"]),
    "pi_lr":        ([0.02, 0.035, 0.06, 0.1], ["pi_diff"]),
    "pi_gamma":     ([0.5, 0.7, 0.9], ["pi_diff"]),
    "dp_lr":        ([0.03, 0.06, 0.1, 0.15], "DP"),
    "dp_league_lr": ([0.003, 0.01, 0.03], "DP"),
    "ht_lr":        ([0.03, 0.06, 0.12], ["ht_diff"]),
    "sot_lr":       ([0.02, 0.04, 0.08], ["sot_diff"]),
    "form_hl_days": ([30.0, 60.0, 120.0], ["form_diff"]),
}


TUNE_GRID_EXTEND = {             # round 2 — only the params whose round-1 optimum sat on a grid edge
    "elo_k":        ([45.0, 65.0, 90.0], ["elo_diff"]),
    "pi_lr":        ([0.1, 0.15, 0.25], ["pi_diff"]),
    "pi_gamma":     ([0.9, 1.0], ["pi_diff"]),
    "dp_lr":        ([0.01, 0.02, 0.03], "DP"),
    "dp_league_lr": ([0.001, 0.003], "DP"),
    "sot_lr":       ([0.08, 0.15], ["sot_diff"]),
    "form_hl_days": ([120.0, 240.0], ["form_diff"]),
}


def tune(extend: bool = False) -> dict:
    """Coordinate descent on a VALIDATION slice inside the training period only:
    fit 2022-07-01..2026-02-28, score 2026-03-01..2026-05-31 (gated rows). The
    Q1/Q2 test windows are never read here (plan §2.2)."""
    from sklearn.linear_model import LogisticRegression
    m = pd.read_parquet(CACHE / "matches.parquet")
    m["kickoff"] = pd.to_datetime(m["kickoff"], utc=True)
    m = m[(m.kickoff >= "2022-01-01") & (m.kickoff < "2026-06-01")]
    stats = pd.read_parquet(CACHE / "stats.parquet")
    import json
    best = dict(DEFAULT_PARAMS)
    grid = TUNE_GRID
    if extend:
        best.update(json.loads((CACHE / "tuned_params.json").read_text()))
        grid = TUNE_GRID_EXTEND

    def score(params, judge):
        f = build_features(m, stats, params)
        g = gated(f)
        tr = f[g & (f.kickoff >= "2022-07-01") & (f.kickoff < "2026-03-01")]
        va = f[g & (f.kickoff >= "2026-03-01")]
        if judge == "DP":
            return per_match_ll(va[["dp_ph", "dp_pd", "dp_pa"]].to_numpy(), va.y.to_numpy()).mean()
        mdl = LogisticRegression(max_iter=2000).fit(tr[judge].fillna(0), tr.y)
        return per_match_ll(mdl.predict_proba(va[judge].fillna(0)), va.y.to_numpy()).mean()

    for name, (cands, judge) in grid.items():
        res = {}
        for v in cands:
            res[v] = score({**best, name: v}, judge)
        best[name] = min(res, key=res.get)
        print(f"  {name:13s} " + "  ".join(f"{v}:{res[v]:.4f}" for v in cands) + f"  -> {best[name]}", flush=True)
    print("TUNED", best)
    (CACHE / "tuned_params.json").write_text(__import__("json").dumps(best))
    return best

def served_reference(match_ids: list[str]) -> pd.DataFrame:
    """What production actually SERVED (source='ensemble', v20260830 — after the
    Poisson blend, Pinnacle shrinkage and Platt) and API-Football's own
    prediction (source='af'). Reference points, not arms."""
    c = _conn()
    q = pd.read_sql("""
        SELECT match_id::text match_id, source, market, model_probability::float p
          FROM predictions
         WHERE market IN ('1x2_home','1x2_draw','1x2_away')
           AND ((source = 'ensemble' AND model_version = 'v20260830') OR source = 'af')
           AND match_id::text = ANY(%(ids)s)""", c, params={"ids": match_ids})
    c.close()
    w = q.pivot_table(index="match_id", columns=["source", "market"], values="p", aggfunc="last")
    out = pd.DataFrame(index=w.index)
    for src, tag in (("ensemble", "served"), ("af", "af")):
        cols = [(src, "1x2_home"), (src, "1x2_draw"), (src, "1x2_away")]
        if all(cl in w.columns for cl in cols):
            out[[f"{tag}_ph", f"{tag}_pd", f"{tag}_pa"]] = w[cols].to_numpy()
    return out.reset_index()


# ───────────────────────────── runs ─────────────────────────────
def load_all(params: dict | None = None) -> pd.DataFrame:
    m = pd.read_parquet(CACHE / "matches.parquet")
    m["kickoff"] = pd.to_datetime(m["kickoff"], utc=True)
    m = m[m.kickoff >= "2022-01-01"]
    stats = pd.read_parquet(CACHE / "stats.parquet")
    if params is None and (CACHE / "tuned_params.json").exists():
        params = __import__("json").loads((CACHE / "tuned_params.json").read_text())
        print(f"using tuned params {params}")
    f = build_features(m, stats, params)
    pin = load_pinnacle(m)
    return f.merge(pin, left_on="match_id", right_index=True, how="left")


def _report(name: str, P: np.ndarray, y: np.ndarray, ref_ll: np.ndarray | None = None) -> str:
    ll = per_match_ll(P, y)
    s = f"{name:10s} LL {ll.mean():.4f}  RPS {rps(P, y):.4f}  acc {np.mean(P.argmax(1) == y):.3f}"
    if ref_ll is not None:
        mu, lo, hi, _ = boot_ci(ll - ref_ll)
        s += f"  Δ vs ref {mu:+.4f} [{lo:+.4f},{hi:+.4f}]"
    return s


def run_q1(df: pd.DataFrame) -> None:
    cutoff, start = pd.Timestamp("2026-08-30 23:59", tz="UTC"), pd.Timestamp("2026-08-31", tz="UTC")
    tr = df[(df.kickoff <= cutoff) & (df.kickoff >= "2022-07-01") & gated(df)]
    te = df[df.kickoff >= start].copy()
    prod = prod_reference(te.match_id.tolist())
    te = te.merge(prod, on="match_id", how="inner")
    te = te.merge(served_reference(te.match_id.tolist()), on="match_id", how="left")
    y = te.y.to_numpy()
    print(f"Q1 — train {len(tr):,} gated rows ≤ {cutoff.date()}, test {len(te):,} rows ≥ {start.date()} "
          f"with a PROD prediction; gated test rows {int(gated(te).sum()):,}")
    Pp = te[["prod_ph", "prod_pd", "prod_pa"]].to_numpy()
    ref = per_match_ll(Pp, y)
    base = np.tile(np.bincount(tr.y, minlength=3) / len(tr), (len(te), 1))
    preds = {"BASE": base, "PROD": Pp}
    for tag in ("served", "af"):
        if f"{tag}_ph" in te:
            preds[tag.upper()] = te[[f"{tag}_ph", f"{tag}_pd", f"{tag}_pa"]].to_numpy(dtype=float)
    for arm in ALPHA_FAMILY + ["DXM"]:
        tr_arm = tr.dropna(subset=["pin_dec_h"]) if arm == "DXM" else tr
        if arm == "DXM":
            ok = te.pin_dec_h.notna().to_numpy()
            P = np.full((len(te), 3), np.nan)
            P[ok] = fit_predict(arm, tr_arm, te[ok])
            preds[arm] = P
        else:
            preds[arm] = fit_predict(arm, tr_arm, te)
    for bucket, mask in (("ALL", np.ones(len(te), bool)), ("GATED", gated(te).to_numpy()),
                         ("UNGATED", ~gated(te).to_numpy()),
                         ("PIN-PRICED", te.pin_close_h.notna().to_numpy())):
        print(f"\n── {bucket}  n={mask.sum():,}")
        for k, P in preds.items():
            mk = mask & ~np.isnan(P).any(1)
            if mk.sum() < 50: continue
            print("  " + _report(k, P[mk], y[mk], None if k == "PROD" else ref[mk]) +
                  ("" if mk.sum() == mask.sum() else f"  (n={mk.sum():,})"))
        if bucket == "PIN-PRICED":
            Pm = te[["pin_close_h", "pin_close_d", "pin_close_a"]].to_numpy()
            print("  " + _report("PIN-CLOSE", Pm[mask], y[mask], ref[mask]))
    for t in sorted(te.tier.unique()):
        mk = (te.tier == t).to_numpy() & gated(te).to_numpy()
        if mk.sum() < 100: continue
        print(f"  tier {t} gated n={mk.sum():,}: PROD {per_match_ll(Pp[mk], y[mk]).mean():.4f}  "
              + "  ".join(f"{k} {per_match_ll(preds[k][mk], y[mk]).mean():.4f}" for k in ("D8", "DX", "DP")))


def run_q2(df: pd.DataFrame) -> None:
    cutoff = pd.Timestamp("2026-05-31 23:59", tz="UTC")
    tr = df[(df.kickoff <= cutoff) & (df.kickoff >= "2022-07-01") & gated(df)]
    te = df[(df.kickoff > cutoff) & df.pin_close_h.notna() & gated(df)].sort_values("kickoff").copy()
    y = te.y.to_numpy()
    Pm = te[["pin_close_h", "pin_close_d", "pin_close_a"]].to_numpy()
    half = len(te) // 2
    print(f"Q2 — train {len(tr):,} gated rows ≤ {cutoff.date()}; test {len(te):,} gated Pinnacle-priced rows "
          f"(alpha fit on first {half:,}, scored on last {len(te) - half:,})")
    print(f"  mean Pinnacle overround {te.pin_close_over.mean():.4f}")
    lm = per_match_ll(Pm[half:], y[half:])
    print(f"  MARKET (close, proportional de-vig) LL {lm.mean():.4f}  RPS {rps(Pm[half:], y[half:]):.4f}")
    results = []
    for arm in ALPHA_FAMILY:
        P = fit_predict(arm, tr, te)
        grid = np.linspace(0, 1, 201)
        lls = [per_match_ll(a * P[:half] + (1 - a) * Pm[:half], y[:half]).mean() for a in grid]
        alpha = float(grid[int(np.argmin(lls))])
        B = alpha * P[half:] + (1 - alpha) * Pm[half:]
        lb = per_match_ll(B, y[half:])
        mu, lo, hi, p = boot_ci(lm - lb)                     # >0 means blend better
        results.append((arm, alpha, per_match_ll(P[half:], y[half:]).mean(), lb.mean(), mu, lo, hi, p))
    # Holm
    order = sorted(range(len(results)), key=lambda i: results[i][7])
    adj = [0.0] * len(results); run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(results) - rank) * results[i][7])); adj[i] = run
    print(f"  {'arm':5s} {'alpha':>6s} {'model LL':>9s} {'blend LL':>9s} {'gain':>8s} {'95% CI':>20s} {'p':>6s} {'Holm p':>7s} verdict")
    for i, (arm, al, lmod, lbl, mu, lo, hi, p) in enumerate(results):
        ok = lbl < lm.mean() and al > 0.02 and adj[i] < 0.05
        print(f"  {arm:5s} {al:6.3f} {lmod:9.4f} {lbl:9.4f} {mu:+8.4f} [{lo:+.4f},{hi:+.4f}] {p:6.3f} {adj[i]:7.3f} "
              f"{'PASS' if ok else 'FAIL'}")


def selfcheck(df: pd.DataFrame) -> None:
    """Leak guard: a match's features must not change if its own result is altered."""
    m = pd.read_parquet(CACHE / "matches.parquet")
    m["kickoff"] = pd.to_datetime(m["kickoff"], utc=True)
    m = m[(m.kickoff >= "2025-01-01") & (m.kickoff < "2025-03-01")].copy()
    base = build_features(m, None).set_index("match_id")
    tgt = m.sort_values("kickoff").iloc[len(m) // 2].match_id
    m2 = m.copy(); m2.loc[m2.match_id == tgt, ["gh", "ga"]] = (9, 0)
    alt = build_features(m2, None).set_index("match_id")
    cols = ["elo_diff", "pi_diff", "dp_diff", "form_diff"]
    same = np.allclose(base.loc[tgt, cols].astype(float), alt.loc[tgt, cols].astype(float), equal_nan=True)
    day = base.loc[tgt, "day"]
    sameday = base[base.day == day].index
    sd_same = np.allclose(base.loc[sameday, cols].astype(float), alt.loc[sameday, cols].astype(float), equal_nan=True)
    later = base[base.day > day].index
    changed = not np.allclose(base.loc[later, cols].astype(float), alt.loc[later, cols].astype(float), equal_nan=True)
    print(f"own features unchanged: {same}; same-day features unchanged: {sd_same}; later features changed: {changed}")
    assert same and sd_same and changed, "LEAK GUARD FAILED"
    print("SELFCHECK PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-cache", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--tune-extend", action="store_true")
    ap.add_argument("--q1", action="store_true")
    ap.add_argument("--q2", action="store_true")
    a = ap.parse_args()
    if a.refresh_cache:
        refresh_cache()
    if a.selfcheck:
        selfcheck(None)
    if a.tune or a.tune_extend:
        tune(extend=a.tune_extend)
    if a.q1 or a.q2:
        df = load_all()
        if a.q1: run_q1(df)
        if a.q2: run_q2(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
