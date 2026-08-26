"""CLV-FIRST-DEV-LOOP-2026-08-26 — rank every bot by the metric that can actually
decide something.

The graduation gate has always been ROI-based. ROI is the thing we care about,
but it is a terrible instrument for measuring it: per-bet SD is 1.341 against
0.090 for de-vigged Pinnacle CLV, a 14.9x ratio, so ROI needs ~222x more bets
for the same precision. Pinning a number to +/-2% takes 17,259 bets on ROI and
78 on CLV.

The double-chance bots made the cost of that concrete: at n=2,436 their ROI
t-stat was -1.83 (undecidable) while their CLV t-stat was -28.18 (decisive), and
bot_dc_strong_fav read PROFITABLE on ROI (+0.40%) while sitting at -4.02% CLV.

So: rank on CLV, cross-check on ROI, and say plainly which bots have enough data
to decide and which do not.

Usage:
    python3 scripts/clv_gate_report.py
    python3 scripts/clv_gate_report.py --min-n 50 --active-only
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

# One-sided 5% test in each direction. Same threshold the shadow-bot promotion
# gate uses for ROI — applied to CLV it is reachable in weeks instead of years.
PROMOTE_T = 1.65
RETIRE_T = -1.65
MIN_CLV_N = 100


def summarise(vals: list[float]) -> tuple[int, float, float, float]:
    n = len(vals)
    if n < 2:
        return n, 0.0, 0.0, 0.0
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    se = math.sqrt(var / n)
    return n, mean, se, (mean / se if se else 0.0)


def verdict(n: int, t: float) -> str:
    if n < MIN_CLV_N:
        return f"collecting ({n}/{MIN_CLV_N})"
    if t >= PROMOTE_T:
        return "PROMOTE"
    if t <= RETIRE_T:
        return "RETIRE"
    return "no edge either way"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=30, help="minimum settled picks to list")
    ap.add_argument("--active-only", action="store_true")
    args = ap.parse_args()

    rows = execute_query(
        """
        SELECT b.name, b.retired_at IS NOT NULL AS retired,
               s.clv_pinnacle::float AS clv,
               CASE WHEN s.result = 'won' THEN s.odds_at_pick - 1 ELSE -1 END::float AS ret
          FROM shadow_bets_unique s
          JOIN bots b ON b.id = s.bot_id
         WHERE s.result IN ('won','lost')
        """,
        [],
    )
    by_bot: dict = {}
    for r in rows:
        d = by_bot.setdefault(r["name"], {"retired": r["retired"], "clv": [], "ret": []})
        d["ret"].append(float(r["ret"]))
        if r["clv"] is not None:
            d["clv"].append(float(r["clv"]))

    out = []
    for name, d in by_bot.items():
        if args.active_only and d["retired"]:
            continue
        if len(d["ret"]) < args.min_n:
            continue
        cn, cm, cse, ct = summarise(d["clv"])
        rn, rm, rse, rt = summarise(d["ret"])
        out.append((ct if cn >= 2 else 0.0, name, d["retired"], cn, cm, ct, rn, rm, rt))
    out.sort(reverse=True)

    print("Bots ranked by de-vigged Pinnacle CLV — the metric that can decide.\n")
    print(f"{'bot':28s} {'CLV n':>6s} {'CLV':>8s} {'CLV t':>7s} "
          f"{'ROI n':>6s} {'ROI':>8s} {'ROI t':>7s}  verdict")
    print("-" * 100)
    disagree = []
    for ct, name, retired, cn, cm, _t, rn, rm, rt in out:
        tag = " (retired)" if retired else ""
        v = verdict(cn, ct) if cn else "no CLV coverage"
        clv_s = f"{cm*100:+7.2f}%" if cn else "      —"
        ct_s = f"{ct:+7.2f}" if cn else "      —"
        print(f"{name + tag:28s} {cn:6d} {clv_s} {ct_s} "
              f"{rn:6d} {rm*100:+7.2f}% {rt:+7.2f}  {v}")
        # Where the two metrics point opposite ways, CLV is the lower-variance
        # estimator and ROI is usually the one that is wrong.
        if cn >= MIN_CLV_N and cm * rm < 0:
            disagree.append((name, cm * 100, ct, rm * 100, rt))

    if disagree:
        print("\nDISAGREEMENTS — CLV and ROI point opposite ways.")
        print("CLV has ~222x the precision per bet, so it is the one to believe.")
        for name, cm, ct, rm, rt in disagree:
            print(f"  {name:26s} CLV {cm:+6.2f}% (t {ct:+6.2f})   "
                  f"ROI {rm:+6.2f}% (t {rt:+6.2f})")

    print(f"\nGate: promote at CLV t >= {PROMOTE_T}, retire at CLV t <= {RETIRE_T}, "
          f"min {MIN_CLV_N} settled picks with CLV.")
    print("CLV has no coverage where Pinnacle quotes no market — BTTS above all "
          "(API-Football's\nPinnacle feed carries 8 bet types and BTTS is not one). "
          "Those bots cannot be gated\nthis way and need a consensus anchor instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
