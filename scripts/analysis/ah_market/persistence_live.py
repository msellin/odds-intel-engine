#!/usr/bin/env python3
"""#187 lever 2 on UNTRIMMED data (retention keeps only open/close/latest after ~1 day, ANALYSIS_GOTCHAS §59):
the last 30 h of AH rows for upcoming/just-played matches. Fetch cadence + how long an opportunity
(EV 3-15% vs Pinnacle's same-line fair price, same fetch) survives. Read-only, descriptive."""
import os, sys
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
load_dotenv(os.path.join(os.getcwd(), ".env"))
sys.path.insert(0, "scripts/analysis/ah_market")
from backtest import power_devig
AF = ["Bet365", "Betano", "1xBet", "Marathonbet", "BetVictor", "SBO", "10Bet", "Superbet"]
conn = psycopg2.connect(os.environ["DATABASE_URL"])
q = pd.read_sql("""
  SELECT o.match_id::text match_id, o.bookmaker, o.selection, o.handicap_line::float line, o.odds::float odds,
         o.timestamp ts, m.date kickoff
    FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
   WHERE o.market = 'asian_handicap' AND o.timestamp > now() - interval '30 hours' AND o.timestamp < m.date
     AND NOT coalesce(o.is_live, false) AND o.handicap_line IS NOT NULL
     AND o.bookmaker = ANY(%(b)s)""", conn, params={"b": ["Pinnacle"] + AF})
q["ts"] = pd.to_datetime(q.ts, utc=True).dt.floor("min")
q["kickoff"] = pd.to_datetime(q.kickoff, utc=True)
q["line"] = q.line.round(2)
pin = q[q.bookmaker == "Pinnacle"]
fetches = pin[["match_id", "ts", "kickoff"]].drop_duplicates().sort_values(["match_id", "ts"])
fetches["mins"] = (fetches.kickoff - fetches.ts).dt.total_seconds() / 60
fetches["gap"] = fetches.groupby("match_id").ts.diff(-1).abs().dt.total_seconds() / 60
fetches["bucket"] = pd.cut(fetches.mins, [45, 120, 360, 720, 1440, 1e9],
                           labels=["45m-2h", "2-6h", "6-12h", "12-24h", "24h+"])
print(f"{q.match_id.nunique()} matches, {len(fetches)} Pinnacle AH fetches in the last 30 h")
print("Pinnacle AH fetch cadence (minutes to the next fetch of the same match):")
print(fetches.groupby("bucket", observed=True).gap.describe()[["count", "25%", "50%", "75%"]].round(0).to_string())
w = pin.pivot_table(index=["match_id", "ts", "line"], columns="selection", values="odds", aggfunc="last").dropna().reset_index()
w["q_home"], w["q_away"] = power_devig(w.home.values, w.away.values)
b = q[q.bookmaker != "Pinnacle"]
rows = []
for side in ("home", "away"):
    x = b[b.selection == side].merge(w[["match_id", "ts", "line", f"q_{side}"]], on=["match_id", "ts", "line"])
    x["ev"] = x.odds * x[f"q_{side}"] - 1
    x["side"] = side
    rows.append(x[["match_id", "bookmaker", "side", "line", "ts", "kickoff", "ev"]])
c = pd.concat(rows)
c = c[(c.ev >= 0.03) & (c.ev < 0.15) & ((c.kickoff - c.ts) >= pd.Timedelta(minutes=45))]
order = fetches[["match_id", "ts"]].copy(); order["k"] = order.groupby("match_id").cumcount()
c = c.merge(order, on=["match_id", "ts"]).sort_values(["match_id", "bookmaker", "side", "line", "k"])
key = ["match_id", "bookmaker", "side", "line"]
c["ep"] = (c.groupby(key).k.diff() != 1).astype(int).groupby([c[k] for k in key]).cumsum()
ep = c.groupby(key + ["ep"]).agg(first=("ts", "min"), last=("ts", "max"), n=("ts", "size"),
                                 kickoff=("kickoff", "first")).reset_index()
ep["dur"] = (ep["last"] - ep["first"]).dt.total_seconds() / 60
ep["lead_h"] = (ep.kickoff - ep["first"]).dt.total_seconds() / 3600
ep["when"] = np.where(ep.lead_h >= 12, "early (12h+)", "late (<12h)")
print(f"\n{len(ep)} opportunity episodes (API-Football books, EV 3-15%, same fetch as Pinnacle)")
for wh, g in ep.groupby("when"):
    print(f"{wh}: n {len(g)} | seen at ONE fetch only {(g.n == 1).mean():.0%} | median fetches {g.n.median():.0f} | "
          f"median life {g.dur.median():.0f} min (multi-fetch: {g[g.n > 1].dur.median():.0f} min) | "
          f"fetches seen {g.n.clip(upper=6).value_counts().sort_index().to_dict()} (6 = 6+)")
