"""MODEL-VERSION-CLV-2026-08-26 — score every model version against the incumbent.

CLV-SLICE-SEARCH found the edge lives in the model version, not in thresholds or
coverage: on bot_v10_all's own picks, v20260712 scored CLV +15.17% (t=+5.79)
while v20260524_market and v14 sat at zero. So the productive loop is model
quality, and CLV makes it fast — ~100 picks decides where ROI needs ~17,000.

Comparing versions on their own date ranges would confound the model with the
period and the fixture mix. This scores them HEAD-TO-HEAD: for each rival, only
matches where BOTH it and the incumbent produced a prediction, both scored under
the same gate. Paired by match, so fixture difficulty cancels.

Also answers the follow-on question — does a better model widen the beatable
universe? COVERAGE-EXPANSION found v20260712 at CLV -5.56% in leagues no bot
bets. That was conditional on THAT model. If newer versions do progressively
less badly there, the boundary is moving and coverage reopens as models improve.

RESULT 2026-08-26 — the comparison this script wants to make is CURRENTLY
IMPOSSIBLE, and that is the finding.

Model versions are routed disjointly. v20260719 and v20260712 both ran from
2026-07-31 to 08-26, and across every finished match in that window they share
exactly ZERO match+market pairs — MODEL_VERSION_OU / MODEL_VERSION_OU_T1 and
friends send different versions to different market/tier slots, so no fixture is
ever scored by two versions at once.

Every available comparison is therefore confounded:
  * by PERIOD, if you split one bot's bets by version (bot_v10_all shows
    v20260712 at CLV +15.17%, but that version ran in a different month against
    a different fixture mix than v20260524_market did)
  * by BOT MIX, if you pool all bots by version (the same v20260712 reads
    -1.42% across 4,511 picks, because those picks are dominated by the
    double-chance bots at -4.3% CLV rather than by bot_v10_all)

Those two numbers for the same model — +15.17% and -1.42% — are not a
contradiction. They are two different confounds, and neither is the model.

The fix is cheap and is filed as MODEL-AB-SHADOW-SCORING: have the pipeline
write shadow predictions from candidate versions alongside the production one on
EVERY match. Then this script's paired comparison works, and CLV settles a model
promotion in ~100 picks instead of never.

Usage:
    python3 scripts/model_version_clv_scoreboard.py
    python3 scripts/model_version_clv_scoreboard.py --incumbent v20260712
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
PRED_MAP = {
    "1x2_home": ("1x2", "home"), "1x2_draw": ("1x2", "draw"), "1x2_away": ("1x2", "away"),
    "over25": ("over_under_25", "over"), "under25": ("over_under_25", "under"),
    "over35": ("over_under_35", "over"), "under35": ("over_under_35", "under"),
}
COMPLEMENT = {"1x2": ["home", "draw", "away"]}
V10 = {1: {"fav": 0.08, "long": 0.12, "ou": 0.08}, 2: {"fav": 0.05, "long": 0.08, "ou": 0.06},
       3: {"fav": 0.04, "long": 0.06, "ou": 0.05}, 4: {"fav": 0.03, "long": 0.05, "ou": 0.04}}


def sides_for(m):
    return COMPLEMENT.get(m, ["over", "under"])


def won(m, sel, sh, sa):
    if m == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sa > sh}[sel]
    line = float(m.replace("over_under_", "")) / 10.0
    t = sh + sa
    return None if t == line else (t > line if sel == "over" else t < line)


def stats(v):
    n = len(v)
    if n < 2:
        return n, 0.0, 0.0
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    se = math.sqrt(var / n)
    return n, m, (m / se if se else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--incumbent", default="v20260712")
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--versions", default=None,
                    help="comma-separated model_versions to score (default: all in window)")
    ap.add_argument("--min-n", type=int, default=40)
    args = ap.parse_args()

    vers = args.versions.split(",") if args.versions else None
    print(f"incumbent = {args.incumbent}   window {args.start} →   "
          f"gate = bot_v10_all's, priced at kickoff-{LEAD_H}h\n")

    preds = execute_query(
        """
        SELECT DISTINCT ON (p.match_id, p.market, p.model_version)
               p.match_id, p.market, p.model_version AS mv,
               p.model_probability::float AS prob
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.source='ensemble' AND m.status='finished' AND m.score_home IS NOT NULL
           AND m.date >= %s AND p.market = ANY(%s)
           AND p.created_at <= m.date - (%s || ' hours')::interval
           AND (%s::text[] IS NULL OR p.model_version = ANY(%s::text[]))
         ORDER BY p.match_id, p.market, p.model_version, p.created_at DESC
        """,
        [args.start, list(PRED_MAP), str(LEAD_H), vers, vers],
    )
    print(f"loaded {len(preds)} point-in-time predictions")
    mids = list({str(r["match_id"]) for r in preds})

    best = execute_query(
        "SELECT o.match_id, o.market, o.selection, max(o.odds)::float AS best "
        "FROM odds_snapshots o JOIN matches m ON m.id=o.match_id "
        "WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = ANY(%s) "
        "AND o.timestamp <= m.date - (%s || ' hours')::interval GROUP BY 1,2,3",
        [mids, ACCESSIBLE, str(LEAD_H)])
    bm = {(str(r["match_id"]), r["market"], r["selection"]): r["best"] for r in best}
    pinr = execute_query(
        "SELECT DISTINCT ON (o.match_id,o.market,o.selection) o.match_id,o.market,"
        "o.selection,o.odds::float AS odds FROM odds_snapshots o "
        "JOIN matches m ON m.id=o.match_id WHERE o.match_id = ANY(%s::uuid[]) "
        "AND o.bookmaker='Pinnacle' AND o.timestamp <= m.date "
        "ORDER BY o.match_id,o.market,o.selection,o.timestamp DESC", [mids])
    pm = {(str(r["match_id"]), r["market"], r["selection"]): r["odds"] for r in pinr}
    meta = execute_query(
        "SELECT m.id, m.score_home, m.score_away, l.tier FROM matches m "
        "JOIN leagues l ON l.id=m.league_id WHERE m.id = ANY(%s::uuid[])", [mids])
    mmap = {str(r["id"]): (int(r["score_home"]), int(r["score_away"]), r["tier"]) for r in meta}

    # Which leagues no active bot touches — for the boundary question.
    unc = execute_query(
        """
        WITH bet AS (SELECT DISTINCT m.league_id FROM simulated_bets sb
            JOIN matches m ON m.id=sb.match_id JOIN bots b ON b.id=sb.bot_id
           WHERE b.is_active AND sb.pick_time >= %s)
        SELECT m.id FROM matches m WHERE m.id = ANY(%s::uuid[])
          AND m.league_id NOT IN (SELECT league_id FROM bet)
        """, [args.start, mids])
    uncovered = {str(r["id"]) for r in unc}
    print(f"{len(bm)} prices, {len(pm)} closes, {len(uncovered)} matches in unbet leagues\n")

    cache: dict = {}

    def score(mid, mkt, sel, prob, tier):
        """CLV for this pick under v10's gate, or None if it wouldn't fire."""
        o = bm.get((mid, mkt, sel))
        if not o or o <= 1.0 or prob < 0.30 or not (1.30 <= o <= 4.50):
            return None
        th = V10.get(tier if tier in V10 else 4)
        floor = th["ou"] if mkt.startswith("over_under") else (th["fav"] if o < 2.5 else th["long"])
        if o * prob - 1.0 < floor:
            return None
        pin_own = pm.get((mid, mkt, sel))
        if not pin_own or pin_own <= 1.0 or o > pin_own * (1.35 if mkt == "1x2" else 1.30):
            return None
        key = (mid, mkt)
        if key not in cache:
            odds = [pm.get((mid, mkt, s2)) for s2 in sides_for(mkt)]
            cache[key] = None if any(x is None or x <= 1.0 for x in odds) else devig(odds)
        probs = cache[key]
        if probs is None:
            return None
        return o * probs[sides_for(mkt).index(sel)] - 1.0

    # (match, market) -> {version: clv}
    grid: dict = defaultdict(dict)
    per_version_unc: dict = defaultdict(list)
    for r in preds:
        mid = str(r["match_id"])
        mkt, sel = PRED_MAP[r["market"]]
        mm = mmap.get(mid)
        if not mm:
            continue
        sh, sa, tier = mm
        if won(mkt, sel, sh, sa) is None:
            continue
        c = score(mid, mkt, sel, r["prob"], tier)
        if c is None:
            continue
        grid[(mid, r["market"])][r["mv"]] = c
        if mid in uncovered:
            per_version_unc[r["mv"]].append(c)

    inc = args.incumbent
    pairs: dict = defaultdict(list)
    for _, per in grid.items():
        if inc not in per:
            continue
        for mv, c in per.items():
            if mv != inc:
                pairs[mv].append(c - per[inc])

    print(f"HEAD-TO-HEAD vs {inc} — same match+market, both models fired, paired\n")
    print(f"{'version':26s} {'n':>6s} {'CLV delta':>11s} {'t':>8s}  verdict")
    print("-" * 66)
    rows = []
    for mv, d in pairs.items():
        n, m, t = stats(d)
        if n >= args.min_n:
            rows.append((m, mv, n, t))
    rows.sort(reverse=True)
    for m, mv, n, t in rows:
        v = "BETTER" if t >= 1.96 else ("worse" if t <= -1.96 else "no difference")
        print(f"{mv:26s} {n:6d} {m*100:+10.2f}% {t:+8.2f}  {v}")
    if not rows:
        print("  (no rival version shares enough fired picks with the incumbent)")

    print(f"\n\nDOES A BETTER MODEL WIDEN THE UNIVERSE?")
    print("CLV in leagues no active bot bets, by version. If newer models do")
    print("progressively less badly, the beatable boundary is moving.\n")
    print(f"{'version':26s} {'n':>6s} {'CLV in unbet leagues':>22s} {'t':>8s}")
    print("-" * 66)
    for mv in sorted(per_version_unc, key=lambda k: -len(per_version_unc[k])):
        n, m, t = stats(per_version_unc[mv])
        if n >= args.min_n:
            print(f"{mv:26s} {n:6d} {m*100:+21.2f}% {t:+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
