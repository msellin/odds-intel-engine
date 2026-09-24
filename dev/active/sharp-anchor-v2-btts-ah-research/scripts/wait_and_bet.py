"""Backtest: bet pre-match vs wait until minute M (still 0-0) and take the in-play price.

Data: live_match_snapshots (AF live odds, May..2026-08-21), Pinnacle closing 1x2 + OU2.5,
final scores, and bots' pre-match picks in simulated_bets.
"""
import os, numpy as np, pandas as pd, psycopg2
from scipy.stats import poisson

conn = psycopg2.connect(os.environ["DATABASE_URL"])
q = lambda s: pd.read_sql(s, conn)

MINS = [15, 30, 45, 60, 70]

live = q("""
select match_id, minute, live_1x2_home h, live_1x2_draw d, live_1x2_away a,
       live_ou_25_over o25, live_ou_25_under u25, captured_at
from live_match_snapshots
where score_home=0 and score_away=0 and live_1x2_home is not null
  and minute between 10 and 75
""")
res = q("""select id match_id, score_home fh, score_away fa from matches
           where status='finished' and score_home is not null""")
pin = q("""
select distinct on (o.match_id, market, selection) o.match_id, market, selection, odds
from odds_snapshots o join matches m on m.id=o.match_id
where bookmaker='Pinnacle' and not coalesce(is_live,false)
  and o.timestamp <= m.date and o.timestamp >= m.date - interval '6 hours'
  and market in ('1x2','over_under_25') and m.date > '2026-04-25' and m.date < '2026-08-22'
order by o.match_id, market, selection, o.timestamp desc
""")
bets = q("""
select distinct on (bot_id, match_id, market, selection) bot_id, match_id, market, selection,
       odds_at_pick, result
from simulated_bets
where market in ('1x2','over_under_25') and result in ('won','lost')
  and match_minute_at_pick is null and odds_at_pick > 1
order by bot_id, match_id, market, selection, pick_time
""")

pw = pin.pivot_table(index="match_id", columns=["market", "selection"], values="odds").dropna()
pw.columns = ["p_" + c[1] for c in pw.columns]  # p_away p_draw p_home p_over p_under
pw = pw.rename(columns={"p_over": "p_o25", "p_under": "p_u25"})

# --- de-vig Pinnacle (proportional) and fit independent-Poisson lambdas ---------------
ih, idr, ia = 1 / pw.p_home, 1 / pw.p_draw, 1 / pw.p_away
s = ih + idr + ia
pw["fH"], pw["fD"], pw["fA"] = ih / s, idr / s, ia / s
io, iu = 1 / pw.p_o25, 1 / pw.p_u25
pw["fU"] = iu / (io + iu)

G = np.arange(0.05, 4.01, 0.05)
LH, LA = np.meshgrid(G, G, indexing="ij")
K = np.arange(0, 11)


def probs(lh, la):
    ph = poisson.pmf(K[:, None, None], lh[None]); pa = poisson.pmf(K[:, None, None], la[None])
    J = ph[:, None] * pa[None]  # [i,j,...]
    i, j = np.meshgrid(K, K, indexing="ij")
    H = (J * (i > j)[..., None, None]).sum((0, 1)) if J.ndim == 4 else None
    return J


def grid_tables(lh, la):
    ph = poisson.pmf(K[:, None], lh.ravel()[None])  # (11, n)
    pa = poisson.pmf(K[:, None], la.ravel()[None])
    J = ph[:, None, :] * pa[None, :, :]            # (11,11,n)
    i, j = np.meshgrid(K, K, indexing="ij")
    H = (J * (i > j)[..., None]).sum((0, 1)); D = (J * (i == j)[..., None]).sum((0, 1))
    A = (J * (i < j)[..., None]).sum((0, 1)); U = (J * ((i + j) <= 2)[..., None]).sum((0, 1))
    return H, D, A, U


gH, gD, gA, gU = grid_tables(LH, LA)
F = pw[["fH", "fD", "fA", "fU"]].to_numpy()
best = np.empty(len(F), int)
for n0 in range(0, len(F), 2000):
    f = F[n0:n0 + 2000]
    err = ((f[:, 0, None] - gH) ** 2 + (f[:, 1, None] - gD) ** 2 + (f[:, 2, None] - gA) ** 2
           + (f[:, 3, None] - gU) ** 2)
    best[n0:n0 + 2000] = err.argmin(1)
pw["lh"], pw["la"] = LH.ravel()[best], LA.ravel()[best]


def cond_probs(lh, la, minute):
    """P(home/draw/away/over2.5) given 0-0 at `minute`; ~95 min of play incl. stoppage."""
    r = np.clip((95 - minute) / 95, 0, 1)
    H, D, A, U = grid_tables(np.asarray(lh * r), np.asarray(la * r))
    return H, D, A, 1 - U


# --- pick the 0-0 snapshot nearest each target minute -----------------------------------
rows = []
for M in MINS:
    w = live[(live.minute - M).abs() <= 3].copy()
    w["dist"] = (w.minute - M).abs()
    w = w.sort_values(["match_id", "dist", "captured_at"]).drop_duplicates("match_id")
    w["M"] = M
    rows.append(w)
L = pd.concat(rows).merge(res, on="match_id").merge(pw.reset_index(), on="match_id")
H, D, A, O = cond_probs(L.lh.to_numpy(), L.la.to_numpy(), L.minute.to_numpy())
L["qH"], L["qD"], L["qA"], L["qO"] = H, D, A, O
L["win_h"] = L.fh > L.fa; L["win_d"] = L.fh == L.fa; L["win_a"] = L.fh < L.fa
L["win_o"] = (L.fh + L.fa) >= 3
L["overround"] = 1 / L.h + 1 / L.d + 1 / L.a - 1


def ci(pnl):
    pnl = np.asarray(pnl, float); n = len(pnl)
    m = pnl.mean(); se = pnl.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return n, m, m - 1.96 * se, m + 1.96 * se


print(f"Matches with Pinnacle close + >=1 in-play 0-0 snapshot: {L.match_id.nunique():,}\n")
print("=== 1. MARKET-WIDE: in-play price at 0-0 vs a Pinnacle-anchored fair price ===")
print("EV = live_odds * P(win | 0-0 at M) - 1, where P comes from Pinnacle's closing line")
print("rolled forward with a Poisson clock. ROI = realised. Both per 1-unit bet.\n")
for sel, oc, qc, wc in [("home", "h", "qH", "win_h"), ("draw", "d", "qD", "win_d"),
                        ("away", "a", "qA", "win_a"), ("over2.5", "o25", "qO", "win_o")]:
    for M in MINS:
        x = L[(L.M == M) & L[oc].notna() & (L[oc] > 1) & (L[oc] < 30)]
        if len(x) < 200: continue
        ev = (x[oc] * x[qc] - 1).mean()
        n, roi, lo, hi = ci(np.where(x[wc], x[oc] - 1, -1))
        print(f"  {sel:8} M={M:2}  n={n:5}  avg odds {x[oc].mean():5.2f}  model-EV {ev:+6.1%}"
              f"   realised ROI {roi:+6.1%}  [{lo:+.1%}, {hi:+.1%}]")
    print()

# Pre-match baseline on the same books? Pinnacle close ROI on ALL matches in L (unconditional)
base = pw.reset_index().merge(res, on="match_id")
base = base[base.match_id.isin(L.match_id)]
print("Baseline — betting every selection at PINNACLE CLOSE, same matches, unconditional:")
for sel, oc, cond in [("home", "p_home", base.fh > base.fa), ("draw", "p_draw", base.fh == base.fa),
                      ("away", "p_away", base.fh < base.fa), ("over2.5", "p_o25", base.fh + base.fa >= 3)]:
    n, roi, lo, hi = ci(np.where(cond, base[oc] - 1, -1))
    print(f"  {sel:8} n={n:5}  ROI {roi:+6.1%}  [{lo:+.1%}, {hi:+.1%}]")
print(f"\nIn-play 1x2 overround at 0-0 (median): "
      + ", ".join(f"M{M}: {L[L.M == M].overround.median():.1%}" for M in MINS))
pc = 1 / pw.p_home + 1 / pw.p_draw + 1 / pw.p_away - 1
print(f"Pinnacle closing 1x2 overround (median): {pc.median():.1%}")

# anchor sanity: is the Poisson-conditioned probability calibrated?
print("\nAnchor calibration (mean predicted vs actual win rate, 0-0 states):")
for M in MINS:
    x = L[L.M == M]
    print(f"  M={M:2}  home {x.qH.mean():.3f}/{x.win_h.mean():.3f}   draw {x.qD.mean():.3f}/"
          f"{x.win_d.mean():.3f}   away {x.qA.mean():.3f}/{x.win_a.mean():.3f}   "
          f"over2.5 {x.qO.mean():.3f}/{x.win_o.mean():.3f}   live-implied draw "
          f"{(1 / x.d / (1 + x.overround)).mean():.3f}")

# --- 2. OUR PICKS: bet pre-match vs wait ---------------------------------------------------
print("\n=== 2. OUR PRE-MATCH PICKS: bet now vs wait for 0-0 at M ===")
bets["won"] = bets.result == "won"
colmap = {("1x2", "home"): "h", ("1x2", "draw"): "d", ("1x2", "away"): "a",
          ("over_under_25", "over"): "o25", ("over_under_25", "under"): "u25"}
bets["oc"] = [colmap.get((m, s)) for m, s in zip(bets.market, bets.selection)]
bets = bets[bets.oc.notna()]
bets = bets[bets.match_id.isin(live.match_id.unique())]  # only matches the live feed covered
print(f"Pre-match picks on matches with live coverage: {len(bets):,}")
n, roi, lo, hi = ci(np.where(bets.won, bets.odds_at_pick - 1, -1))
print(f"  A) bet pre-match (as placed)                        n={n:5} ROI {roi:+6.1%} [{lo:+.1%}, {hi:+.1%}]")
for M in [15, 30, 45, 60]:
    lm = L[L.M == M].set_index("match_id")
    b = bets.join(lm[["h", "d", "a", "o25", "u25"]], on="match_id")
    b["live"] = [r[r.oc] if pd.notna(r.get(r.oc, np.nan)) else np.nan for _, r in b.iterrows()]
    still = b[b.live.notna() & (b.live > 1)]
    n1, r1, l1, h1 = ci(np.where(still.won, still.odds_at_pick - 1, -1))
    n2, r2, l2, h2 = ci(np.where(still.won, still.live - 1, -1))
    print(f"  M={M}: picks still 0-0 with a live price: n={n1}")
    print(f"     pre-match price on that subset   ROI {r1:+6.1%} [{l1:+.1%}, {h1:+.1%}]  avg odds {still.odds_at_pick.mean():.2f}")
    print(f"     WAIT & take live price           ROI {r2:+6.1%} [{l2:+.1%}, {h2:+.1%}]  avg odds {still.live.mean():.2f}")
    print(f"     paired diff (live - prematch)    {r2 - r1:+6.1%}")
    for sel in ["h","d","a","o25","u25"]:
        z = still[still.oc==sel]
        if len(z) < 10: continue
        print(f"        {sel:4} n={len(z):4}  median live/prematch odds ratio {(z.live/z.odds_at_pick).median():.3f}"
              f"  ROI prematch {np.where(z.won,z.odds_at_pick-1,-1).mean():+6.1%}  live {np.where(z.won,z.live-1,-1).mean():+6.1%}")
    share = b.match_id.isin(lm.index).mean()
    print(f"     share of picks that are still 0-0 (and priced) at M: {len(still)/len(b):.0%}")
