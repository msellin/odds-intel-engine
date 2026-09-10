# MARKET-VOCAB-CANONICAL Phase 2 — CONTEXT (continue-here)

Owner chose FULL canonicalization (2026-09-10), "make sure you are careful."

## State
- Phase 1 shipped: workers/canonical_market.py (Market/Selection enums + normalize()), smoke
  MARKET-VOCAB-ENFORCED, scripts/executable_shadow_eval.py. Commits 46aae53, 4cf4a81.
- normalize() covers 100% of bet-table vocab (0 unrecognised across shadow/simulated/real, 120d).
- Blast-radius map complete — see -plan.md. Frontend is the dominant risk (public /picks + /performance).

## Key facts to not re-derive
- odds_snapshots ALREADY canonical — never touch it.
- Non-canonical lives only in shadow_bets (~21k), simulated_bets (~1.5k), real_bets (~0.5k).
- settlement.py already tolerates mixed vocab (has a duplicate O/U normalizer to retire).
- Primary bad writer = daily_pipeline_v2 (1X2/O/U/'over 2.5'); + placers for real_bets.
- AH selection carries the line ('home -1.5') with NO line column → PRESERVE selection, don't strip.
- combo = passthrough. predictions '1x2_home' = separate namespace, exclude.
- Frontend auto-deploys on push (pm2). Frontend at ../odds-intel-web.

## Sequence + where we are
1. Frontend shared normalizer (src/lib/market-vocab.ts) + route 5 high-risk filters/bridges +
   4 label fns through it (accept BOTH spellings). ADDITIVE. ← START HERE
2. Engine readers via normalize(); retire settlement dup normalizer; settlement golden must pass.
3. Engine writers (daily_pipeline_v2 + placers) emit canonical.
4. Backfill migration (3 bot tables; AH/combo preserved); verify /picks + /performance populate.
5. Flip MARKET-VOCAB-ENFORCED to strict (DB only canonical for these families).
Each stage = own commit + verification. Steps 3-4 ONLY after 1-2 verified in prod.

## High-risk frontend sites (from map)
PRE_MATCH_MARKETS (upcoming-picks.ts:121), CALIBRATED_PUBLIC_MARKETS (engine-data.ts:3412),
PublishedPickMarket bucketing (engine-data.ts:3844), _mapPaperToSnapshotKey (engine-data.ts:1757),
calibrationKey (real-money-tier.ts:71), non-lowercased engine-data.ts:3051 + picks/page.tsx:42.
Label fns: picks/page.tsx:41 formatMarket, place-bet-table.tsx:99 fmtSelShort, admin/shadow-bots labels.
