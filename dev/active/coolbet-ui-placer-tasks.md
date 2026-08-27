# COOLBET-UI-PLACER — tasks

## Done (2026-08-27)

- [x] Audit what already exists — found the whole chain built, never used
      (all 837 `real_bets` carry `ticket=None`)
- [x] Establish live state — daemon dead since 08-23, JWT expired, paused
- [x] Drop FlareSolverr from the Mac path in favour of CDP cookies
- [x] Fix infinite recursion in `_sync_playwright_factory` (RecursionError)
- [x] Verify login-form selectors against the live DOM before trusting them
- [x] Recover a session unattended — no SMS (`reese84` survived)
- [x] Harvest + persist 6 fresh Imperva cookies from CDP-Chrome
- [x] Map the UI: search, result link, odds button, stake field, place button
- [x] Build `workers/automation/coolbet_ui_placer.py` (stage-only default)
- [x] Port the squad guard onto the UI matching path
- [x] Stage a real bet end to end (FK Partizan @ 3.30, €1) — place btn disabled
- [x] Investigate in-play bots: odds reality, profitability, decay
- [x] Smoke test `COOLBET-UI-PLACER`

## Done (2026-08-27, later)

- [x] OU ladder from the `Väravate arv (Üle/Alla)` card — all 20 picks resolve
- [x] Diacritic folding + `Austria Wien` alias — the last two match failures
- [x] Min-odds gate `(1 + threshold) / prob` as the authoritative price check
- [x] `coolbet_placement_attempts` writer — every attempt, with a reason
- [x] Betslip hygiene: deselect after staging, refuse a dirty slip, refuse to
      place unless the slip holds exactly one selection
- [x] Two-step confirm in `place()` + **balance-delta confirmation**
- [x] Periodic runner: dedup on confirmed placements, kickoff cutoff, daily
      count + stake ceilings, pick marking (2=placed / 1=checked)
- [x] launchd plist (`RunAtLoad=false` — loading must not place a bet)

## Blocked

- [ ] **Placement itself is operator-run.** The agent builds and verifies the
      whole path but does not execute real-money placement, and will not load
      the launchd job. `launchctl load` is the operator's step.

## Open

- [ ] **`empty_slip` cannot clear a leftover selection.** The per-selection
      trash icon is located correctly (16px svg beside the odds text) but
      refuses a programmatic click — dispatched MouseEvents are ignored by
      React and a real Playwright click times out at x=1276, likely clipped.
      Fails SAFE: `stage_bet` refuses to run on a dirty slip rather than
      risking a wrong bet, so the cost is a blocked pass, not a bad wager.
      One manual click clears it. Try widening the window or the keyboard path.
- [ ] **First confirmed placement is still unproven.** The two-step confirm is
      written defensively against several button shapes because it can only be
      observed mid-placement. A wrong guess reports "not confirmed" rather than
      claiming a phantom bet, but the happy path has not been seen once.
- [ ] Fix cold-Chrome dead-end: `cdp_auto_login` reuses a Coolbet tab but never
      opens one, so a pageless Chrome cannot be attached to at all
      (`Browser.setDownloadBehavior` protocol error). Open via `/json/new` first.
- [ ] Add `playwright` (and optionally `patchright`) to `requirements.txt` —
      neither is listed, which is how bug 1 drifted in unnoticed.
- [ ] Diagnose the daemon's 7 consecutive errors **before** clearing
      `placement_paused`. Do not clear it to make things look green.
- [ ] Fresh-window read on `bot_coolbet_value_v1` before wiring picks → placement.
- [ ] Finish COOLBET-SQUAD-GUARD — the API path
      (`coolbet_placer.fuzzy_match_event`) is still exposed; only the UI path
      is guarded.
- [ ] OU/AH support on the UI path (Estonian `Üle/Alla 2.5` line matching).
- [ ] Re-validate `inplay_l` on a fresh window; its last 48 bets are −3.4%.
