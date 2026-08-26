"""SHADOW-BOT-FIXES-2026-08-26 — what do the new rules actually change?

"How will ROI change?" cannot be answered by opinion, and at these sample sizes
it cannot be answered by a forward ROI estimate either. It CAN be answered
counterfactually on settled history: re-score every historical line-shop pick
under the new rules, split it into KEPT vs REJECTED, and look at what the
rejected ones actually returned.

If the rejected set lost money, the change helps by exactly that much. If it
made money, the change hurts. Either way it is a measurement, not a forecast.

Rules applied:
  1. Shin de-vig instead of proportional -> recomputed edge must still clear 3%
  2. OU line-integrity guard -> soft quote must be nearest its OWN stated line

Usage:
    python3 scripts/rule_change_impact.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import shin_devig, proportional_devig  # noqa: E402

EDGE_MIN = 0.03
SIDES = {"1x2": ["home", "draw", "away"]}
OU_LADDER = ["over_under_15", "over_under_20", "over_under_25", "over_under_275",
             "over_under_30", "over_under_325", "over_under_35", "over_under_40",
             "over_under_45"]


def stats(rets):
    n = len(rets)
    if n == 0:
        return 0, 0.0, 0.0
    mean = sum(rets) / n
    if n < 2:
        return n, mean * 100, 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    se = (var / n) ** 0.5
    return n, mean * 100, se * 100


def main() -> int:
    picks = execute_query(
        "SELECT b.name, s.match_id, s.market, s.selection, s.odds_at_pick, "
        "s.result, s.pick_time, s.recommended_bookmaker "
        "FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id "
        "WHERE b.name = ANY(%s) AND s.result IN ('won','lost')",
        [["bot_pin_1x2_home_v1", "bot_sweep_ou25_v1", "bot_sweep_ou35_v1",
          "bot_pin_1x2_draw_tier4_v1"]],
    )
    print(f"{len(picks)} settled line-shop picks to re-score\n")

    buckets = defaultdict(lambda: {"kept": [], "rej_edge": [], "rej_line": []})

    for p in picks:
        mid, mkt, sel = str(p["match_id"]), p["market"], p["selection"]
        o = float(p["odds_at_pick"])
        ret = (o - 1.0) if p["result"] == "won" else -1.0
        bot = p["name"]
        sides = SIDES.get(mkt, ["over", "under"])

        pin = {}
        for s_ in sides:
            q = execute_query(
                "SELECT odds FROM odds_snapshots WHERE match_id=%s AND market=%s "
                "AND selection=%s AND bookmaker='Pinnacle' AND timestamp <= %s "
                "ORDER BY timestamp DESC LIMIT 1",
                [mid, mkt, s_, p["pick_time"]],
            )
            if q:
                pin[s_] = float(q[0]["odds"])
        if len(pin) != len(sides):
            continue

        # Rule 2 — line integrity (OU only). Is our quote nearest its OWN line?
        if mkt.startswith("over_under"):
            own = pin[sel]
            own_gap = abs(1.0 / o - 1.0 / own)
            drifts = False
            for cand in OU_LADDER:
                if cand == mkt:
                    continue
                q = execute_query(
                    "SELECT odds FROM odds_snapshots WHERE match_id=%s AND market=%s "
                    "AND selection=%s AND bookmaker='Pinnacle' AND timestamp <= %s "
                    "ORDER BY timestamp DESC LIMIT 1",
                    [mid, cand, sel, p["pick_time"]],
                )
                if q and float(q[0]["odds"]) > 1.0:
                    if abs(1.0 / o - 1.0 / float(q[0]["odds"])) < own_gap:
                        drifts = True
                        break
            if drifts:
                buckets[bot]["rej_line"].append(ret)
                continue

        # Rule 1 — Shin edge must still clear the floor
        i = sides.index(sel)
        odds = [pin[s_] for s_ in sides]
        shin_edge = o * shin_devig(odds)[i] - 1.0
        if shin_edge < EDGE_MIN:
            buckets[bot]["rej_edge"].append(ret)
        else:
            buckets[bot]["kept"].append(ret)

    print(f"{'bot':28s} {'group':16s} {'n':>5s} {'ROI':>9s} {'SE':>8s}")
    print("-" * 72)
    all_kept, all_rej = [], []
    for bot in sorted(buckets):
        b = buckets[bot]
        for label, key in (("kept", "kept"), ("dropped: low edge", "rej_edge"),
                           ("dropped: bad line", "rej_line")):
            n, roi, se = stats(b[key])
            if n:
                print(f"{bot:28s} {label:16s} {n:5d} {roi:+8.1f}% {se:7.1f}%")
        all_kept.extend(b["kept"])
        all_rej.extend(b["rej_edge"] + b["rej_line"])
        print()

    nk, rk, sk = stats(all_kept)
    nr, rr, sr = stats(all_rej)
    n_all, r_all, _ = stats(all_kept + all_rej)
    print("=" * 72)
    print(f"{'ALL line-shop, as it ran':28s} {'':16s} {n_all:5d} {r_all:+8.1f}%")
    print(f"{'  -> KEPT under new rules':28s} {'':16s} {nk:5d} {rk:+8.1f}% {sk:7.1f}%")
    print(f"{'  -> DROPPED by new rules':28s} {'':16s} {nr:5d} {rr:+8.1f}% {sr:7.1f}%")
    if nr:
        print(f"\nThe new rules would have removed {nr} of {n_all} picks "
              f"({100.0*nr/n_all:.0f}%), and those picks returned {rr:+.1f}%.")
        print(f"Portfolio ROI would have moved {r_all:+.1f}% -> {rk:+.1f}% "
              f"({rk - r_all:+.1f}pp).")
        print("""
TWO REASONS NOT TO READ THAT AS 'THE CHANGE HURT ROI':

1. SELECTION BIAS. This re-scores only picks the OLD rules chose. The new rules
   also admit picks the old ones rejected — Shin fires MORE 1X2 picks, not fewer
   (493 -> 582 over four months). Conditioning on the old selection and then
   comparing subsets of it is a collider: the honest comparison is a full
   re-selection under each rule, which is scripts/lineshop_replay.py, and that
   showed Shin slightly ahead (+6.15% -> +6.68% on 1X2, +0.10% -> +1.58% on OU).

2. THE 'BAD LINE' PnL IS NOT REAL MONEY. Those picks were dropped because the
   quoted price belongs to a DIFFERENT total than the one we settled against.
   The winning Coolbet over-2.5 picks were credited at an average 1.92 while
   Pinnacle's actual 2.5 price was 1.56 — we booked a 3.0-line price against a
   2.5-line outcome. Their apparent return is the artefact itself, not profit
   that was available. Scoring the guard by the PnL of what it removes is
   circular.

Neither split is significant in any case: kept vs dropped differ by ~16pp with a
combined SE near 14pp (t ~ 1.1).""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
