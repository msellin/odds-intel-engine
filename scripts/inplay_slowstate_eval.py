#!/usr/bin/env python3
"""INPLAY-SLOWSTATE-EVAL (2026-09-15, OWN Phase 1b) — the rig's pre-registered read.

Primary metric: realised HIT-RATE minus the book's own de-vigged implied
probability on the SELECTED set, cluster-robust on fixture. Why not ROI: per-bet
return sd at odds ~1.6 is ~0.79 so ±2% needs ~6,000 bets; the Bernoulli lift has
sd ~0.49 and separates "we pick well" from "the price moved". Why not CLV: it is
inadmissible in play (ANALYSIS_GOTCHAS §14).

Pre-registered rule (plan §Phase 1b):
  * n ≥ 1,000 and lift point estimate < 0                 → STOP (close in-play)
  * n ≥ 3,000 and lift CI lower bound > 0                 → Phase 3 candidate at THAT book
  * otherwise                                             → COLLECTING / OBSERVE
Reported per bot (live arm vs AF control arm), per trigger, and pooled. ROI is
printed dimmed as context only. Read-only.

    python3 scripts/inplay_slowstate_eval.py [--days 365]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

N_STOP, N_DECIDE, LIFT_TARGET, POWER_Z = 1000, 3000, 0.025, (1.96 + 0.84)


def lift_stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    lifts = [(1.0 if r["result"] == "won" else 0.0) - float(r["book_prob"]) for r in rows]
    mean = sum(lifts) / n
    by: dict[str, float] = defaultdict(float)
    for l, r in zip(lifts, rows):
        by[str(r["match_id"])] += l - mean
    se = math.sqrt(sum(v * v for v in by.values())) / n if n else 0.0
    sd = math.sqrt(sum((l - mean) ** 2 for l in lifts) / n) if n > 1 else 0.0
    roi = sum(float(r["pnl"] or 0) for r in rows) / sum(float(r["stake"] or 0) for r in rows) if n else 0.0
    need = int((POWER_Z * sd / LIFT_TARGET) ** 2) if sd else 0
    return {"n": n, "clusters": len(by), "lift": mean, "se": se, "lo": mean - 1.96 * se,
            "hi": mean + 1.96 * se, "sd": sd, "roi": roi, "n_for_power": need,
            "hit": sum(1 for r in rows if r["result"] == "won") / n,
            "book_p": sum(float(r["book_prob"]) for r in rows) / n}


def fmt(s: dict) -> str:
    if s.get("n", 0) == 0:
        return "n=0"
    return (f"n={s['n']:5d} (fx {s['clusters']:4d})  hit {s['hit']:.3f} vs book {s['book_p']:.3f}  "
            f"lift {s['lift']*100:+.2f}pp [{s['lo']*100:+.2f}, {s['hi']*100:+.2f}]  "
            f"| ROI {s['roi']*100:+.1f}% (context)  | n for +2.5pp @80%: {s['n_for_power']}")


def verdict(s: dict) -> str:
    n = s.get("n", 0)
    if n >= N_STOP and s["lift"] < 0:
        return "STOP — lift negative at n≥1,000"
    if n >= N_DECIDE and s["lo"] > 0:
        return "PHASE-3 CANDIDATE at this book (owner decision)"
    if n >= N_DECIDE:
        return "OBSERVE — CI includes 0 at n≥3,000"
    return f"COLLECTING {n}/{N_STOP if n < N_STOP else N_DECIDE}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    rows = execute_query(
        """SELECT b.name AS bot, s.strategy_profile AS trigger, s.match_id, s.result, s.pnl, s.stake,
                  s.calibrated_prob AS book_prob, s.odds_at_pick, s.recommended_bookmaker AS book
             FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id
            WHERE b.name IN ('bot_inplay_slowstate_v1', 'bot_inplay_slowstate_afctl_v1')
              AND s.result IN ('won', 'lost') AND s.calibrated_prob IS NOT NULL
              AND s.pick_time > now() - make_interval(days => %s)""",
        (a.days,)) or []
    print(f"\n=== in-play slow-state rig — settled picks with a book prob: {len(rows)} ===")
    for bot in ("bot_inplay_slowstate_v1", "bot_inplay_slowstate_afctl_v1"):
        sub = [r for r in rows if r["bot"] == bot]
        s = lift_stats(sub)
        print(f"\n{bot}: {fmt(s)}")
        for trig in sorted({r["trigger"] for r in sub if r["trigger"]}):
            print(f"   {trig:18s} {fmt(lift_stats([r for r in sub if r['trigger'] == trig]))}")
        if bot == "bot_inplay_slowstate_v1":
            print(f"   verdict: {verdict(s)}")
    live = lift_stats([r for r in rows if r["bot"] == "bot_inplay_slowstate_v1"])
    ctl = lift_stats([r for r in rows if r["bot"] == "bot_inplay_slowstate_afctl_v1"])
    if live.get("n") and ctl.get("n"):
        print(f"\nfresh-board value (live lift − control lift): {(live['lift']-ctl['lift'])*100:+.2f}pp")
    print("\nNB: the LIVE arm measures Epicbet's on-screen price. Trigger findings transfer to other "
          "books; price findings do not (Coolbet's in-play margin is tighter and is the book with a placer).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
