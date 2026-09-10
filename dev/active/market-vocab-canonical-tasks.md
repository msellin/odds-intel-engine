# MARKET-VOCAB-CANONICAL Phase 2 — TASKS

## Step 1 — Frontend tolerant ✅ DONE + VALIDATED IN PROD 2026-09-10 (frontend 8426ecb + c865ee1)
- [x] Create odds-intel-web/src/lib/market-vocab.ts (normalizeMarket + marketLabel) — DONE, verified parity, pushed 8426ecb (dead code until wired)
- [x] PRE_MATCH_MARKETS + CALIBRATED_PUBLIC_MARKETS already list both spellings — forward-compatible, no change + CALIBRATED_PUBLIC_MARKETS to include canonical AND legacy (or normalize on read)
- [x] _mapPaperToSnapshotKey + calibrationKey rerouted through normalizeMarket (VERIFIED zero regression, canonical now handled) through the normalizer (no more silent null on canonical)
- [x] formatMarket lowercase-hardened; engine-data.ts:3051 reads odds_snapshots (already canonical) — no change needed (engine-data.ts:3051, picks/page.tsx:42)
- [x] formatMarket + fmtSelShort routed/hardened (fmtSelShort takes O/U line from market) through marketLabel (accept both)
- [x] tsc --noEmit clean; deployed; /picks + /performance validated rendering + labels correct in prod /picks + /performance still populate
## Step 2 — Engine readers tolerant ✅ DONE 2026-09-10
- [x] O/U mirror (coolbet_model_ou_shadow) = the ONLY legacy simulated_bets reader; now accepts both encodings via normalize() + DISTINCT ON (match,market,selection). VERIFIED pick-identical on 89 real historical picks. (1x2 mirror reads already-canonical '1x2'; ou35/corners read canonical odds_snapshots — no change.)
- [~] settlement.py LEFT AS-IS: audit confirmed it already tolerates canonical ('over_under' in m); retiring its dup normalizer deferred as optional cleanup (minimise grading risk).
- [x] SETTLEMENT-GOLDEN passes (grades byte-identical). 3 SETTLE failures (AH/DC/void) are PRE-EXISTING — settlement.py untouched by this work.
## Step 3 — Engine writers canonical
- [ ] daily_pipeline_v2 emits canonical (Market/Selection) into simulated_bets + shadow_bets
- [ ] placers emit canonical into real_bets
- [ ] fresh pipeline run writes canonical (verify)
## Step 4 — Backfill
- [ ] supabase/migrations/NNN_canonicalize_bet_vocab.sql (shadow/simulated/real; AH/combo preserved; idempotent)
- [ ] verify counts + /picks + /performance populate post-backfill
## Step 5 — Strict enforcement
- [ ] flip MARKET-VOCAB-ENFORCED to "DB only canonical for these families"
