# Coolbet in-play — tasks

- [x] Verify transport works live (2 fixtures, dedicated FS session) — ~6 s/fixture
- [x] Measure `limit=13` vs `limit=300` on a LIVE match — 8/12 vs 39/48 groups/markets
- [x] Confirm `book_event_map` resolves live fixtures — 13/24 Coolbet, 21/24 Unibet-Site
- [x] Fix live sidebets limit (13 → non-binding) + correct the stale comment
- [x] Coolbet in-play collector: serial, dedicated FS session, writes `inplay_book_quotes`
- [x] Fixture selection = AF-live ∩ Coolbet-mapped ∩ Epicbet board
- [x] Smoke tests: never `coolbet_prod`; serial-only; limit not 13; book label correct
- [x] launchd plist (Mac, KeepAlive) + repo copy
- [x] Docs: WORKFLOWS, COOLBET_RUNBOOK, SYSTEM_MAP if a bot appears (it should not)
- [ ] THE MEASUREMENT: cross-book suspension lead, with the pre-registered stop
