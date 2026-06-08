"""OU25-DEDICATED-MODEL — Poisson goals model + Dixon-Coles wrapper.

The wrapper exposes an sklearn-classifier-shaped `predict_proba` so the
production OU inference path (`xgboost_ensemble.get_xgboost_prediction`) can
treat it identically to the v14 over_under XGBoost classifier.

`bundle_ou["over_under"].predict_proba(X)[0]` → array of [under25_prob, over25_prob]
"""
from __future__ import annotations

import math

import numpy as np


DIXON_COLES_RHO = -0.18
MAX_GOALS = 8


def _dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles low-score correction. Only affects (0,0)/(0,1)/(1,0)/(1,1)."""
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _over25_prob_from_lambdas(lam_h: float, lam_a: float, rho: float = DIXON_COLES_RHO) -> float:
    """Build joint goal matrix via Poisson × DC τ; return P(home + away > 2.5)."""
    from scipy.stats import poisson

    lam_h = max(0.05, float(lam_h))
    lam_a = max(0.05, float(lam_a))

    pmf_h = poisson.pmf(range(MAX_GOALS), lam_h)
    pmf_a = poisson.pmf(range(MAX_GOALS), lam_a)

    over25 = 0.0
    total = 0.0
    for h in range(MAX_GOALS):
        for a in range(MAX_GOALS):
            p = pmf_h[h] * pmf_a[a] * _dc_tau(h, a, lam_h, lam_a, rho)
            total += p
            if h + a > 2:
                over25 += p
    return float(over25 / total) if total > 0 else 0.5


class Ou25PoissonWrapper:
    """sklearn-classifier-shaped wrapper around two count:poisson regressors.

    `classes_` = [False, True] mirrors v14's over_under classifier so the
    production inference code at `xgboost_ensemble.py:382-388` resolves the
    over25 column by `classes_.index(True)` without modification.
    """

    classes_ = np.array([False, True])

    def __init__(self, home_goals_model, away_goals_model, feature_cols, dc_rho: float = DIXON_COLES_RHO):
        self.home_goals_model = home_goals_model
        self.away_goals_model = away_goals_model
        self.feature_cols = list(feature_cols)
        self.dc_rho = float(dc_rho)

    def _predict_lambdas(self, X) -> tuple[np.ndarray, np.ndarray]:
        import pandas as pd

        if hasattr(X, "columns"):
            X_ordered = X[self.feature_cols]
        else:
            X_ordered = pd.DataFrame(X, columns=self.feature_cols)
        lam_h = np.maximum(0.05, self.home_goals_model.predict(X_ordered))
        lam_a = np.maximum(0.05, self.away_goals_model.predict(X_ordered))
        return lam_h, lam_a

    def predict_proba(self, X) -> np.ndarray:
        lam_h, lam_a = self._predict_lambdas(X)
        over = np.array([_over25_prob_from_lambdas(h, a, self.dc_rho)
                         for h, a in zip(lam_h, lam_a)])
        under = 1.0 - over
        return np.column_stack([under, over])

    def predict(self, X) -> np.ndarray:
        return self.predict_proba(X)[:, 1] > 0.5
