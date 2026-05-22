---
task: COMBO-RESTRUCTURE
status: in_progress
started: 2026-05-22
---

## Tasks

### A — Bot restructure (`acca_bot.py`)
- [x] Add `require_ou15` flag to all ACCA_VARIANTS (True for all)
- [x] Fix all variants to N=5 only (min_legs=5, max_legs=5)
- [x] `bot_acca_value` + `bot_acca_proven`: structure stays `straight`
- [x] `bot_combo_system` + `bot_combo_proven_system`: structure → `fours_up`
- [x] `_pick_legs()`: enforce OU15/over in selected legs when require_ou15=True
- [x] `settle_combo_bet()` in settlement.py: add `fours_up` branch
- [x] Migration 118: update bot strategy descriptions in DB

### B — real_bets schema + settlement
- [x] Migration 118: add `combo_legs JSONB`, `system_type TEXT` to real_bets
- [x] `store_real_bet()`: add optional combo_legs + system_type params
- [x] `settlement.py`: `_settle_real_combo_bets()` — settle pending combo real_bets when all legs finished

### C — Admin UI
- [x] New API route `/api/admin/record-combo/route.ts`
- [x] New component `RecordComboModal`
- [x] `place-bet-table.tsx`: add "Record" button for combo rows

### D — Smoke tests + docs
- [x] Add smoke tests for new bot structure, fours_up settlement, record-combo route
- [x] Update PRIORITY_QUEUE.md
- [x] Update MODEL_WHITEPAPER.md (bot strategy change)
- [x] Update ROADMAP.md
