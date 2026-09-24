"""COMBINED 1X2 MODEL ([[#141]] round 3b, COMB-HYB) — ratings + consensus + Pinnacle (+ AF).

Adopted 2026-09-24 on a pre-registered test (dev/active/1x2-model-rebuild-plan.md,
"ROUND 3b"): on 12,640 matches from 2026-08-31 it scored log-loss 0.9763 vs 1.0711
for the shipped XGBoost head and 0.9810 for the simple rule "Pinnacle, else
consensus, else rating"; on Pinnacle-priced matches it beat Pinnacle alone by
0.0017 (closing prices).

One multinomial logit PER AVAILABILITY GROUP, because a missing source is not a
zero:
    P&C   rating + consensus (+ log n_books) + Pinnacle
    P     rating + Pinnacle
    C     rating + consensus (+ log n_books)
    none  rating + API-Football's prediction (percent + comparison.total)
API-Football is used ONLY in "none": it added information where no book prices
the match and hurt where the market exists. All sources enter as log-odds vs the
draw. Parameters are plain JSON so they can be stored and re-applied cheaply.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GROUPS = ("P&C", "P", "C", "none")
MIN_ROWS = 300


def lo(p: np.ndarray) -> np.ndarray:
    """(n,3) probabilities -> (n,2) log-odds vs draw."""
    p = np.clip(np.asarray(p, float), 1e-4, 1)
    return np.stack([np.log(p[:, 0] / p[:, 1]), np.log(p[:, 2] / p[:, 1])], 1)


def assign_groups(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["has_c"] = d["c_h"].notna() if "c_h" in d else False
    d["has_p"] = d["pin_h"].notna() if "pin_h" in d else False
    d["has_af"] = (d["af_h"].notna() & d["af_th"].notna()) if "af_h" in d else False
    d["group"] = np.where(d.has_p & d.has_c, "P&C", np.where(d.has_p, "P", np.where(d.has_c, "C", "none")))
    return d


def design(d: pd.DataFrame, group: str, use_af: bool | None = None,
           extra: list[str] | None = None) -> np.ndarray:
    """`extra`: optional additional numeric columns (e.g. round-3c XI features); NaN -> 0
    plus one has-flag per column, so a missing lineup is not read as a real zero."""
    if use_af is None:
        use_af = group == "none"
    cols = [lo(d[["r_h", "r_d", "r_a"]].to_numpy())]
    if group in ("P&C", "C"):
        cols += [lo(d[["c_h", "c_d", "c_a"]].to_numpy()), np.log(d["n_books"].to_numpy(float))[:, None]]
    if group in ("P&C", "P"):
        cols.append(lo(d[["pin_h", "pin_d", "pin_a"]].to_numpy()))
    if use_af:
        afp = d[["af_h", "af_d", "af_a"]].astype(float).fillna(1 / 3).to_numpy()
        th = d["af_th"].astype(float).fillna(0.5).clip(0.02, 0.98).to_numpy()
        cols += [lo(afp), np.log(th / (1 - th))[:, None], d["has_af"].to_numpy(float)[:, None]]
    for c in extra or ():
        v = d[c].astype(float)
        cols += [v.fillna(0.0).to_numpy()[:, None], v.notna().to_numpy(float)[:, None]]
    return np.hstack(cols)


def fit(d: pd.DataFrame, extra: list[str] | None = None) -> dict:
    """d: finished matches with r_*, c_*, n_books, pin_*, af_*, y. Returns JSON-able params."""
    from sklearn.linear_model import LogisticRegression
    d = assign_groups(d)
    params = {}
    for g in GROUPS:
        a = d[d.group == g]
        if len(a) < MIN_ROWS:
            continue
        m = LogisticRegression(max_iter=3000, C=1.0).fit(design(a, g, extra=extra), a.y)
        params[g] = {"classes": [int(c) for c in m.classes_], "coef": m.coef_.tolist(),
                     "intercept": m.intercept_.tolist(), "n": int(len(a)), "extra": list(extra or [])}
    return params


def predict(d: pd.DataFrame, params: dict) -> tuple[np.ndarray, np.ndarray]:
    """Returns (probabilities (n,3) in home/draw/away order, group labels). A group
    with no fitted params falls back to the rule source (Pinnacle > consensus > rating)."""
    d = assign_groups(d)
    P = d[["r_h", "r_d", "r_a"]].to_numpy(float).copy()
    c = d.has_c.to_numpy(); P[c] = d.loc[c, ["c_h", "c_d", "c_a"]].to_numpy(float)
    p = d.has_p.to_numpy(); P[p] = d.loc[p, ["pin_h", "pin_d", "pin_a"]].to_numpy(float)
    for g, prm in params.items():
        mk = d.group.to_numpy() == g
        if not mk.any():
            continue
        z = design(d[mk], g, extra=prm.get("extra")) @ np.asarray(prm["coef"]).T + np.asarray(prm["intercept"])
        z = np.exp(z - z.max(1, keepdims=True))
        z = z / z.sum(1, keepdims=True)
        out = np.zeros((mk.sum(), 3))
        for j, cl in enumerate(prm["classes"]):
            out[:, cl] = z[:, j]
        P[mk] = out
    return P, d.group.to_numpy()
