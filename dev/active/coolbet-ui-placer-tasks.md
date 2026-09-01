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

## Done (2026-08-27 → 08-31, confirmed live 2026-09-01)

- [x] **Placement is proven in production.** The operator loaded the launchd
      job; `place_coolbet_ui.py` now runs hourly 06:00–21:00 UTC with
      `--execute` (real money) out of this working directory. **51 real bets
      placed 2026-08-27 → 08-31** (`real_bets.notes LIKE 'ui-placer%'`),
      50 settled, **−€33.90 / −6.78% ROI**. 23 `place`-stage rows in
      `coolbet_placement_attempts` in the last 3 days alone.
- [x] **OU support on the UI path.** 18 of the 51 bets are OU
      (`over_under_35` ×11, `over_under_25` ×7). AH is still open — see below.
- [x] `placement_paused` cleared — currently `False`, no pause reason set.

## Status check 2026-09-01

This list had drifted badly and was **actively misleading on live real-money
code**: it still claimed the first confirmed placement was unproven and that
OU was unimplemented, while 51 real bets — including 18 OU — had already been
placed. Both moved to Done above with the evidence. Verify against `real_bets`
and `coolbet_placement_attempts` before trusting any line below.

## Open

- [ ] **`empty_slip` cannot clear a leftover selection.** The per-selection
      trash icon is located correctly (16px svg beside the odds text) but
      refuses a programmatic click — dispatched MouseEvents are ignored by
      React and a real Playwright click times out at x=1276, likely clipped.
      Fails SAFE: `stage_bet` refuses to run on a dirty slip rather than
      risking a wrong bet, so the cost is a blocked pass, not a bad wager.
      One manual click clears it. Try widening the window or the keyboard path.
- [ ] Fix cold-Chrome dead-end: `cdp_auto_login` reuses a Coolbet tab but never
      opens one, so a pageless Chrome cannot be attached to at all
      (`Browser.setDownloadBehavior` protocol error). Open via `/json/new` first.
- [ ] `playwright` is still absent from `requirements.txt`. Partially
      mitigated 2026-09-01: its import in `place_coolbet_ui.py` was made lazy,
      so reading a constant no longer needs the driver and the smoke test runs
      in CI. **Do not simply add it to `requirements.txt`** — that file is
      installed on the VPS, which never drives a browser, and playwright pulls
      browser binaries. It is a Mac-only dependency; pin it somewhere that
      reflects that.
- [ ] `session_healthy=False` with `last_error = "heartbeat: maintenance probe
      returned non-200"` as of 2026-09-01 07:55, and `coolbet_health_ping` has
      been failing every 5 min for days. Placement still works (the UI path has
      no cookie-freshness dependency — that is the point of it), so this is a
      **monitoring** signal that is permanently red rather than a placement
      outage. A health check that is always red is not a health check.
- [ ] Fresh-window read on `bot_coolbet_value_v1` before wiring picks → placement.
- [ ] Finish COOLBET-SQUAD-GUARD — the API path
      (`coolbet_placer.fuzzy_match_event`) is still exposed; only the UI path
      is guarded.
- [ ] **AH** support on the UI path. OU shipped; AH is still unimplemented
      (no `asian_handicap` in `coolbet_ui_placer.py`) and raises rather than
      guessing a line. Note `AH-NO-QUARTER`: Coolbet offers only full/half
      lines.
- [ ] Re-validate `inplay_l` on a fresh window; its last 48 bets are −3.4%.
