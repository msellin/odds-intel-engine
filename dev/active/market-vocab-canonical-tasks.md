# MARKET-VOCAB-CANONICAL Phase 2 — TASKS

## Step 1 — Frontend tolerant ✅ DONE + VALIDATED IN PROD 2026-09-10 (frontend 8426ecb + c865ee1)
- [x] Create odds-intel-web/src/lib/market-vocab.ts (normalizeMarket + marketLabel) — DONE, verified parity, pushed 8426ecb (dead code until wired)
- [x] PRE_MATCH_MARKETS + CALIBRATED_PUBLIC_MARKETS already list both spellings — forward-compatible, no change + CALIBRATED_PUBLIC_MARKETS to include canonical AND legacy (or normalize on read)
- [x] _mapPaperToSnapshotKey + calibrationKey rerouted through normalizeMarket (VERIFIED zero regression, canonical now handled) through the normalizer (no more silent null on canonical)
- [x] formatMarket lowercase-hardened; engine-data.ts:3051 reads odds_snapshots (already canonical) — no change needed (engine-data.ts:3051, picks/page.tsx:42)
- [x] formatMarket + fmtSelShort routed/hardened (fmtSelShort takes O/U line from market) through marketLabel (accept both)
- [x] tsc --noEmit clean; deployed; /picks + /performance validated rendering + labels correct in prod /picks + /performance still populate
## Step 2 — Engine readers tolerant
- [ ] Route exact-match readers through canonical_market.normalize()
- [ ] Retire settlement.py duplicate O/U normalizer → canonical_market
- [ ] settlement golden fixture passes
## Step 3 — Engine writers canonical
- [ ] daily_pipeline_v2 emits canonical (Market/Selection) into simulated_bets + shadow_bets
- [ ] placers emit canonical into real_bets
- [ ] fresh pipeline run writes canonical (verify)
## Step 4 — Backfill
- [ ] supabase/migrations/NNN_canonicalize_bet_vocab.sql (shadow/simulated/real; AH/combo preserved; idempotent)
- [ ] verify counts + /picks + /performance populate post-backfill
## Step 5 — Strict enforcement
- [ ] flip MARKET-VOCAB-ENFORCED to "DB only canonical for these families"
