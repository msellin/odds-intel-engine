Parent: PRIORITY_QUEUE.md #101 EE-SWEEPERS-2026-09-23 (Tonybet sub-item)

- [x] Capability map (step 0)
- [x] Migration: `book_fair_probs` (383)
- [x] `workers/automation/tonybet_feed.py` (fetch, match, parse, store, fair probs, raw archive)
- [x] Dry run on the VPS: 188/252 matched, 5,102 rows/sweep, favourites agree 95–97% vs Epicbet/Coolbet
- [x] Scheduler job + health staleness list
- [x] Smoke test `TONYBET-SWEEPER`
- [x] Docs: DATA_SOURCES, WORKFLOWS, INFRASTRUCTURE, SYSTEM_MAP line
- [x] Phase 1b: deep board at T-24h/3h/30m + close; corners, bookings, team totals, 1H/2H goals, 1H/2H result, BTTS by half, 1H DC. Player/goalscorer/combos → raw archive only
- [x] Phase 2: live stats every 120 s (`book_live_stats`) + results every 2 h (`book_match_results`), migration 385
- [ ] After ~1 week: validate `book_match_results` FT/HT vs AF scores, and live corners/cards vs `match_stats` where AF has them
- [ ] Parse player/goalscorer markets (needs `players` relation for names) — from the raw archive back to 2026-09-23
- [x] Deploy + first scheduled run verified (14:01 UTC: 550 events, 164/218 matched, 4,714 rows, 4,717 fair probs)
- [x] Acceptance 1–3 → promoted to ACCESSIBLE_BOOKMAKERS
