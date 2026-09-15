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
- [ ] docs: SYSTEM_MAP §4, COOLBET_OWN_BETTING, RELIABILITY_LEDGER, PRIORITY_QUEUE
- [x] ⚖️ 0.C owner: bootout both `--execute` plists; move to `local/launchd/paused/`; fix header comment

## Phase 1a — sharp-tight freshness
- [ ] mig 355 `odds_snapshots.first_seen_at`, `last_seen_at` (+ `shadow_bets.decision_quote_age_min`)
- [ ] dedup-on-change in `coolbet_explorer`, `epicbet_explorer`, `unibet_odds_feed`
- [ ] retention exemption for the three own books, 60 days; cost check recorded
- [ ] instrument refuses legs > 60 min old; pre-registration amended and dated
- [ ] `scripts/sharp_tight_slope.py` + first output committed
- [ ] smoke: `OWN-BOOK-DEDUP-ON-CHANGE`, `OWN-BOOK-RETENTION-EXEMPT`, `SHARP-TIGHT-FRESHNESS-REFUSES-STALE`
- [ ] stop/promote rule recorded in PRIORITY_QUEUE row

## Phase 1b — in-play slow-state rig (⚖️ go/no-go first)
- [ ] read `oufid.jsonl`; record result in inplay discovery context
- [ ] collector generalised to `--book coolbet|epicbet`; Mac launchd `KeepAlive`; heartbeat
- [ ] mig 356 `inplay_book_quotes` + prune job (90 d)
- [ ] `bot_inplay_slowstate_v1` (T1, T2 locked) + `af_control` arm; registry + SYSTEM_MAP
- [ ] `scripts/inplay_slowstate_eval.py` (hit-rate lift, cluster-robust, power line)
- [ ] smoke: `INPLAY-COLLECTOR-HEARTBEAT`, `INPLAY-SLOWSTATE-TRIGGERS-LOCKED`, `INPLAY-SLOWSTATE-PRICE-IS-BOOK-NOT-AF`
- [ ] stop rules (n=1,000 / n=3,000) recorded in PRIORITY_QUEUE row

## Phase 2 — promotions (⚖️ accounts + T&Cs from owner)
- [ ] mig 357 `promo_terms`, `promo_ledger`
- [ ] `scripts/promo_ev.py` (boost / free-bet SNR / acca insurance, under terms)
- [ ] `scripts/promo_review.py` monthly
- [ ] smoke: `PROMO-EV-FORMULAS`, `PROMO-LEDGER-EV-BEFORE-BET`

## Phase 3 — policy
- [ ] log `max_accepted_stake` in `coolbet_placement_attempts`; smoke `PLACEMENT-LOGS-MAX-STAKE`

## Phase 5 — cull
- [ ] delete `inplay_bot.py`, `coolbet_inplay.py`, `place_all_inplay_bets` (after 1b)
- [ ] drop 4 inactive `BOOK_MARKET_BOTS`; remove `WIDE_CONFIGS`, retired O/U stop, `_ODDS_TOLERANCE`
- [ ] wire or delete `PIN_CROSS_DRIFT_VETO_ENABLED`
- [ ] web: Stripe/tier dead code + deps; README
- [ ] close CS2 + tennis queue rows with reasons
- [ ] grep-ripple docs for every removal

## Phase 6 — /admin/shadow-bots rework (parallel with 1a, after Phase 0)
- [ ] engine view `shadow_bets_own_book_clv` (margin-corrected per row) + smoke
- [ ] `lib/shadow-bots/queries.ts` (typed, cached 60 s; no full-ledger fetch)
- [ ] `lib/shadow-bots/verdict.ts` (+ tests): live edge, break-even, PLACE/THIN/SKIP/BLOCKED, prereg bot verdict
- [ ] safety strip incl. orphaned `CoolbetDaemonsPause`, `CAN_STAKE`
- [ ] picks table + row (Age, Break-even, Gate, Live edge, Verdict, Place/Skip)
- [ ] `place-action` → `/api/admin/real-bet` (fixes "no path from shadow pick to real_bets")
- [ ] scoreboard from `active_names()`; delete ROI pill
- [ ] deletions: ForwardTestPanel + query, retired SHADOW_BOTS, BOT_BADGES, FAMILIES, discipline strip, Kambi tooltip
- [ ] fix SYSTEM_MAP §2 (5 retired bots listed); extend drift test map→registry
- [ ] smokes: `SHADOW-BOTS-REGISTRY-DRIVEN`, `SHADOW-BOTS-VERDICT-IS-PREREG`, `SHADOW-BOTS-PLACE-WRITES-REAL-BETS`
