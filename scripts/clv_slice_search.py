"""CLV-SLICE-SEARCH-2026-08-26 — search for the NEXT profitable bot directly.

The shadow-bot programme exists to find a second profitable bot alongside
bot_v10_all: more markets, more leagues, more coverage. Its method has been to
guess a config, deploy it, and wait for ROI. That cannot work — ROI needs ~17,000
bets to resolve +/-2% and a shadow bot produces about five a day, so a single
config takes years to judge and only one config is in flight at a time.

De-vigged Pinnacle CLV resolves the same question in ~100 picks (per-bet SD 0.090
vs 1.341). That makes the search tractable in a different way: instead of
deploying configs, replay every candidate pick that our model and the market
BOTH had an opinion on, and ask which slices of (market x league tier x odds
band) beat the closing line.

Strictly point-in-time: predictions filtered on created_at <= kickoff-3h, prices
on timestamp <= kickoff-3h, and the Pinnacle CLV anchor taken at the close.

Gates on CLV, never ROI. PER-BOT-SWEEP-2026-08-24 established that selecting
configs on backtest ROI is anti-predictive here (-9.2% out of sample); CLV is
both lower-variance and not the quantity being optimised into.

Usage:
    python3 scripts/clv_slice_search.py --start 2026-05-01
    python3 scripts/clv_slice_search.py --min-edge 0.05 --min-n 80
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

LEAD_H = 3
ACCESSIBLE = ["Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Coolbet"]

# predictions.market -> (odds_snapshots.market, selection)
PRED_MAP = {
    "1x2_home": ("1x2", "home"), "1x2_draw": ("1x2", "draw"), "1x2_away": ("1x2", "away"),
    "btts_yes": ("btts", "yes"), "btts_no": ("btts", "no"),
    "over25": ("over_under_25", "over"), "under25": ("over_under_25", "under"),
    "over35": ("over_under_35", "over"), "under35": ("over_under_35", "under"),
    "over15": ("over_under_15", "over"), "under15": ("over_under_15", "under"),
}
COMPLEMENT = {"1x2": ["home", "draw", "away"], "btts": ["yes", "no"]}


def sides_for(mkt: str) -> list[str]:
    return COMPLEMENT.get(mkt, ["over", "under"])


def won(mkt: str, sel: str, sh: int, sa: int):
    if mkt == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sa > sh}[sel]
    if mkt == "btts":
        y = sh > 0 and sa > 0
        return y if sel == "yes" else not y
    line = float(mkt.replace("over_under_", "")) / 10.0
    t = sh + sa
    if t == line:
        return None
    return t > line if sel == "over" else t < line


def stats(v: list[float]):
    n = len(v)
    if n < 2:
        return n, 0.0, 0.0, 0.0
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    se = math.sqrt(var / n)
    return n, m, se, (m / se if se else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--v10-gate", action="store_true",
                    help="Use bot_v10_all's threshold structure instead of a flat edge floor")
    args = ap.parse_args()

    print(f"window {args.start} → {args.end}   model edge >= {args.min_edge:.0%}   "
          f"pick priced at kickoff-{LEAD_H}h\n")

    print("loading model predictions (point-in-time)...")
    preds = execute_query(
        """
        SELECT DISTINCT ON (p.match_id, p.market)
               p.match_id, p.market, p.model_probability::float AS prob
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.source = 'ensemble' AND m.status = 'finished'
           AND m.score_home IS NOT NULL
           AND m.date >= %s AND m.date < %s
           AND p.created_at <= m.date - (%s || ' hours')::interval
           AND p.market = ANY(%s)
         ORDER BY p.match_id, p.market, p.created_at DESC
        """,
        [args.start, args.end, str(LEAD_H), list(PRED_MAP)],
    )
    print(f"  {len(preds)} predictions")

    print("loading best accessible price at pick time...")
    best = execute_query(
        """
        SELECT o.match_id, o.market, o.selection, max(o.odds)::float AS best
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND m.date >= %s AND m.date < %s
           AND o.timestamp <= m.date - (%s || ' hours')::interval
           AND o.bookmaker = ANY(%s)
         GROUP BY 1,2,3
        """,
        [args.start, args.end, str(LEAD_H), ACCESSIBLE],
    )
    best_map = {(str(r["match_id"]), r["market"], r["selection"]): r["best"] for r in best}
    print(f"  {len(best_map)} best prices")

    print("loading Pinnacle closes (CLV anchor)...")
    pin = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection)
               o.match_id, o.market, o.selection, o.odds::float AS odds
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = 'Pinnacle' AND m.status = 'finished'
           AND m.score_home IS NOT NULL
           AND m.date >= %s AND m.date < %s AND o.timestamp <= m.date
         ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
        """,
        [args.start, args.end],
    )
    pin_map = {(str(r["match_id"]), r["market"], r["selection"]): r["odds"] for r in pin}
    print(f"  {len(pin_map)} Pinnacle closes")

    meta = execute_query(
        """
        SELECT m.id, m.score_home, m.score_away, l.tier
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND m.date >= %s AND m.date < %s
        """,
        [args.start, args.end],
    )
    meta_map = {str(r["id"]): (int(r["score_home"]), int(r["score_away"]), r["tier"])
                for r in meta}

    devig_cache: dict = {}
    outliers: dict = defaultdict(int)
    slices: dict = defaultdict(lambda: {"clv": [], "ret": []})
    considered = fired = 0

    for p in preds:
        mid = str(p["match_id"])
        mkt, sel = PRED_MAP[p["market"]]
        mm = meta_map.get(mid)
        if not mm:
            continue
        sh, sa, tier = mm
        o = best_map.get((mid, mkt, sel))
        if not o or o <= 1.0:
            continue
        considered += 1
        edge = o * p["prob"] - 1.0

        if args.v10_gate:
            # bot_v10_all's actual gate — the only bot in the fleet with
            # decisive positive CLV (+4.82%, t=+5.27 on n=454). A flat edge floor
            # over the same predictions is negative in EVERY slice, so whatever
            # works here is the structure, not the model. Three parts:
            #   - thresholds scale with league tier (efficient markets demand more)
            #   - LONGSHOTS demand a higher edge than favourites, which is
            #     hand-compensation for the favourite-longshot bias measured in
            #     book_bias_probe
            #   - a hard 30 pct floor on model probability
            V10 = {1: {"fav": 0.08, "long": 0.12, "ou": 0.08},
                   2: {"fav": 0.05, "long": 0.08, "ou": 0.06},
                   3: {"fav": 0.04, "long": 0.06, "ou": 0.05},
                   4: {"fav": 0.03, "long": 0.05, "ou": 0.04}}
            th = V10.get(tier if tier in V10 else 4)
            if p["prob"] < 0.30:
                continue
            if not (1.30 <= o <= 4.50):
                continue
            floor = th["ou"] if mkt.startswith("over_under") else (
                th["fav"] if o < 2.5 else th["long"])
            if edge < floor:
                continue
        elif edge < args.min_edge:
            continue
        w = won(mkt, sel, sh, sa)
        if w is None:
            continue

        key = (mid, mkt)
        if key not in devig_cache:
            sides = sides_for(mkt)
            odds = [pin_map.get((mid, mkt, s2)) for s2 in sides]
            devig_cache[key] = (None if any(x is None or x <= 1.0 for x in odds)
                                else devig(odds))
        probs = devig_cache[key]
        if probs is None:
            continue
        fair = probs[sides_for(mkt).index(sel)]

        # OUTLIER GUARD. Without this the search is dominated by broken prices:
        # the first run put "over_under_25 at odds 4.5+" on top with CLV +173.95%
        # (t=+42.49), which is not an edge — a normal OU 2.5 price is 1.5-2.5, so
        # a 4.5+ quote labelled OU 2.5 is a mislabelled line or a junk row. Same
        # class of fault as the Coolbet OU shift found in SHADOW-OU-EDGE-AUDIT.
        # Mirrors production's _PIN_OU_OUTLIER_MULT / _PIN_1X2_OUTLIER_MULT: a
        # soft price may not exceed Pinnacle's by more than 30-35%.
        pin_own = pin_map.get((mid, mkt, sel))
        if not pin_own or pin_own <= 1.0:
            continue
        mult = 1.35 if mkt == "1x2" else 1.30
        if o > pin_own * mult:
            outliers[mkt] += 1
            continue

        clv = o * fair - 1.0
        ret = (o - 1.0) if w else -1.0
        fired += 1

        band = ("<1.5" if o < 1.5 else "1.5-2.0" if o < 2.0 else "2.0-3.0" if o < 3.0
                else "3.0-4.5" if o < 4.5 else "4.5+")
        t_lab = f"T{tier}" if tier is not None else "T?"
        for k in ((mkt, "all", "all"), (mkt, t_lab, "all"), (mkt, "all", band),
                  (mkt, t_lab, band)):
            slices[k]["clv"].append(clv)
            slices[k]["ret"].append(ret)

    print(f"\n{considered} candidate picks had both a model view and a price; "
          f"{fired} cleared the {args.min_edge:.0%} edge gate and had a CLV anchor")
    if outliers:
        print(f"rejected as price outliers (soft > Pinnacle x 1.30/1.35): "
              f"{dict(outliers)}\n")
    else:
        print()

    rows = []
    for (mkt, tier, band), d in slices.items():
        n, cm, cse, ct = stats(d["clv"])
        if n < args.min_n:
            continue
        rn, rm, _, rt = stats(d["ret"])
        rows.append((ct, mkt, tier, band, n, cm, ct, rm, rt))
    rows.sort(reverse=True)

    print(f"{'market':16s} {'tier':5s} {'odds band':10s} {'n':>6s} {'CLV':>8s} "
          f"{'CLV t':>7s} {'ROI':>8s} {'ROI t':>7s}")
    print("-" * 82)
    for _, mkt, tier, band, n, cm, ct, rm, rt in rows[:28]:
        flag = "  <-- candidate" if ct >= 1.65 else ""
        print(f"{mkt:16s} {tier:5s} {band:10s} {n:6d} {cm*100:+7.2f}% {ct:+7.2f} "
              f"{rm*100:+7.2f}% {rt:+7.2f}{flag}")

    good = [r for r in rows if r[6] >= 1.65]
    print(f"\n{len(good)} slice(s) with CLV t >= 1.65 out of {len(rows)} tested at n >= {args.min_n}.")
    if len(rows):
        exp = 0.05 * len(rows)
        print(f"Testing {len(rows)} slices, ~{exp:.0f} would clear a one-sided 5% bar by chance "
              f"alone.\nTreat anything at or below that count as noise, and require "
              f"out-of-sample\nconfirmation before deploying — see FAVOURITE-BAND for how "
              f"that goes wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
