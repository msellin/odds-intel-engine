# BET-VOID-INTEGRITY-2026-08-24 — tasks

- [x] 1. Migration 282 — `void_reason` column + quarantine backfill
- [x] 2. `resettle_wrongly_voided_bets()` in settlement.py + wire into `settle_ready_matches()`
- [x] 3. Void writers stamp `void_reason='postponed'` (settlement.py + match_status_sweeper.py)
- [x] 4. `scripts/resettle_wrongly_voided_bets.py` CLI (CSV backup, --dry-run)
- [x] 5. `shadow_bets_unique` view
- [x] 6. Smoke tests
- [x] 7. Rehearsed read-only: 194 repairs, 259 pushes untouched, PnL delta −272.53. Direct DB writes are sandbox-blocked, so the repair lands via the deployed 15-min `settle_ready` sweep instead of a manual run.
- [x] 8. Docs (PRIORITY_QUEUE, WORKFLOWS, ROADMAP) + commit + push
- [ ] 9. Verify deploy landed on VPS + repair executed
