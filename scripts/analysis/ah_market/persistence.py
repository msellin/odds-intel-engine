#!/usr/bin/env python3
"""#187 lever 2 — how long does an AH opportunity last, and how often do we look? (read-only, descriptive)

An opportunity EPISODE = consecutive Pinnacle fetches at which the same (match, book, side, line) is priced at
EV in [3%, 15%) vs Pinnacle's same-line fair price (the bot's rule, backtest.candidates). If most episodes
last several fetches, polling faster adds little; if many are seen at ONE fetch only, more are probably missed
between fetches. Also reports the fetch cadence by time-to-kickoff.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "scripts/analysis/ah_market")
import backtest as bt

runs, pin, m = bt.load()
fair = bt.pin_fair(pin)
fair["fitted"] = False
fair = fair.merge(m[["match_id", "kickoff"]], on="match_id")
fair["mins"] = (fair.kickoff - fair.ts).dt.total_seconds() / 60
# cadence: gap to the next Pinnacle fetch of the same match, by time-to-kickoff bucket
f = fair[["match_id", "ts", "mins"]].drop_duplicates(["match_id", "ts"]).sort_values(["match_id", "ts"])
f["gap"] = f.groupby("match_id").ts.diff(-1).abs().dt.total_seconds() / 60
f["bucket"] = pd.cut(f.mins, [45, 120, 360, 720, 1440, 4320, 1e9],
                     labels=["45m-2h", "2-6h", "6-12h", "12-24h", "1-3d", "3d+"])
print("Pinnacle AH fetch cadence (minutes to next fetch), by time to kickoff:")
print(f.groupby("bucket", observed=True).gap.describe()[["count", "25%", "50%", "75%"]].round(0).to_string())

c = bt.candidates(runs, fair.drop(columns=["mins"]), m[["match_id", "kickoff"]] .assign(score_home=0))
c = c[c.bookmaker.isin(["Bet365", "Betano", "1xBet", "Marathonbet", "BetVictor", "SBO", "10Bet", "Superbet"])]
# episodes: consecutive Pinnacle fetches (per match) at which the key qualifies
order = f[["match_id", "ts"]].copy()
order["k"] = order.groupby("match_id").cumcount()
c = c.merge(order, on=["match_id", "ts"])
c = c.sort_values(["match_id", "bookmaker", "side", "line", "k"])
key = ["match_id", "bookmaker", "side", "line"]
c["new_ep"] = (c.groupby(key).k.diff() != 1).astype(int)
c["ep"] = c.groupby(key).new_ep.cumsum()
ep = c.groupby(key + ["ep"]).agg(first=("ts", "min"), last=("ts", "max"), n=("ts", "size"),
                                 kickoff=("kickoff", "first"), ev=("ev", "max")).reset_index()
ep["dur_min"] = (ep["last"] - ep["first"]).dt.total_seconds() / 60
ep["lead_h"] = (ep.kickoff - ep["first"]).dt.total_seconds() / 3600
ep["when"] = np.where(ep.lead_h >= 12, "early (12h+)", "late (<12h)")
print(f"\n{len(ep)} opportunity episodes (API-Football books, EV 3-15%)")
for w, g in ep.groupby("when"):
    print(f"\n{w}: n {len(g)}  seen at ONE fetch only {(g.n == 1).mean():.0%}  "
          f"median fetches {g.n.median():.0f}  median duration {g.dur_min.median():.0f} min  "
          f"(multi-fetch episodes: median {g[g.n > 1].dur_min.median():.0f} min)")
    print("  fetches seen:", g.n.clip(upper=6).value_counts().sort_index().to_dict(), "(6 = 6+)")
