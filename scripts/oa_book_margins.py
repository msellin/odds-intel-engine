"""Which books are SHARP (anchor candidates) and which are SOFT (where to bet)?

Measures 1x2 overround per bookmaker from The Odds API, so every book is quoted
at the SAME moment on the SAME fixture — no time-alignment problem, unlike our
stored snapshots.
"""
import json
import os
import statistics as st
import urllib.request
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path("/Users/margussellin/www/odds-intel-engine/.env"))
K = os.getenv("OA_KEY")

LEAGUES = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_uefa_champs_league",
    "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga",
    "soccer_efl_champ", "soccer_turkey_super_league",
]


def get(path, **params):
    params["apiKey"] = K
    qs = "&".join(f"{a}={b}" for a, b in params.items())
    with urllib.request.urlopen(
            f"https://api.the-odds-api.com/v4/{path}?{qs}", timeout=40) as r:
        return json.load(r), dict(r.headers)


by_book = defaultdict(list)
n_events = 0
hdr = {}
for lg in LEAGUES:
    try:
        data, hdr = get(f"sports/{lg}/odds", regions="eu,uk", markets="h2h",
                        oddsFormat="decimal")
    except Exception as e:                       # noqa: BLE001
        print(f"  {lg}: {type(e).__name__} {str(e)[:80]}")
        continue
    for ev in data:
        n_events += 1
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk["key"] != "h2h":
                    continue
                outs = mk.get("outcomes", [])
                if len(outs) != 3:
                    continue
                ov = sum(1.0 / o["price"] for o in outs) - 1.0
                if -0.02 < ov < 0.40:
                    by_book[bm["title"]].append(ov)

print(f"\nevents sampled: {n_events}   credits remaining: {hdr.get('x-requests-remaining')}")
print(f"\n{'bookmaker':26s} {'markets':>8s} {'median overround':>17s}   role")
rows = sorted(((st.median(v), b, len(v)) for b, v in by_book.items() if len(v) >= 15))
for ov, b, n in rows:
    role = ("SHARP — anchor candidate" if ov < 0.035
            else "tight" if ov < 0.05
            else "mid" if ov < 0.07 else "SOFT — bet here")
    print(f"  {b:24s} {n:8,} {ov*100:16.2f}%   {role}")

if rows:
    print(f"\n  sharpest: {rows[0][1]} at {rows[0][0]*100:.2f}%")
    print(f"  softest : {rows[-1][1]} at {rows[-1][0]*100:.2f}%")
    print(f"\n  our stored 'Pinnacle' reads 9.09-10.58% on the same market type.")
