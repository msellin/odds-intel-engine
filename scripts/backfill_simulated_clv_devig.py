"""META-MODEL-CLV-TARGET-2026-08-26 — backfill simulated_bets.clv_pinnacle_devig.

simulated_bets already has `clv_pinnacle`, but it is the RAW vig-inclusive value:
it carries Pinnacle's overround, so it reads positive by roughly the margin on a
bet with no edge. It is also computed through get_pinnacle_closing_odds(), whose
fallback had no kickoff cutoff until PIN-CLOSE-PRE-KO-FALLBACK-2026-08-26 — on a
match where is_closing was never marked it could return an IN-PLAY tick.

That column is the training label for `train_b_ml3.py --bets-mode`. A meta-model
learning "will this bet beat the closing line" from labels that are shifted by
the overround and occasionally sourced from live prices is learning a corrupted
target. Migration 283 added clv_pinnacle_devig for the clean version; this fills
it.

Bulk, not row-by-row: the Pinnacle closing prices are loaded once per
match/market and reused across every row that shares them.

Usage:
    python3 scripts/backfill_simulated_clv_devig.py --dry-run
    python3 scripts/backfill_simulated_clv_devig.py
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _market_complement_selections,
    _normalize_bet_market,
    _normalize_bet_selection,
)
from workers.model.devig import devig  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()

    bets = execute_query(
        """
        -- CLV-DEVIG-STALE-PRICE-2026-09-05: price the CLV at the odds that were
        -- actually available, not `odds_at_pick`, which STALE-BEST-ODDS showed is a
        -- MAX() high-water mark across the fixture's whole snapshot history. Pricing
        -- CLV off a price nobody could take inflates it, and the inflation SCALES
        -- WITH ODDS (measured -2.75pp at 1.0-1.8 rising to -6.62pp at 3.5+), which
        -- manufactured a fake "our edge lives at long odds" slope.
        SELECT id, match_id, market, selection,
               COALESCE(NULLIF(odds_at_pick_live, 0), odds_at_pick) AS odds_at_pick
          FROM simulated_bets
         WHERE result IN ('won','lost') AND clv_pinnacle_devig IS NULL
        """,
        [],
    )
    print(f"{len(bets)} settled simulated_bets rows without clv_pinnacle_devig")
    if not bets:
        return 0

    match_ids = sorted({str(b["match_id"]) for b in bets})
    print(f"spanning {len(match_ids)} matches — loading Pinnacle closes")

    closes: dict = {}
    for i in range(0, len(match_ids), 500):
        chunk = match_ids[i : i + 500]
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.selection)
                   o.match_id, o.market, o.selection, o.odds
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = 'Pinnacle'
               AND o.timestamp <= m.date
             ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
            """,
            [chunk],
        )
        for r in rows:
            closes[(str(r["match_id"]), r["market"], r["selection"])] = float(r["odds"])
    print(f"loaded {len(closes)} Pinnacle closing prices")

    # Cache the de-vig per (match, market) — one solve serves every row on it.
    prob_cache: dict = {}
    updates: list[tuple] = []
    skipped = defaultdict(int)

    for b in bets:
        mid = str(b["match_id"])
        mkt = _normalize_bet_market(b["market"], b["selection"])
        sel = _normalize_bet_selection(b["selection"])
        # DOUBLE-CHANCE-CLV-2026-08-26: DC has no Pinnacle price of its own, but
        # each DC outcome is a union of 1X2 outcomes, and the de-vigged 1X2
        # probabilities partition the space — so P(1X) = P(home) + P(draw), etc.
        # The DC bots are the highest-volume in the fleet and had no CLV at all.
        _DC = {"1x": ("home", "draw"), "12": ("home", "away"), "x2": ("draw", "away")}
        if mkt == "double_chance":
            legs = _DC.get(sel)
            if not legs:
                skipped["dc_bad_selection"] += 1
                continue
            base = ["home", "draw", "away"]
            key = (mid, "1x2")
            if key not in prob_cache:
                odds = [closes.get((mid, "1x2", s2)) for s2 in base]
                prob_cache[key] = (None if any(o is None or o <= 1.0 for o in odds)
                                   else devig(odds))
            probs = prob_cache[key]
            if probs is None:
                skipped["no_full_pinnacle_close"] += 1
                continue
            p = sum(probs[base.index(l)] for l in legs)
            if not (0.0 < p < 1.0):
                skipped["bad_prob"] += 1
                continue
            updates.append((round(float(b["odds_at_pick"]) * p - 1.0, 4), b["id"]))
            continue

        sides = _market_complement_selections(mkt, sel)
        if not sides or sel not in sides:
            skipped["market_not_deviggable"] += 1
            continue
        key = (mid, mkt)
        if key not in prob_cache:
            odds = [closes.get((mid, mkt, s)) for s in sides]
            if any(o is None or o <= 1.0 for o in odds):
                prob_cache[key] = None
            else:
                prob_cache[key] = devig(odds)
        probs = prob_cache[key]
        if probs is None:
            skipped["no_full_pinnacle_close"] += 1
            continue
        p = probs[sides.index(sel)]
        if not (0.0 < p < 1.0):
            skipped["bad_prob"] += 1
            continue
        updates.append((round(float(b["odds_at_pick"]) * p - 1.0, 4), b["id"]))

    print(f"computable: {len(updates)}   skipped: {dict(skipped)}")
    if args.dry_run:
        for v, i in updates[:10]:
            print(f"  {i} -> clv_pinnacle {v:+.4f}")
        return 0

    done = 0
    for i in range(0, len(updates), args.batch):
        chunk = updates[i : i + args.batch]
        execute_write(
            """
            UPDATE simulated_bets AS s SET clv_pinnacle_devig = v.clv
              FROM (SELECT unnest(%s::double precision[]) AS clv,
                           unnest(%s::uuid[]) AS id) AS v
             WHERE s.id = v.id
            """,
            [[c for c, _ in chunk], [str(i2) for _, i2 in chunk]],
        )
        done += len(chunk)
        print(f"  updated {done}/{len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
