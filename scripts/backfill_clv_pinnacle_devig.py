#!/usr/bin/env python3
"""
CLV-PINNACLE-LIVE-TWO-DEFINITIONS-2026-09-07 — backfill.

The simulated-settlement path used to write clv_pinnacle as the RAW ratio
(odds_at_pick / pinnacle_closing - 1, Pinnacle's vig still in it); the shadow
path wrote the DE-VIGGED odds_at_pick * devig(p) - 1. Same column, two
quantities. The settlement code is now unified on the de-vigged definition; this
restates the historical simulated_bets rows to match.

Recomputes clv_pinnacle (and clv_pinnacle_live where odds_at_pick_live exists)
from get_devigged_pinnacle_close_prob — the same function settlement now uses.

--dry-run (default): reports how many rows would change and the before/after
distribution, writes nothing. Pass --apply to write.
"""
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv; load_dotenv()
from workers.api_clients.db import execute_query, execute_write
from workers.jobs.settlement import get_devigged_pinnacle_close_prob


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    rows = execute_query("""
        SELECT id::text, match_id::text, market, selection,
               odds_at_pick, odds_at_pick_live, clv_pinnacle AS old_cp
        FROM simulated_bets
        WHERE result IN ('won','lost')
          AND odds_at_pick IS NOT NULL
          AND match_id IS NOT NULL
        ORDER BY pick_time DESC
    """ + (f" LIMIT {int(args.limit)}" if args.limit else ""))
    print(f"settled simulated_bets to restate: {len(rows):,}")

    changed = 0
    olds, news = [], []
    updates = []
    for r in rows:
        tp = None
        try:
            tp = get_devigged_pinnacle_close_prob(r["match_id"], r["market"], r["selection"])
        except Exception:
            tp = None
        if not tp:
            continue
        new_cp = round(float(r["odds_at_pick"]) * tp - 1.0, 4)
        new_cpl = (round(float(r["odds_at_pick_live"]) * tp - 1.0, 4)
                   if r["odds_at_pick_live"] else None)
        old_cp = float(r["old_cp"]) if r["old_cp"] is not None else None
        if old_cp is None or abs((old_cp or 0) - new_cp) > 1e-6:
            changed += 1
            if old_cp is not None:
                olds.append(old_cp); news.append(new_cp)
            updates.append((new_cp, new_cpl, r["id"]))

    def _mean(x): return sum(x)/len(x) if x else 0.0
    print(f"rows with a resolvable de-vigged CLV: {len(updates):,}")
    print(f"rows whose clv_pinnacle CHANGES: {changed:,}")
    if olds:
        print(f"  mean clv_pinnacle  BEFORE (vigged): {_mean(olds):+.4f}")
        print(f"  mean clv_pinnacle  AFTER (de-vig)  : {_mean(news):+.4f}")
        print(f"  mean shift: {_mean(news)-_mean(olds):+.4f}")
        print("  sample (old → new):")
        for o, n in list(zip(olds, news))[:8]:
            print(f"    {o:+.4f} → {n:+.4f}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Pass --apply to write.")
        return 0

    print(f"\nAPPLYING {len(updates):,} updates...")
    for i in range(0, len(updates), 1000):
        for new_cp, new_cpl, bid in updates[i:i+1000]:
            execute_write(
                "UPDATE simulated_bets SET clv_pinnacle=%s, clv_pinnacle_live=%s WHERE id=%s",
                [new_cp, new_cpl, bid])
        print(f"  {min(i+1000,len(updates)):,}/{len(updates):,}")
    print("DONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
