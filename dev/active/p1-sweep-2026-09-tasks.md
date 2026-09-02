# P1 Sweep — task checklist

## 1. STALE-ODDS-HISTORY-RESTATE  [in progress]
- [x] migration: `odds_at_pick_live` on simulated_bets + shadow_bets
- [x] backfill script (latest-per-book max at pick_time, accessible books)
- [x] run backfill, report coverage
- [x] audit scripts publish live-priced ROI + coverage; keep claimed as secondary
- [ ] landing reads restated figure
- [x] smoke test + mutation check
- [ ] docs (PRIORITY_QUEUE, ANALYSIS_GOTCHAS, MODEL_WHITEPAPER if model logic)

## 2. AF-STALE steps 2-4
- [ ] trust order: book date wins over AF for scheduled fixtures
- [ ] `date_source` column surviving the 04:00 AF re-sync
- [ ] suppress pick generation on disputed fixtures
- [ ] smoke

## 3. BET365-EXECUTION-AUDIT
- [ ] (blocked on 1)

## 4. SWEEP-HOME-BOTS-CALIBRATION
- [ ] (blocked on 1) confound already ruled out — go straight to model

## 5. BOT-GATE-OU-BTTS
- [ ] (blocked on 4)

## 6. AF-QUOTA-REALLOCATION
- [ ] measure current split
- [ ] proposal -> OPERATOR SIGN-OFF -> ship
