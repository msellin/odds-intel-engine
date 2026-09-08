#!/usr/bin/env python3
"""1H-MODEL-EDGE-TEST — does a first-half goals model beat the 1H market?

Same discipline that killed BTTS/AH: fit on TRAIN, validate on untouched TEST,
and ask two questions the ROI number can't:
  (A) does our 1H model OUT-RANK the 1H market (AUC per outcome)?
  (B) does it ADD info beyond the market price (nested multinomial-ish, OOS)?

The model is a first-half Dixon-Coles-lite Poisson: team 1H attack/defence
multipliers fit from stored HT goals (the genuinely NEW signal — first-half
scoring tendency, not a re-projection of the full-match model). Pre-match only,
chronological split, no leakage (rates fit on TRAIN dates only).

Read-only. Uses matches.ht_score_* (1H-HT-GOALS) + odds_snapshots 1x2_1h.
"""
from __future__ import annotations
import sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.joint_probability import build_joint_matrix, prob_event  # noqa: E402

SPLIT = 0.7
RHO = -0.05  # mild Dixon-Coles low-score correlation


def load():
    ht = pd.DataFrame(execute_query("""
        SELECT id::text mid, date, home_team_id::text h, away_team_id::text a,
               ht_score_home hh, ht_score_away ha
        FROM matches
        WHERE ht_score_home IS NOT NULL AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
    """)).dropna().sort_values("date").reset_index(drop=True)
    # 1H 1x2 market (latest pre-match per selection), matches that also have HT goals
    odds = pd.DataFrame(execute_query("""
        WITH latest AS (
          SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
          WHERE o.market='1x2_1h' AND o.timestamp<=m.date AND m.ht_score_home IS NOT NULL
          ORDER BY o.match_id,o.selection,o.timestamp DESC)
        SELECT mid,selection,odds FROM latest
    """))
    piv = odds.pivot_table(index="mid", columns="selection", values="odds", aggfunc="first").dropna()
    piv.columns = [f"o_{c}" for c in piv.columns]
    return ht, piv


def fit_rates(train: pd.DataFrame):
    """First-half attack/defence multipliers per team (home & away split)."""
    lg_h = train.hh.mean(); lg_a = train.ha.mean()
    gf_h = defaultdict(float); ga_h = defaultdict(float); n_h = defaultdict(int)
    gf_a = defaultdict(float); ga_a = defaultdict(float); n_a = defaultdict(int)
    for r in train.itertuples():
        gf_h[r.h]+=r.hh; ga_h[r.h]+=r.ha; n_h[r.h]+=1
        gf_a[r.a]+=r.ha; ga_a[r.a]+=r.hh; n_a[r.a]+=1
    # shrink to league mean (Bayesian-ish) with a prior weight K
    K=6
    def mult(gf,n,base): return (gf+K*base)/((n+K)*base)
    att_h={t: mult(gf_h[t],n_h[t],lg_h) for t in n_h}
    def_h={t: mult(ga_h[t],n_h[t],lg_a) for t in n_h}   # home team's defence vs away scoring
    att_a={t: mult(gf_a[t],n_a[t],lg_a) for t in n_a}
    def_a={t: mult(ga_a[t],n_a[t],lg_h) for t in n_a}
    return dict(lg_h=lg_h, lg_a=lg_a, att_h=att_h, def_h=def_h, att_a=att_a, def_a=def_a)


def predict_1h(row, R):
    lam_h = R["lg_h"] * R["att_h"].get(row.h,1.0) * R["def_a"].get(row.a,1.0)
    lam_a = R["lg_a"] * R["att_a"].get(row.a,1.0) * R["def_h"].get(row.h,1.0)
    lam_h=max(0.05,min(lam_h,3.0)); lam_a=max(0.05,min(lam_a,3.0))
    M=build_joint_matrix(lam_h,lam_a,RHO)
    return prob_event(M,"home"), prob_event(M,"draw"), prob_event(M,"away")


def main():
    ht, piv = load()
    n_ht=len(ht); print(f"HT-goals matches available for rate fit: {n_ht:,}")
    # chronological split on the HT universe; rates fit on TRAIN dates only
    cut_date = ht.date.quantile(SPLIT)
    train = ht[ht.date<=cut_date]
    R = fit_rates(train)
    # TEST = matches with 1H odds AND HT goals AND date in the TEST window (no leakage)
    test = ht[ht.date>cut_date].merge(piv, left_on="mid", right_index=True).reset_index(drop=True)
    if len(test)<150:
        print(f"[!] only {len(test)} test matches with 1H odds in the TEST window — "
              f"HT backfill still filling; re-run after it completes for a robust read.")
    print(f"train (rate fit) n={len(train):,}   test (edge) n={len(test):,}")
    # model probs
    mp = test.apply(lambda r: predict_1h(r, R), axis=1, result_type="expand")
    mp.columns=["m_home","m_draw","m_away"]; test=pd.concat([test,mp],axis=1)
    # market implied (de-vig 3-way)
    inv=test[["o_home","o_draw","o_away"]].rdiv(1.0)
    s=inv.sum(axis=1)
    for c in ["home","draw","away"]: test[f"k_{c}"]=inv[f"o_{c}"]/s
    # actual 1H outcome
    test["y"]=np.where(test.hh>test.ha,"home",np.where(test.hh<test.ha,"away","draw"))

    print("\n(A) DISCRIMINATION — one-vs-rest AUC on TEST (model vs market):")
    for c in ["home","draw","away"]:
        yb=(test.y==c).astype(int)
        if yb.nunique()<2: continue
        am=roc_auc_score(yb,test[f"k_{c}"]); ad=roc_auc_score(yb,test[f"m_{c}"])
        print(f"    {c:5s}: market {am:.3f}  model {ad:.3f}  Δ={ad-am:+.3f}")

    # (C) incremental info: does model add beyond market? per-outcome nested logit, OOS split inside test
    def logit(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
    ti=int(len(test)*0.6); tr=slice(0,ti); teN=slice(ti,len(test))
    print("\n(C) INCREMENTAL INFO — nested logit TEST log-loss (market vs market+model):")
    for c in ["home","draw","away"]:
        yb=(test.y==c).astype(int).values
        if yb[tr].sum()<10 or yb[teN].sum()<5: continue
        Xm=logit(test[f"k_{c}"].values).reshape(-1,1)
        Xmm=np.column_stack([logit(test[f"k_{c}"].values),logit(test[f"m_{c}"].values)])
        lm=LogisticRegression(C=1e6).fit(Xm[tr],yb[tr]); lmm=LogisticRegression(C=1e6).fit(Xmm[tr],yb[tr])
        llm=log_loss(yb[teN],lm.predict_proba(Xm[teN])[:,1]); llmm=log_loss(yb[teN],lmm.predict_proba(Xmm[teN])[:,1])
        print(f"    {c:5s}: market {llm:.4f}  +model {llmm:.4f}  Δ={llmm-llm:+.5f}  coef[mkt,model]={lmm.coef_[0].round(3)}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
