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
- [ ] **The router bypasses Coolbet's per-pick gates 1-7.** Confirmed in recon:
      no `already_placed`, no kickoff cutoff, no per-match MARKET_FAMILY guard,
      **no daily bet/stake caps**. Its only guard is `_has_exposure`. Routing real
      money through it today would run with a thinner gate stack than
      `place_coolbet_ui.py --execute` already enforces.
- [ ] **No live dry-test.** `route(stage=True)` has never run green end-to-end
      since the Coolbet arm was dead. Must see a clean stage-in-action on a real
      candidate first.
- [ ] `unibet_placer` balance parsing can report a real placement as not-placed
      (bare except on a locale string) — that would mean a placed bet with no
      `real_bets` row, i.e. the double-bet bug again by another route.
- [ ] **OWNER: set `ROUTER_ALLOW_REAL`** — last step, after all the above.
