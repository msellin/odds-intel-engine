Parent: PRIORITY_QUEUE.md #101 EE-SWEEPERS-2026-09-23 (Tonybet sub-item)

- [x] Capability map (step 0)
- [x] Migration: `book_fair_probs` (383)
- [x] `workers/automation/tonybet_feed.py` (fetch, match, parse, store, fair probs, raw archive)
- [x] Dry run on the VPS: 188/252 matched, 5,102 rows/sweep, favourites agree 95–97% vs Epicbet/Coolbet
- [x] Scheduler job + health staleness list
- [x] Smoke test `TONYBET-SWEEPER`
- [x] Docs: DATA_SOURCES, WORKFLOWS, INFRASTRUCTURE, SYSTEM_MAP line
- [ ] Phase 1b: deep board — every market family in the payload (corners, cards, team totals, 1H, goalscorer), not just the six
- [ ] Phase 2: live score/clock/corners/cards (120 s) + results FT/HT/2H within 24 h
- [ ] Deploy + first scheduled run verified
- [ ] Acceptance 1–3 (then ACCESSIBLE_BOOKMAKERS promotion, separate commit)
