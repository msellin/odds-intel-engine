# MARKET-VOCAB-CANONICAL Phase 2 — TASKS

## Step 1 — Frontend tolerant (additive, breaks nothing)
- [ ] Create odds-intel-web/src/lib/market-vocab.ts (normalizeMarket + marketLabel), mirror canonical_market
- [ ] Route PRE_MATCH_MARKETS + CALIBRATED_PUBLIC_MARKETS to include canonical AND legacy (or normalize on read)
- [ ] Route _mapPaperToSnapshotKey + calibrationKey through the normalizer (no more silent null on canonical)
- [ ] Fix the 2 non-lowercased compares (engine-data.ts:3051, picks/page.tsx:42)
- [ ] Route the 4 label fns through marketLabel (accept both)
- [ ] Verify build; deploy; eyeball /picks + /performance still populate
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
