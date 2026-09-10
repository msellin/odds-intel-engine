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
## Step 3 — Engine writers canonical ✅ DONE 2026-09-10 (91ed7d5)
- [x] canonicalize_for_storage applied at store_bet / bulk_store_shadow_bets / store_real_bet / placer reconcile INSERT (chokepoint approach, not 110 literals)
- [x] real_bets canonical via store_real_bet + placer INSERT
- [x] canonicalize_for_storage verified: AH/combo preserved, unknown passthrough, idempotent
## Step 4 — Backfill ✅ DONE + VERIFIED 2026-09-10 (script 048b4fd, executed)
- [x] scripts/backfill_canonicalize_vocab.py (dedup-aware, dry-run, plausibility guard); EXECUTED: sim 1421 / shadow 21478 / real 403 rows; 0 collisions; VERIFY CLEAN
- [x] O/U mirror feed intact (89==89 picks pre/post); /picks + /performance render correctly; 1 source typo ('over 25') skipped for manual review
## Step 5 — Strict enforcement ✅ DONE 2026-09-10
- [x] MARKET-VOCAB-ENFORCED now has strict part: bet tables must store ONLY canonical; legacy reappearing fails CI
