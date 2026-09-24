"""#128 addendum — back over 0.5 at minute 5-10 (still 0-0) instead of pre-match?

Owner, 2026-09-24: "what happens with the 0.5 odds 5-10 minutes into play — what if we
placed the bet there?" Stated before running (dev/active/ou-low-lines-bias-sweep.md,
addendum): the in-play price at 0-0 is fair minus the in-play margin (the 1x2 draw
calibration earlier the same day matched within 0.4pp), so waiting should not beat the
pre-match price; with ~100-200 bets only a glaring mispricing is detectable.

Sources: Epicbet in-play boards (`inplay_book_quotes`, placeable, from 2026-09-15) and
AF live (`live_match_snapshots`, NOT placeable, until 2026-08-21) for sample size.
Entry = the first 0-0 snapshot at minute 5..10 whose over-0.5 price is live.
Guards: AF score age <= 120 s (Epicbet rows carry `af_age_s`); over 0.5 < over 1.5 in the
same snapshot when both are listed (ladder, ANALYSIS_GOTCHAS §80).

    python3 scripts/ou05_inplay_early_entry.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import psycopg2
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workers.model.devig import devig

MIN_LO, MIN_HI = 5, 10
MAX_AF_AGE_S = 120

EPIC_SQL = """
select q.match_id, q.minute, q.captured_at, q.af_age_s,
       (e->>'line')::numeric as line,
       max(case when s->>'sel' = 'Over' and not (s->>'suspended')::boolean then (s->>'odds')::numeric end) as over,
       max(case when s->>'sel' = 'Under' and not (s->>'suspended')::boolean then (s->>'odds')::numeric end) as under
from inplay_book_quotes q
cross join lateral jsonb_array_elements(q.markets) e
cross join lateral jsonb_array_elements(e->'sel') s
where q.book = 'Epicbet' and q.minute between %(lo)s and %(hi)s
  and q.score_home = 0 and q.score_away = 0
  and e->>'fam' = 'ou' and e->>'gid' = '15' and (e->>'line')::numeric in (0.5, 1.5)
group by 1, 2, 3, 4, 5
"""

AF_SQL = """
select match_id, minute, captured_at, live_ou_05_over as over, live_ou_05_under as under,
       live_ou_15_over as over15
from live_match_snapshots
where score_home = 0 and score_away = 0 and minute between %(lo)s and %(hi)s
  and live_ou_05_over is not null
"""

PRE_SQL = """
select distinct on (o.match_id, o.selection) o.match_id, o.selection, o.odds
from odds_snapshots o join matches m on m.id = o.match_id
where o.bookmaker = 'Epicbet' and o.market = 'over_under_05'
  and not coalesce(o.is_live, false) and o.timestamp <= m.date
order by o.match_id, o.selection, o.timestamp desc
"""

RES_SQL = """select id as match_id, score_home + score_away as goals from matches
             where status = 'finished' and score_home is not null"""


def summarise(label, pnl, won, odds, implied=None):
    pnl = np.asarray(pnl, float); n = len(pnl)
    if n == 0:
        print(f"  {label:44} n=0"); return
    t, p = (stats.ttest_1samp(pnl, 0.0) if n > 2 else (np.nan, np.nan))
    se = pnl.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    imp = f" implied {np.mean(implied):.3f}" if implied is not None else ""
    print(f"  {label:44} n={n:4}  avg odds {np.mean(odds):.3f}{imp}  hit {np.mean(won):.3f}  "
          f"ROI {pnl.mean():+6.2%} ±{1.96 * se:.1%}  (t={t:+.2f})")


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rd = lambda q: pd.read_sql(q, conn, params=dict(lo=MIN_LO, hi=MIN_HI))
    res = pd.read_sql(RES_SQL, conn)

    # ── Epicbet in-play ──
    ep = rd(EPIC_SQL)
    ep = ep[ep.af_age_s.isna() | (ep.af_age_s <= MAX_AF_AGE_S)]
    w = ep.pivot_table(index=["match_id", "captured_at", "minute"], columns="line",
                       values=["over", "under"], aggfunc="first")
    w.columns = [f"{a}{str(b).replace('.', '')}" for a, b in w.columns]
    w = w.reset_index().dropna(subset=["over05", "under05"])
    if "over15" in w:
        w = w[w.over15.isna() | (w.over05 < w.over15)]
    w = w.sort_values("captured_at").drop_duplicates("match_id").merge(res, on="match_id")
    w["won"] = w.goals > 0
    w["pnl"] = np.where(w.won, w.over05 - 1, -1.0)
    w["implied"] = [devig([a, b])[0] for a, b in zip(w.over05, w.under05)]

    pre = pd.read_sql(PRE_SQL, conn).pivot_table(index="match_id", columns="selection", values="odds").dropna()
    pre = pre.reset_index().rename(columns={"over": "pre_over", "under": "pre_under"}).merge(res, on="match_id")
    pre["won"] = pre.goals > 0
    pre["pnl"] = np.where(pre.won, pre.pre_over - 1, -1.0)

    print(f"#128 addendum — over 0.5 entered at minute {MIN_LO}-{MIN_HI} with the score 0-0\n")
    print("EPICBET (placeable)")
    summarise("in-play entry, min 5-10 at 0-0", w.pnl, w.won, w.over05, w.implied)
    both = w.merge(pre[["match_id", "pre_over"]], on="match_id")
    if len(both):
        summarise("same matches, PRE-MATCH price instead", np.where(both.won, both.pre_over - 1, -1),
                  both.won, both.pre_over)
        print(f"    paired: in-play/pre-match odds ratio median {(both.over05 / both.pre_over).median():.3f}"
              f"  (n={len(both)})")
    sub = pre[pre.match_id.isin(ep.match_id.unique())]
    summarise("pre-match bet on EVERY in-play-covered match", sub.pnl, sub.won, sub.pre_over)
    if len(sub):
        share_00 = sub.match_id.isin(w.match_id).mean()
        print(f"    share of those matches still bettable at 0-0 in the window: {share_00:.0%}")

    # ── AF live (not placeable) ──
    af = rd(AF_SQL)
    af = af[af.over15.isna() | (af.over < af.over15)]
    af = af.sort_values("captured_at").drop_duplicates("match_id").merge(res, on="match_id")
    af = af[(af.over > 1) & (af.under > 1)]
    af["won"] = af.goals > 0
    af["pnl"] = np.where(af.won, af.over - 1, -1.0)
    af["implied"] = [devig([a, b])[0] for a, b in zip(af.over, af.under)]
    print("\nAPI-FOOTBALL LIVE (not placeable; 2026-05..08-21) — sample-size check")
    summarise("in-play entry, min 5-10 at 0-0", af.pnl, af.won, af.over, af.implied)
    for lo, hi in [(1.0, 1.08), (1.08, 1.15), (1.15, 9)]:
        x = af[(af.over >= lo) & (af.over < hi)]
        summarise(f"  odds band {lo:.2f}-{hi:.2f}", x.pnl, x.won, x.over, x.implied)


if __name__ == "__main__":
    main()
