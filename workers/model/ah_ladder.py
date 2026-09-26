"""AH LADDER FIT ([[#187]] twin, pre-registered 2026-09-26 in dev/active/ah-market-bot-prereg.md "TWIN — LADDER FIT").

WHY. 12 h+ before kickoff Pinnacle quotes ~3.5 Asian-handicap rungs per fetch while the soft books quote ~7.5,
so a same-rung rule cannot price most early rungs. This fits ONE goal-difference distribution to every rung
Pinnacle does quote in a fetch and reads a fair price for any rung off it.

THE MODEL. D = home goals − away goals ~ Skellam(μh, μa), truncated to [−15, 15]. For a HOME line L the
break-even probability of backing home is q(L) = W / (W + Lo), where W / Lo are the stake-weighted chances the
bet wins / loses — a quarter line is half the stake on each neighbouring line, a whole-line push is neither.
That is the same quantity a 2-way de-vig of the two prices gives (fair odds = 1 / q), so fitted and quoted
rungs are directly comparable. The away side is 1 − q(L).

Pure numpy + scipy; used by both the backtest (scripts/analysis/ah_market/backtest.py) and the live twin bot.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.stats import skellam

D = np.arange(-15, 16)
MAX_RESID = 0.03          # a fit that misses any quoted rung by more than this is rejected
MIN_RUNGS = 2


def _halves(line: float) -> tuple[float, ...]:
    f = round(abs(line) % 1, 2)
    return (line - 0.25, line + 0.25) if f in (0.25, 0.75) else (line,)


def q_home(pmf: np.ndarray, line: float) -> float:
    """Break-even probability of the HOME side at home line `line` under the distribution `pmf` over D."""
    w = lo = 0.0
    hs = _halves(line)
    for h in hs:
        x = D + h
        w += pmf[x > 1e-9].sum() / len(hs)
        lo += pmf[x < -1e-9].sum() / len(hs)
    return float(w / (w + lo)) if (w + lo) > 0 else float("nan")


def pmf_of(mu_h: float, mu_a: float) -> np.ndarray:
    p = skellam.pmf(D, mu_h, mu_a)
    return p / p.sum()


def fit(rungs: dict[float, float]) -> tuple[float, float, float] | None:
    """rungs: home line -> observed (de-vigged) home break-even probability. Returns (μh, μa, max |resid|)
    or None when there are too few rungs or the fit misses a quoted rung by more than MAX_RESID."""
    items = [(float(l), float(q)) for l, q in rungs.items() if 0 < q < 1]
    if len(items) < MIN_RUNGS:
        return None
    lines = np.array([l for l, _ in items])
    obs = np.array([q for _, q in items])

    def loss(x):
        mh, ma = np.exp(x)
        pm = pmf_of(mh, ma)
        return float(sum((q_home(pm, l) - q) ** 2 for l, q in zip(lines, obs)))

    best = None
    for start in ((0.3, 0.1), (0.1, 0.3), (0.2, 0.2)):   # log(1.35), log(1.1) … around typical goal rates
        r = minimize(loss, np.array(start), method="Nelder-Mead",
                     options={"xatol": 1e-5, "fatol": 1e-10, "maxiter": 400})
        if best is None or r.fun < best.fun:
            best = r
    mh, ma = np.exp(best.x)
    pm = pmf_of(mh, ma)
    resid = max(abs(q_home(pm, l) - q) for l, q in zip(lines, obs))
    if resid > MAX_RESID:
        return None
    return float(mh), float(ma), float(resid)


def fitted_q(fit_result: tuple[float, float, float], line: float, side: str) -> float:
    q = q_home(pmf_of(fit_result[0], fit_result[1]), line)
    return q if side == "home" else 1.0 - q


# ── FAST GRID FIT (the one the backtest and the live bot use) ────────────────────────────────────────────
# Least squares over a precomputed (μh, μa) grid, step 0.025 on [0.1, 4.5] — ~31k pairs × 41 home lines
# (−5 … +5 by 0.25). A grid point's q is within ~0.002 of the continuous optimum on every rung, far inside
# MAX_RESID. Built lazily once per process (~6 s).
GRID_MU = np.round(np.arange(0.1, 4.5001, 0.025), 4)
GRID_LINES = np.round(np.arange(-5.0, 5.0001, 0.25), 2)
_GRID: tuple[np.ndarray, np.ndarray] | None = None


def _grid() -> tuple[np.ndarray, np.ndarray]:
    global _GRID
    if _GRID is None:
        mh, ma = np.meshgrid(GRID_MU, GRID_MU, indexing="ij")
        mh, ma = mh.ravel(), ma.ravel()
        pm = skellam.pmf(D[None, :], mh[:, None], ma[:, None])
        pm /= pm.sum(axis=1, keepdims=True)
        q = np.empty((len(mh), len(GRID_LINES)))
        for j, line in enumerate(GRID_LINES):
            hs = _halves(float(line))
            w = sum((pm * ((D + h) > 1e-9)).sum(axis=1) for h in hs) / len(hs)
            lo = sum((pm * ((D + h) < -1e-9)).sum(axis=1) for h in hs) / len(hs)
            q[:, j] = w / (w + lo)
        _GRID = (np.stack([mh, ma], axis=1), q)
    return _GRID


def _col(line: float) -> int | None:
    j = int(round((float(line) + 5.0) / 0.25))
    return j if 0 <= j < len(GRID_LINES) and abs(GRID_LINES[j] - line) < 1e-9 else None


def fit_grid(rungs: dict[float, float]) -> tuple[int, float] | None:
    """(grid row, max |resid|) or None — same acceptance rule as fit(). Rungs outside ±5 are ignored."""
    items = [(c, float(q)) for l, q in rungs.items() if 0 < q < 1 and (c := _col(float(l))) is not None]
    if len(items) < MIN_RUNGS:
        return None
    params, q = _grid()
    cols = np.array([c for c, _ in items])
    obs = np.array([v for _, v in items])
    err = q[:, cols] - obs[None, :]
    i = int(np.argmin((err ** 2).sum(axis=1)))
    resid = float(np.abs(err[i]).max())
    return None if resid > MAX_RESID else (i, resid)


def grid_q(fit_result: tuple[int, float], line: float, side: str) -> float | None:
    c = _col(line)
    if c is None:
        return None
    qh = float(_grid()[1][fit_result[0], c])
    return qh if side == "home" else 1.0 - qh
