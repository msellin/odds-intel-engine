"""1X2 RATING MODEL ([[#141]]) — walk-forward team ratings from match scores.

Built and measured in `scripts/ab_1x2_rating_arms.py`; plan, pre-registration and
results in `dev/active/1x2-model-rebuild-plan.md`. On the 2026-08-31.. holdout the
8-feature logit on these ratings (arm D8) beat the shipped XGBoost head by
-0.035 log-loss on gated rows (95% CI -0.043..-0.027) and in every league tier.
It does NOT beat Pinnacle's closing line (alpha = 0, as the literature predicts),
so where Pinnacle prices a match the market remains the better number.

Why ratings from scores rather than `match_feature_vectors`: final scores exist
for 100% of finished matches; the feature table's strength columns were blank
on 53% of training rows. Elo (Hvattum & Arntzen 2010), pi-ratings (Constantinou
& Fenton 2013) and dynamic Poisson attack/defence (Maher 1982; Dixon & Coles
1997) all need nothing but scores.

LEAK GUARD: matches are processed one kickoff date at a time; a date's features
are read from the state as it stood after the previous date. Upcoming fixtures
(no score) receive features and never update state.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

MIN_HISTORY = 8          # both teams need >= 8 prior rated matches (coverage gate)

# Chosen on the validation slice only (fit 2022-07..2026-02, score 2026-03..05),
# two coordinate-descent rounds — see the plan doc, "tuning".
TUNED_PARAMS = dict(elo_k=45.0, elo_hfa=60.0, pi_lr=0.1, pi_gamma=0.9, dp_lr=0.03,
                    dp_league_lr=0.003, ht_lr=0.06, sot_lr=0.08, form_hl_days=240.0,
                    league_hl_days=365.0)

D8_FEATURES = ["elo_diff", "pi_diff", "dp_diff", "ht_diff", "form_diff", "rest_diff",
               "lg_home", "lg_draw"]


def gated(df: pd.DataFrame, n: int = MIN_HISTORY) -> pd.Series:
    return (df.n_home >= n) & (df.n_away >= n)


def _skellam_1x2(lh: float, la: float, n: int = 11):
    k = np.arange(n)
    fk = np.array([math.factorial(int(i)) for i in k], float)
    ph = np.exp(-lh) * lh ** k / fk
    pa = np.exp(-la) * la ** k / fk
    mat = np.outer(ph, pa)
    return float(np.tril(mat, -1).sum()), float(np.trace(mat)), float(np.triu(mat, 1).sum())


def _clip(d: dict, *keys, lim: float = 2.0) -> None:
    """Online SGD on a sparse schedule can run away for a team with a handful of
    freak scores (dp_diff reached ±31 goals unclamped). ±2 on the log scale is
    already a 7x multiplier."""
    for k in keys:
        d[k] = max(-lim, min(lim, d[k]))


def build_features(m: pd.DataFrame, stats: pd.DataFrame | None, P: dict | None = None) -> pd.DataFrame:
    """Walk-forward pre-match features for every match. Date-batched (leak guard)."""
    P = {**TUNED_PARAMS, **(P or {})}
    # Deterministic order: league-level updates inside a date do not commute
    # exactly, so ties on kickoff must break the same way on every run.
    # `kickoff` may be tz-aware datetimes (research harness) or epoch SECONDS (the
    # production job). Everything below runs on an integer UTC day number, because
    # pandas 3.0.4 on the VPS segfaults on any take/filter over a tz-aware datetime
    # column (reproduced 2026-09-24; 3.0.2 is fine) — so the serving path never
    # builds one, and this function never needs one.
    if pd.api.types.is_datetime64_any_dtype(m["kickoff"]):
        secs = (m["kickoff"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    else:
        secs = m["kickoff"].astype(float)
    m = m.assign(_secs=secs.to_numpy())
    m = m.sort_values(["_secs", "match_id"], kind="mergesort").reset_index(drop=True)
    m["day"] = (m["_secs"] // 86400).astype(np.int64)
    if stats is not None:
        m = m.merge(stats[["match_id", "sot_h", "sot_a"]].drop_duplicates("match_id"), on="match_id", how="left")
    else:
        m = m.assign(sot_h=np.nan, sot_a=np.nan)

    elo = {}
    seen: set = set()
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
            elo[t] = (s / n - P.get("elo_new_offset", 50.0)) if n >= 5 else 1500.0   # newcomers start below league mean
        return elo[t]

    def pi_exp(r):
        return math.copysign(10 ** (abs(r) / 3.0) - 1, r)

    rows = []
    for day, grp in m.groupby("day", sort=True):
        feats = []
        for r in grp.itertuples(index=False):
            h, a, L = r.home, r.away, r.league_id
            eh, ea = elo_of(h, L), elo_of(a, L)
            for t in (h, a):                       # NEWC: newcomers' Poisson strength prior
                if t not in seen:
                    seen.add(t)
                    att[t] = P.get("dp_new_att", 0.0); dfn[t] = P.get("dp_new_def", 0.0)
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
                rest_diff=(min(day - last_day[h], 21) if h in last_day else np.nan)
                          - (min(day - last_day[a], 21) if a in last_day else np.nan),
                lg_home=lw[0], lg_draw=lw[1],
                n_home=nplayed[h], n_away=nplayed[a],
            ))
        rows.extend(feats)

        # ── update state with this date's results ──
        for r in grp.itertuples(index=False):
            if pd.isna(r.gh) or pd.isna(r.ga):
                continue                  # upcoming fixture: features only, never a state update
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
                    dec = 0.5 ** ((day - ld) / P["form_hl_days"])
                    form[t] = (p * dec + pts, w * dec + 1, day)
                else:
                    form[t] = (pts, 1.0, day)
                nplayed[t] += 1
                last_day[t] = day
            # league outcome rates
            lw = lg[L]
            dec = 0.5 ** (((day - lw[3]) if lw[3] is not None else 0) / P["league_hl_days"])
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
    out = m.drop(columns="_secs").merge(f, on="match_id", how="left")
    out["y"] = np.where(out.gh > out.ga, 0, np.where(out.gh == out.ga, 1, 2))
    out.loc[out.gh.isna() | out.ga.isna(), "y"] = -1
    return out


