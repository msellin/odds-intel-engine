"""Like-for-like: the SAME books on the SAME top leagues, our stored snapshots
vs The Odds API. Separates 'our league mix is wider' from 'our collection is
degrading prices'."""
import json
import os
import statistics as st
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
load_dotenv(Path("/Users/margussellin/www/odds-intel-engine/.env"))
K = os.getenv("OA_KEY")

from workers.api_clients.db import execute_query  # noqa: E402

SIDES = ("home", "draw", "away")
# Top leagues only, matched by name on our side.
OA_LEAGUES = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
              "soccer_italy_serie_a", "soccer_france_ligue_one"]
OUR_NAMES = ["Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1"]


def get(path, **params):
    params["apiKey"] = K
    qs = "&".join(f"{a}={b}" for a, b in params.items())
    with urllib.request.urlopen(
            f"https://api.the-odds-api.com/v4/{path}?{qs}", timeout=40) as r:
        return json.load(r), dict(r.headers)


oa = defaultdict(list)
hdr = {}
for lg in OA_LEAGUES:
    try:
        data, hdr = get(f"sports/{lg}/odds", regions="eu,uk", markets="h2h",
                        oddsFormat="decimal")
    except Exception as e:                       # noqa: BLE001
        print(f"  {lg}: {type(e).__name__}")
        continue
    for ev in data:
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk["key"] != "h2h" or len(mk.get("outcomes", [])) != 3:
                    continue
                ov = sum(1.0 / o["price"] for o in mk["outcomes"]) - 1.0
                if -0.02 < ov < 0.40:
                    oa[bm["title"]].append(ov)

# Ours: same five leagues, deduped one quote per leg, latest pre-KO.
rows = execute_query(
    """SELECT s.bookmaker, s.match_id, s.selection, s.od
         FROM (SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
                      o.match_id, o.selection, o.bookmaker, o.odds::float od
                 FROM odds_snapshots o
                 JOIN matches m ON m.id = o.match_id
                 JOIN leagues l ON l.id = m.league_id
                WHERE o.market = '1x2' AND o.is_live IS NOT TRUE
                  AND o.is_closing = false AND o.odds > 1.01
                  AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                  AND m.date > now() - interval '60 days'
                  AND l.name = ANY(%s)
                ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC) s""",
    (OUR_NAMES,)) or []
g = defaultdict(lambda: defaultdict(dict))
for r in rows:
    g[r["bookmaker"]][r["match_id"]][r["selection"]] = r["od"]
ours = {}
for bk, ms in g.items():
    ovs = [sum(1 / s[x] for x in SIDES) - 1 for s in ms.values() if len(s) == 3]
    if len(ovs) >= 20:
        ours[bk] = (st.median(ovs), len(ovs))

print(f"\ncredits remaining: {hdr.get('x-requests-remaining')}")
print(f"\nTOP-5 LEAGUES ONLY — same books, two sources\n")
print(f"  {'book':18s} {'OddsAPI':>10s} {'n':>5s}   {'OURS':>10s} {'n':>6s}   gap")
alias = {"Pinnacle": "Pinnacle", "Coolbet": "Coolbet", "1xBet": "1xBet",
         "Betfair": "Betfair", "Marathon Bet": "Marathonbet",
         "William Hill": "William Hill", "Bet Victor": "BetVictor",
         "888sport": "888Sport", "Betano (UK)": "Betano"}
for oa_name, our_name in alias.items():
    if oa_name not in oa or our_name not in ours:
        continue
    a = st.median(oa[oa_name]); na = len(oa[oa_name])
    b, nb = ours[our_name]
    print(f"  {our_name:18s} {a*100:9.2f}% {na:5,}   {b*100:9.2f}% {nb:6,}   {(b-a)*100:+6.2f}pp")
print("\n  A large positive gap = our stored price is WORSE than the live market,")
print("  i.e. our COLLECTION is degrading it, not the league mix.")
