# NEAR-KICKOFF-CAPTURE + DIRECT-BOOK-CLV — tasks

- [x] Claim both in PRIORITY_QUEUE (🔄)
- [x] Migration 332 real_bets closing columns
- [x] settlement: get_book_close / real_bet_closing / real-bet settle uses own book
- [x] backfill + direct_book_clv_report scripts
- [x] web: real-bets-log Pin CLV column + tooltips; engine-data fields
- [x] Migration 333 book_event_map + record_book_events
- [x] Sweeps record pairings (Coolbet, Epicbet, Unibet-Site)
- [x] near_kickoff_capture job + launchd plist (NOT loaded)
- [x] closing_snap 24/7
- [x] Live dry-run of Coolbet (NO_FS, 322 rows) / Epicbet (direct, 99 rows); Unibet untested (operator Chrome)
- [x] Smoke tests (update pinned REAL-BETS-CLV-EDGE-SCHEMA; add new)
- [x] Docs ripple: WORKFLOWS, ROADMAP, DATA_SOURCES, INFRASTRUCTURE, ANALYSIS_GOTCHAS §63, PRIORITY_QUEUE
- [x] Sub-agent verification pass — found AH bug (fixed: rung pinned, home-perspective line), in-play race (fixed: re-check kickoff per fetch), NO_FS forced, Epicbet FS id defaulted in code
- [ ] Commit + push engine and web
- [ ] After migrations apply: run backfill, run report
- [ ] Owner go → load plist on Mac
