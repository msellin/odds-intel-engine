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

## Added after the verification pass (all done 2026-09-18)

- [x] Suspension recorded as data, not absence (was destroying the measurement)
- [x] De-dup markets returned by both fo-match and sidebets
- [x] Honour `daemons_paused` footprint pause, failing closed
- [x] Populate league/home/away from DB; store real `seconds`
- [x] Log + count empty boards; make per-cycle counters per-cycle
- [x] Replace the vacuous NEVER-PROD assertion; mutation-verify every guard
- [x] Whitelist `coolbet_inplay` in the FS stale-session sweeper
- [x] Correct the `limit=13` docstring the previous commit left stale
- [x] Delete the 86 malformed pre-fix rows
