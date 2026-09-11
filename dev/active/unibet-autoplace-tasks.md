# UNIBET-AUTOPLACE — tasks

## Done 2026-09-11
- [x] Recon: map Coolbet's gate stack + the router's dispatch seam
- [x] **FIX: dead Coolbet arm** — `page = up.attach(pw)` got a tuple; the router
      could never place or stage at Coolbet, and failed indistinguishably from a
      legitimate decline
- [x] **FIX: loosened second gate** — `stage_bet` called without `edge_threshold`,
      so the live-price re-check used 0.03 instead of the bot's 0.08/0.10
- [x] **FIX: the double-bet bug** — Unibet placements wrote nothing, so
      `_has_exposure()` (the ONLY cross-book dedup) was blind to them
- [x] Session heartbeat `ensure_logged_in()` on the Unibet arm before placement
- [x] Routing audit: every book PRICED is recorded with why it won/lost
- [x] Smoke `ROUTER-UNIBET-PARITY`; fixed 2 stale assertions in sibling tests

## Before the switch can be flipped — NOT yet done
- [ ] **Coolbet arm does not store the routing note.** `stage_bet` writes
      `real_bets` itself, so router-placed Coolbet rows carry no rationale. The
      analysis would be one-sided (Unibet-only). Either thread `notes` through
      `stage_bet`, or persist the router's own decision log (better — it also
      captures picks where NO book cleared, which `real_bets` never sees).
- [x] **DONE 2026-09-11 — gate-stack parity.** Ported the four missing gates into
      `route()`, REUSING `place_coolbet_ui`'s own functions rather than
      reimplementing (canon_bet collapses the two market vocabularies in
      real_bets; a guard without it sees half the book):
      already_placed (fail-closed) · kickoff cutoff KICKOFF_CUTOFF_MIN · per-match
      exposure_conflict (also blocks same-FAMILY second opinions + per-match caps,
      stronger than the old exact-match check) · daily MAX_BETS_PER_DAY /
      MAX_STAKE_PER_DAY which ABORT the run rather than skip the pick.
      In-pass `held` append + day counters so a within-run race cannot
      double-place (the Airbus UK incident: 3 bets at 13:00/13:02/13:02).
      Stage mode occupies the guard too, so a dry run cannot look rosier than
      the real run. Verified live in report mode: 7 candidates -> 4 blocked by
      cross-book dedup, 3 no-book-clears, 0 routed, day_start 4 bets/EUR40.
- [ ] **No live dry-test.** `route(stage=True)` has never run green end-to-end
      since the Coolbet arm was dead. Must see a clean stage-in-action on a real
      candidate first.
- [ ] `unibet_placer` balance parsing can report a real placement as not-placed
      (bare except on a locale string) — that would mean a placed bet with no
      `real_bets` row, i.e. the double-bet bug again by another route.
- [ ] **OWNER: set `ROUTER_ALLOW_REAL`** — last step, after all the above.
