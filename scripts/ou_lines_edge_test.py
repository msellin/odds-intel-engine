#!/usr/bin/env python3
"""OU-LINES-EDGE-TEST — do O/U 1.5 and 3.5 carry edge like O/U 2.5 does?

TIER A of MARKET-EXPANSION: the goals model ALREADY emits over15/under15 and
over35/under35 predictions (same machinery as the live 2.5). No new model, no
new data. Test with the BTTS/AH discipline:
  (A) discrimination — AUC(model) vs AUC(market), held-out TEST
  (B) incremental info — nested logit y~market vs y~market+model, OOS
  (C) executable ROI — flat-back at best accessible price with an edge gate

Market = all-book de-vigged consensus P(over) for discrimination; best-of-
accessible for ROI. Outcome from final score (half-lines → no push).
Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

ACCESSIBLE = ("Coolbet", "Betano", "Unibet", "Epicbet")
LINES = {"25": 2.5, "15": 1.5, "35": 3.5}


def run_line(tag: str, line: float):
    mkt = f"over_under_{tag}"
    # per (match, book) latest pre-match over & under odds, all books
    od = pd.DataFrame(execute_query(f"""
        WITH latest AS (
          SELECT DISTINCT ON (o.match_id,o.bookmaker,o.selection)
                 o.match_id::text mid,o.bookmaker,o.selection,o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
          WHERE o.market='{mkt}' AND o.selection IN ('over','under')
            AND o.timestamp<=m.date AND m.status='finished' AND m.score_home IS NOT NULL
            AND o.bookmaker NOT LIKE 'api-football%'
          ORDER BY o.match_id,o.bookmaker,o.selection,o.timestamp DESC)
        SELECT mid,bookmaker,
               MAX(odds) FILTER (WHERE selection='over') o_over,
               MAX(odds) FILTER (WHERE selection='under') o_under
        FROM latest GROUP BY mid,bookmaker
    """)).dropna()
    od = od[(od.o_over > 1) & (od.o_under > 1)]
    od["imp"] = (1/od.o_over)/((1/od.o_over)+(1/od.o_under))
    cons = od.groupby("mid").agg(mkt=("imp","median")).reset_index()
    # SINGLE-BOOK executable price (Coolbet, the placement venue) — NOT best-of-books,
    # which + an edge gate reintroduces the line-shop selection artifact (§52) that
    # loses OOS and makes even the profitable 2.5 look negative.
    cb = od[od.bookmaker == "Coolbet"][["mid", "o_over"]].rename(columns={"o_over": "best_over"})
    best = cb.drop_duplicates("mid")

    mo = pd.DataFrame(execute_query(f"""
        SELECT m.id::text mid, m.date,
               po.model_probability::float mdl,
               (m.score_home+m.score_away)::int total
        FROM matches m
        JOIN LATERAL (SELECT model_probability FROM predictions
                      WHERE match_id=m.id AND market='over{tag}' ORDER BY model_version DESC LIMIT 1) po ON true
        WHERE m.status='finished' AND m.score_home IS NOT NULL
    """))
    df = mo.merge(cons, on="mid").merge(best, on="mid").dropna().sort_values("date").reset_index(drop=True)
    df["y"] = (df.total > line).astype(int)
    n = len(df); cut = int(n*0.7); tr = slice(0,cut); te = slice(cut,n)
    mkt_p = df["mkt"].values; mdl = df.mdl.values; y = df.y.values

    print(f"\n=== O/U {line} (over{tag}) — n={n:,} matches, TEST={n-cut:,}, over-rate(test)={y[te].mean():.3f} ===")
    print("(A) TEST AUC:   market %.4f   model %.4f   (Δ=%+.4f)  -> model %s" % (
        roc_auc_score(y[te],mkt_p[te]), roc_auc_score(y[te],mdl[te]),
        roc_auc_score(y[te],mdl[te])-roc_auc_score(y[te],mkt_p[te]),
        "OUT-ranks" if roc_auc_score(y[te],mdl[te])>roc_auc_score(y[te],mkt_p[te]) else "does NOT out-rank"))
    def logit(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
    Xm=logit(mkt_p).reshape(-1,1); Xmm=np.column_stack([logit(mkt_p),logit(mdl)])
    lm=LogisticRegression(C=1e6).fit(Xm[tr],y[tr]); lmm=LogisticRegression(C=1e6).fit(Xmm[tr],y[tr])
    ll_m=log_loss(y[te],lm.predict_proba(Xm[te])[:,1]); ll_mm=log_loss(y[te],lmm.predict_proba(Xmm[te])[:,1])
    print("(B) nested logit TEST log-loss:  market %.4f   +model %.4f  (Δ=%+.5f)  coef[mkt,model]=%s" % (
        ll_m, ll_mm, ll_mm-ll_m, lmm.coef_[0].round(3)))
    # (C) CALIBRATED model-edge ROI — the SAME method that validated O/U 2.5.
    # Raw model probs are over-confident; isotonic-calibrate on TRAIN, then bet OVER
    # at best-accessible price where cal_edge >= thr. Held-out TEST + 3-fold robustness.
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip").fit(mdl[tr], y[tr])
    df = df.copy(); df["cal"] = iso.predict(mdl)
    df_te = df.iloc[te].copy()
    df_te["cal_edge"] = df_te.cal - 1/df_te.best_over
    for thr in (0.03, 0.05, 0.08):
        pick = df_te[df_te.cal_edge >= thr]
        if len(pick) < 20:
            print(f"    cal-edge≥{thr:.0%}: n={len(pick)} (too few)"); continue
        ret = np.where(pick.y==1, pick.best_over-1, -1.0)
        # 3-fold robustness on the picks (chronological)
        pk = pick.sort_values("date"); fsz = max(1,len(pk)//3)
        folds = [pk.iloc[i:i+fsz] for i in range(0,len(pk),fsz)][:3]
        froi = [np.where(f.y==1,f.best_over-1,-1.0).mean()*100 for f in folds if len(f)>=10]
        robust = "ROBUST" if froi and all(r>0 for r in froi) else "not-robust"
        print(f"    cal-edge≥{thr:.0%}: OVER ROI {ret.mean()*100:+.1f}%  n={len(pick)}  odds {pick.best_over.mean():.2f}  folds={[f'{r:+.0f}' for r in froi]} {robust}")


def main():
    for tag, line in LINES.items():
        run_line(tag, line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
