# OWN implementation — TASKS

Mark `[x]` as done; note the commit hash. Never run the full smoke suite locally.

## Visibility invariant (all phases)
- [x] smoke `OWN-BOTS-OFF-CUSTOMER-SURFACES` (maturity experimental, no simulated_bets / picks_forward_test rows, /performance filter present)
- [ ] 1a: FRESH/STALE badge + `decision_quote_age_min` column on /admin/shadow-bots upcoming rows
- [ ] 1b: IN-PLAY rows (minute, score, on-screen price, de-vigged prob) in the same section
- [ ] 2: Promotions panel on /admin/shadow-bots

## Phase 0 — placement safety (P0) — DONE 2026-09-15 (also: REAL-BETS-SHADOW-LINK FK fix, mig 354 adds real_bets.shadow_bet_id)
- [x] 0.A `workers/automation/placement_gate.py` with `assert_may_place`, `PlacementRefused`, `read_placement_paused_strict`
- [x] 0.A flip `is_placement_paused` / `is_daemons_paused` error default to paused; leave `is_publishing_paused`
- [x] 0.A mig 354 `coolbet_session_state.real_money_armed BOOLEAN NOT NULL DEFAULT FALSE`
- [x] 0.A move `PLACEABLE_BOTS`, `ui_place_enabled_bots`, `effective_allowlist` into the gate; shims in `place_coolbet_ui.py`
- [x] 0.B gate in `place_coolbet_ui.main()` (run-level)
- [x] 0.B gate in `coolbet_ui_placer.stage_bet` before `select_outcome`; remove late read
- [x] 0.B gate in `best_price_router.route()` + `_dispatch_unibet`; iterate `effective_allowlist()`
- [x] 0.B gate in `coolbet_placer.place_all_bets` and `place_bet_by_id`
- [x] 0.D `scripts/reconcile_placed_attempts_to_real_bets.py` + run once (1 missing row)
- [x] 0.D settlement: settle any `placed_real` row on a finished match; run once (3 rows)
- [x] 0.E `coolbet_control --status` host view + `CAN_STAKE` line
- [x] 0.F smoke: `PLACEMENT-GATE-FAIL-CLOSED`, `PLACEMENT-GATE-ALL-EXECUTORS`, `ROUTER-NO-ALLOWLIST-BYPASS`, `REAL-BETS-ATTEMPTS-RECONCILED`, `REAL-BETS-SETTLE-ANY-FINISHED`
- [x] docs: SYSTEM_MAP §4, COOLBET_OWN_BETTING, RELIABILITY_LEDGER §14, PRIORITY_QUEUE (all four in 6fdc37fa — the box was wrong, the work was done)
- [x] ⚖️ 0.C owner: bootout both `--execute` plists; move to `local/launchd/paused/`; fix header comment

## Phase 1a — sharp-tight freshness
- [x] mig 355 `shadow_bets.closing_margin`, `clv_margin_corrected`, `decision_quote_age_min`; views `shadow_bets_unique` (extended) + `shadow_bets_own_book_clv` (seen-stamps DROPPED — watchdogs count rows/window; row timestamp already is the observation time)
- [x] ~~dedup-on-change~~ dropped (see plan 1a.1)
- [x] retention exemption for the three own books, 60 days, in BOTH pruners (cost: ~2.8M rows/45d)
- [x] instrument refuses legs > 60 min old (`is_fresh_enough`); pre-registration Amendment 1 dated 2026-09-15
- [x] `scripts/sharp_tight_slope.py` (first output after mig 355 + backfill)
- [x] smoke: `OWN-BOOK-RETENTION-EXEMPT`, `SHARP-TIGHT-FRESHNESS-REFUSES-STALE`, `SHADOW-CLV-MARGIN-CORRECTED`
- [x] stop/promote rule recorded in the PRIORITY_QUEUE row (RETIRE at n=300 if the CI includes 0; PROMOTE only with CI>0, zero-crossing ≤+6pp and ≥2 fresh legs/day)

## Phase 1b — in-play slow-state rig (⚖️ go/no-go first)
- [x] `oufid.jsonl` — scratch file from the 09-14 session no longer exists on disk; O/U fidelity remains UNREPORTED, so the paper bot measures at Epicbet's own board (no AF O/U dependency) and the control arm uses AF only where it quotes the same market
- [x] `workers/jobs/inplay_collector.py` (Epicbet; Coolbet in-play collection FILED as follow-up — Imperva budget); Mac `KeepAlive` plist `com.oddsintel.inplay-collector`; heartbeat in `pipeline_health_state`
- [x] mig 357 `inplay_book_quotes` + VPS prune job 03:20 (90 d) + `shadow_bets.inplay_*` columns + the two bot rows
- [x] `bot_inplay_slowstate_v1` (T1, T2 locked, cap 2.20) + `bot_inplay_slowstate_afctl_v1` control arm; registry `FAM_INPLAY` + SYSTEM_MAP section
- [x] `scripts/inplay_slowstate_eval.py` (hit-rate lift, cluster-robust, power line, STOP/DECIDE verdict)
- [x] smoke: `INPLAY-COLLECTOR-HEARTBEAT`, `INPLAY-SLOWSTATE-TRIGGERS-LOCKED`, `INPLAY-SLOWSTATE-PRICE-IS-BOOK-NOT-AF`
- [x] stop rules recorded in the PRIORITY_QUEUE row (STOP n=1,000 if lift<0; DECIDE n=3,000 on the CI)

## Phase 2 — promotions (⚖️ accounts + T&Cs from owner)
- [x] mig 356 `promo_terms`, `promo_ledger`
- [x] `workers/automation/promo_ev.py` + `scripts/promo_ev.py` (fair / ev / terms / add-terms; boost, free-bet SNR/SR, acca insurance, deposit bonus, all under terms)
- [x] `scripts/promo_review.py` monthly (kill on 2 consecutive months > 1.5 sd below EV)
- [x] smoke: `PROMO-EV-FORMULAS`, `PROMO-LEDGER-EV-BEFORE-BET`

## Phase 3 — policy
- [x] clamped stake recorded as stage `stake_limit` with `stake_applied` = accepted amount; smoke `PLACEMENT-LOGS-MAX-STAKE`

## Phase 5 — cull (small items DONE 2026-09-15; big deletion filed)
- [ ] delete `inplay_bot.py`, `coolbet_inplay.py`, `place_all_inplay_bets` — DEFERRED → queue `INPLAY-BOT-DELETE` (10 live modules incl. live_poller + 139 test lines; not a same-day deletion)
- [x] dropped 4 retired `BOOK_MARKET_BOTS` entries; removed `WIDE_CONFIGS` and the retired line-shop O/U stop (+ env override); `_ODDS_TOLERANCE` KEPT (still used by the API placer legacy flow)
- [ ] wire or delete `PIN_CROSS_DRIFT_VETO_ENABLED` — left as a documented SHADOW counter (removing it changes pipeline output; decide with the model work, not OWN)
- [x] web README fixed (Next 16 / pm2); Stripe/tier dead code → queue `WEB-TIER-DEAD-CODE` (👥, not OWN)
- [x] no open CS2/tennis queue rows existed to close (the reviewer's point was missing VERDICT docs; CS2 verdict 2026-07-31 is recorded in the audit §3)
- [x] grep-ripple: COOLBET_OWN_BETTING (line-shop stop), OWN_STRATEGY_AUDIT §7, WORKFLOWS Mac table (placer/router unloaded, collector added)

## Phase 6 — /admin/shadow-bots rework (parallel with 1a, after Phase 0)
- [x] engine view `shadow_bets_own_book_clv` (margin-corrected per row, written at settle + backfill) + smoke
- [x] `lib/shadow-bots/queries.ts` (8 cached + 4 live queries; was ~20 uncached)
- [x] `lib/shadow-bots/verdict.ts` + `verdict.selfcheck.ts` (30 assertions)
- [x] safety strip incl. `CoolbetDaemonsPause`, `real_money_armed`, CAN_STAKE
- [x] picks table + row (Age, Break-even, Gate, Live edge, Verdict, Place/Skip)
- [x] `place-action` → `/api/admin/real-bet` with `shadowBetId`, `placed_real = NULL`
- [x] scoreboard from `bots WHERE retired_at IS NULL`; t-stat pill deleted; prereg verdict
- [x] deletions: ForwardTestPanel + query, SHADOW_BOTS, BOT_BADGES, FAMILIES, discipline strip, Kambi tooltip (page 2264 → 81 lines)
- [ ] fix SYSTEM_MAP §2 (5 retired bots listed); extend drift test map→registry
- [x] smokes: `SHADOW-BOTS-REGISTRY-DRIVEN`, `SHADOW-BOTS-VERDICT-IS-PREREG`, `SHADOW-BOTS-PLACE-WRITES-REAL-BETS`

## Live state 2026-09-15 evening
- `com.oddsintel.inplay-collector` LOADED and running on the Mac (15 live fixtures on cycle 1, rows in `inplay_book_quotes`, heartbeat `pipeline_health_state.inplay_collector`).
- `com.oddsintel.coolbet-ui-placer` and `com.oddsintel.best-price-router` UNLOADED (`~/Library/LaunchAgents/paused/`).
- `real_money_armed = FALSE`, `placement_paused = TRUE`, both placer toggles OFF → `CAN_STAKE: no`.
