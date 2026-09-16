"""Dixon-Coles bivariate-Poisson goals model (Dixon & Coles, 1997).

WHY THIS EXISTS (2026-09-16). Both shipped heads measure residual α = 0.0000
against Pinnacle, and they share ONE feature set engineered for match outcome,
of which only nine columns clear 88% population. Two zeros from one feature set
is one result about the feature set. This is the control that settles it: a
goals model needing only `(home, away, score, date)` — 171,509 matches at 100%
coverage, where every other candidate input is capped at 4–15% by data we do not
have.

It is also the model `MODEL_WHITEPAPER` §5.1 has claimed we use since May, to
justify weighting goal-line markets higher than 1x2. We never had one.

THE MODEL
    λ = exp(atk_home − def_away + γ)      home expected goals
    μ = exp(atk_away − def_home)          away expected goals
    P(x,y) = τ(x,y,λ,μ,ρ) · Pois(x;λ) · Pois(y;μ)

τ is the correction that makes this Dixon-Coles rather than two independent
Poissons. Independent Poissons misprice exactly the four low scores — 0-0, 1-0,
0-1, 1-1 — which is where O/U 1.5 and 2.5 are decided, so it is load-bearing
here rather than a refinement.

Pure: no DB, no I/O, no global state. `dixon_coles.selfcheck()` exercises it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

MAX_GOALS = 10          # score matrix truncation; P(>10 goals) is ~1e-6 at real lambdas
RHO_BOUNDS = (-0.2, 0.2)

# RIDGE (2026-09-16, found in phase 2). Without a penalty the optimiser pushes a
# thinly-observed team's attack/defence to the parameter bounds: the first
# walk-forward produced lambda values from 0.003 to 227.4 goals. The MEAN was
# right (predicted total 3.0644 vs actual 3.0600) so the level was never the
# problem — the distribution was, and a degenerate lambda sends P(over 2.5) to 0
# or 1 for that fixture. Shrinking attack/defence toward 0 (= league average) is
# the standard fix and is exactly the right prior: a team we have barely seen
# should be assumed average, not extreme.
# ⚠️ THE INTERCEPT IS WHY THE RIDGE IS SAFE. With lambda = exp(atk - dfn + gamma)
# and no intercept, the LEAGUE'S SCORING LEVEL is carried by mean(dfn) — so
# penalising dfn^2 drags every league's goal rate toward exp(gamma) home and 1.0
# away, which is an arbitrary anchor, not the league average. Measured on the
# first regularised walk-forward: predicted mean total goals fell to 2.952
# against an actual 3.060. An explicit intercept, EXCLUDED from the penalty,
# absorbs the level so the ridge only ever shrinks a team toward its league's
# average rather than toward a constant.
RIDGE = 0.02
# Teams below this many appearances in the fit window are still fitted (dropping
# them would bias the league's own level) but the ridge does the work of keeping
# them near average.
PARAM_BOUND = 1.5       # was 3.0; exp(1.5+1.5+1) = 55 goals is still generous


def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score correction. Returns 1 outside the four cells.

    Note the asymmetry between (0,1) and (1,0): the correction is NOT symmetric
    in the two teams, which is the point — it encodes that 1-0 and 0-1 are
    mispriced by independent Poissons in opposite directions.
    """
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _log_pois(k: int, lam: float) -> float:
    return k * math.log(lam) - lam - math.lgamma(k + 1)


@dataclass
class DCFit:
    """A fitted league. `atk`/`dfn` are per-team; `gamma` is home advantage."""
    teams: list[str]
    atk: dict[str, float]
    dfn: dict[str, float]
    intercept: float
    gamma: float
    rho: float
    n_matches: int
    converged: bool
    loglik: float = field(default=float("nan"))

    def rates(self, home: str, away: str) -> tuple[float, float] | None:
        """(λ, μ) for a fixture, or None when either team was not in the fit.
        None is deliberate: silently substituting a league-average team is how a
        model ends up 'predicting' fixtures it knows nothing about."""
        if home not in self.atk or away not in self.atk:
            return None
        lam = math.exp(self.intercept + self.atk[home] - self.dfn[away] + self.gamma)
        mu = math.exp(self.intercept + self.atk[away] - self.dfn[home])
        return lam, mu

    def score_matrix(self, home: str, away: str, max_goals: int = MAX_GOALS):
        r = self.rates(home, away)
        if r is None:
            return None
        return score_matrix(r[0], r[1], self.rho, max_goals)


def score_matrix(lam: float, mu: float, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """(max_goals+1)² matrix of P(home=x, away=y), renormalised to sum to 1.

    Renormalisation absorbs both the truncation tail and the fact that τ does
    not preserve total mass; without it every derived probability is biased low
    by the same factor, which is invisible in ranking metrics (AUC) and fatal in
    log-loss.
    """
    x = np.arange(max_goals + 1)
    lp_h = x * np.log(lam) - lam - np.array([math.lgamma(k + 1) for k in x])
    lp_a = x * np.log(mu) - mu - np.array([math.lgamma(k + 1) for k in x])
    m = np.exp(lp_h[:, None] + lp_a[None, :])
    for i in (0, 1):
        for j in (0, 1):
            m[i, j] *= tau(i, j, lam, mu, rho)
    m = np.clip(m, 1e-15, None)
    return m / m.sum()


def prob_over(matrix: np.ndarray, line: float) -> float:
    """P(total goals > line). `line` is a .5 line, so no push case exists."""
    idx = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    return float(matrix[idx > line].sum())


def prob_1x2(matrix: np.ndarray) -> tuple[float, float, float]:
    h = float(np.tril(matrix, -1).sum())     # home goals > away goals
    d = float(np.trace(matrix))
    a = float(np.triu(matrix, 1).sum())
    return h, d, a


def fit(matches, xi: float = 0.0, ref_date=None, max_iter: int = 200,
        ridge: float = RIDGE) -> DCFit | None:
    """Fit one league.

    `matches` — iterable of (home, away, home_goals, away_goals, date).
    `xi`      — exponential time-decay rate per DAY. 0 disables decay.
    `ref_date`— decay is measured back from here (the prediction date), NOT from
                the newest match in the data: using the data's own max would make
                the weights depend on what happened to be collected.

    Returns None when the league has too little to fit. Identifiability is fixed
    by mean(atk) = 0 — without a constraint, (atk + c, dfn + c) is the same model
    for any c and the optimiser wanders.
    """
    data = list(matches)
    if len(data) < 20:
        return None
    teams = sorted({m[0] for m in data} | {m[1] for m in data})
    if len(teams) < 4:
        return None
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    hi = np.array([idx[m[0]] for m in data])
    ai = np.array([idx[m[1]] for m in data])
    hg = np.array([int(m[2]) for m in data])
    ag = np.array([int(m[3]) for m in data])

    if xi > 0 and ref_date is not None:
        age = np.array([max((ref_date - m[4]).days, 0) for m in data], dtype=float)
        w = np.exp(-xi * age)
    else:
        w = np.ones(len(data))
    # A league whose entire history has decayed to nothing carries no information;
    # fitting it produces confident nonsense.
    if w.sum() < 10.0:
        return None

    lg_h = np.array([math.lgamma(k + 1) for k in hg])
    lg_a = np.array([math.lgamma(k + 1) for k in ag])
    low = (hg <= 1) & (ag <= 1)

    def neg_ll(p):
        # mean(atk) = mean(dfn) = 0; the level lives in `intercept`, which is not
        # penalised. Constraining BOTH is what makes the ridge mean "shrink this
        # team toward its league average" rather than "shrink this league toward
        # one goal a side".
        atk = np.concatenate([p[:n - 1], [-p[:n - 1].sum()]])
        dfn = np.concatenate([p[n - 1:2 * n - 2], [-p[n - 1:2 * n - 2].sum()]])
        intercept, gamma, rho = p[-3], p[-2], p[-1]
        la = np.exp(np.clip(intercept + atk[hi] - dfn[ai] + gamma, -5, 5))
        mu = np.exp(np.clip(intercept + atk[ai] - dfn[hi], -5, 5))
        ll = (hg * np.log(la) - la - lg_h) + (ag * np.log(mu) - mu - lg_a)
        if low.any():
            t = np.ones(len(data))
            m00 = low & (hg == 0) & (ag == 0)
            m01 = low & (hg == 0) & (ag == 1)
            m10 = low & (hg == 1) & (ag == 0)
            m11 = low & (hg == 1) & (ag == 1)
            t[m00] = 1.0 - la[m00] * mu[m00] * rho
            t[m01] = 1.0 + la[m01] * rho
            t[m10] = 1.0 + mu[m10] * rho
            t[m11] = 1.0 - rho
            # τ can go non-positive for extreme (λ,μ,ρ); the likelihood is
            # undefined there, so push the optimiser back rather than log(≤0).
            t = np.clip(t, 1e-9, None)
            ll = ll + np.log(t)
        # Ridge on attack/defence only — never on gamma or rho, which are
        # league-level and well observed. Scaled by the total weight so the
        # penalty means the same thing in a 60-match league and a 2,000-match
        # one.
        pen = ridge * w.sum() * float((atk * atk).sum() + (dfn * dfn).sum()) / n
        return -float((w * ll).sum()) + pen

    # intercept starts at the log of the observed mean goals per side, so the
    # optimiser begins at the right LEVEL and only has to learn the spread.
    _mean_gs = max((hg.mean() + ag.mean()) / 2.0, 0.05)
    p0 = np.concatenate([np.zeros(n - 1), np.zeros(n - 1),
                         [math.log(_mean_gs), 0.25, -0.05]])
    bounds = ([(-PARAM_BOUND, PARAM_BOUND)] * (n - 1)
              + [(-PARAM_BOUND, PARAM_BOUND)] * (n - 1)
              + [(-2.0, 2.0), (-1, 1), RHO_BOUNDS])
    res = minimize(neg_ll, p0, method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": max_iter})
    p = res.x
    atk = np.concatenate([p[:n - 1], [-p[:n - 1].sum()]])
    dfn = np.concatenate([p[n - 1:2 * n - 2], [-p[n - 1:2 * n - 2].sum()]])
    return DCFit(teams=teams,
                 atk={t: float(atk[i]) for t, i in idx.items()},
                 dfn={t: float(dfn[i]) for t, i in idx.items()},
                 intercept=float(p[-3]), gamma=float(p[-2]), rho=float(p[-1]),
                 n_matches=len(data), converged=bool(res.success),
                 loglik=-float(res.fun))
