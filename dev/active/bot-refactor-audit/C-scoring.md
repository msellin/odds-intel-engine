Parent row: [[#162]] BOT-REFACTOR-CLEANUP-2026-09-25 (PRIORITY_QUEUE.md) — phase 1, auditor C.

# C — Settlement, CLV, ROI & scoring: current truth (2026-09-25)

This audit only reads. It made no code, config, DB or doc change. DB figures come from SELECTs run on
2026-09-25, roughly 11:00–12:30 UTC. Applied migrations go up to **432**. Migration **433 (`433_one_bot_performance.sql`)
and 434 exist uncommitted in the working tree and are NOT applied.** So "today" in this report means
production as it runs right now. "#159 WIP" means the uncommitted change another session is making.
**That session edited `settlement.py` while this audit was running** (the hero block's legacy CLV became
`NULL::numeric` mid-read). Line numbers are for the working copy as it stood at the end of the audit.

Builds on: `dev/active/unified-bot-model-genesis/ledgers.md` (column history, invariants),
`docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md` (reader/writer inventory), handover §3/§4. This report does not
repeat those. It covers what they did not: which number each surface shows today, where each one
disagrees, and what is still left after #159.

---

## 0. Summary

* **For one bot, today's surfaces show up to 6 ROIs and 5 CLVs** (measured on `bot_v10_1x2` in §3).
  They agree on n (397 settled) and on nothing else. ROI ranges +5.0% … +13.4%. CLV ranges −1.6% … +7.0%.
* **#159 (in progress) fixes the core.** It adds one `bot_performance` view that `/performance`,
  `/admin/bots` (via `bot_scoreboard`) and `dashboard_cache.bot_breakdown` all read. It adds a public
  price basis `odds_at_pick_available`, with a producer at pick time and a 30-min backfill. CLV becomes the
  sharp-anchor close for every bot. `bot_weekly`/`bot_market_stats`/`bot_config.admissible_metric` move to anchor CLV.
  Forward-test rows follow the #158 record. Nothing in §8 duplicates that.
* **What #159 does NOT reach (this audit's main output):**
  1. **The public `/picks` forward-test panel still shows own-book `clv_margin_corrected`** (−2.6% for the
     sharp arm) while `/performance` shows sharp-anchor CLV (+2.4%) for the same bot. This breaks §3.5 on a
     public page that is not in #159's file list (`picks-forward-test-panel.tsx`, `picks/page.tsx`).
  2. **The /admin/shadow-bots Pick queue (real-money-facing manual Place)** scores bots from a *fourth*
     source, `shadow_bot_scoreboard` (mig 360). It reads the shadow COPIES of sim bots and ranks by mc-CLV:
     bot_v10_1x2 there = n 355, ROI +5.0%, CLV −1.6%.
  3. **Five separate verdict engines, with five thresholds.** The §3.3 retirement flag (n ≥ 50, sharp-anchor CI < 0)
     is implemented in none of them.
  4. **A settlement bug: 90 of 93 CLV-AUTOVOIDed sim bets have been RESURRECTED to won/lost** by
     `resettle_wrongly_voided_bets` (61 won, +€591 net; 10 of them on the CALIBRATED `bot_v10_1x2`, +€45.7).
     Cause: autovoid never sets `void_reason`.
  5. **Consensus close is structurally missing for sim/shadow history.** `clv_sharp.py` only computes
     consensus for legs ≤ 7 days old. So "Pinnacle else consensus" is Pinnacle-only for the model bots:
     bot_v10_1x2 has an anchor CLV on 170 of 397 legs (43%).
  6. **Three paper bots settle their own `shadow_bets` rows** in parallel with the generic settler. They
     agree on the grade but skip CLV. The corners bot has 0 closing prices on 944 settled rows.
  7. **219 shadow zombies sit on postponed matches.** `scripts/match_status_sweeper.py` is not scheduled.
     They inflate the #157 "pending" counts.
  8. The `real_bets` CLV (OWN money) still uses the no-age-limit Pinnacle close. It is not in `leg_clv_sharp`.

---

## 1. What changed since the 2026-09-24 docs (my area)

| When | Change | Effect on scoring |
|---|---|---|
| 09-24 `2cf14433`, `04fff33f` | sim settlement writes `clv` again (NULL since 09-14) and `clv_pinnacle_devig` (NULL since 09-05) | Legacy columns became whole again. That is why `bot_breakdown.avg_clv` reads +7.0%. |
| 09-24 mig 422 (`25f61f79`) | shadow `clv_pinnacle_live` settle fix, 20,090 rows | — |
| 09-25 mig 430 (#156) | `picks_forward_test_anchor_clv`. Forward-test stop rule amended to sharp-anchor vs junk control | `/performance` forward-test rows lead with anchor CLV. `/picks` panel does NOT (§6 P1) |
| 09-25 mig 431 (#158) | `pick_rule_recheck` table + `picks_forward_test_arm_rule` / `_record_leg` / `_bot_record` views | `/performance` bot record = native + rechecked_pass. `bot_scoreboard` still counts native only (consensus C: 4 vs 35 settled) |
| 09-25 mig 432 (#155 part) | `show_on_picks` for newplus twin + high_roi_v2 | distribution, not scoring |
| 09-25 **uncommitted** mig 433 + `workers/utils/pick_price.py` + `scripts/backfill_odds_at_pick_live.py` + `supabase_client.store_bet` + `settlement.write_dashboard_cache` + web `bot-performance.ts`, `engine-data.ts`, `performance/*`, `admin/bots/*`, `api/v1/track-record` | #159 | See §0. Replaces bot_scoreboard/bot_weekly definitions, adds bot_performance, bot_ledger appended columns. |
| 09-25 **uncommitted** mig 434 | #161 twin arms | new arms are recorded-only. 433's `bot_ledger` forward-test branch filters `arm IN (live, consensus_anchor, junk_anchor)`, so twins are out of every bot record (correct for EXPERIMENTAL) |

---

## 2. Inventory

### 2a. Settlement writers (grade / void / pnl)

| # | Path | file:line | Ledger | Writes | Trigger | Notes |
|---|---|---|---|---|---|---|
| S1 | `settle_bet_result` (resolver registry) | settlement.py:574 | shared | returns result/pnl/clv | called by S2–S6, S11 | pnl = `(odds_at_pick−1)·stake` (:610). The high-water price enters stored pnl here |
| S2 | `_settle_pending_bets` | settlement.py:3797 (UPDATE :3970) | simulated_bets | result, pnl, bankroll_after, closing_odds, clv, clv_pinnacle, clv_pinnacle_live, clv_pinnacle_devig, closing_bookmaker | run_settlement 21:00/23:30/01:00, settle_ready */15 | also `UPDATE bots.current_bankroll` :4014 |
| S3 | `_settle_pending_shadow_bets` | settlement.py:4027 (UPDATE :4186) | shadow_bets | result, pnl, closing_odds, clv, clv_pinnacle, clv_live, clv_pinnacle_live, margin fields | same | excludes `corners_ou_%` (:113) |
| S4 | `_settle_real_bets_for_matches` / `_settle_real_combo_bets` | :1591 / :2026 | real_bets | result, pnl, clv (own book ≤ 60 min), clv_pinnacle (no age limit) via `real_bet_closing` :1548 | same | half_won/half_lost vocabulary |
| S5 | `settle_picks_forward_test` | :1820 | picks_forward_test | outcome (push not void), pnl (flat 1u at published odds), clv (raw own-book), clv_margin_corrected | same | |
| S6 | `settle_picks_board` | :1961 | picks_board | outcome | same | watchlist; graded (1,290 rows) but counted nowhere (§6 P6) |
| S7 | `fix_stale_live_matches` | :2095 (void :2214/:2223) | sim + shadow | void, void_reason='postponed' ONLY for matches it transitions | settle_ready | **ABD/WO with no score → finished 0-0 and graded** (:2193). Books normally void an abandoned match |
| S8 | `_void_real_bets_on_dead_matches` | :1662 | real_bets | void, notes | run_settlement | status IN (postponed, cancelled) |
| S9 | `_void_forward_test_on_dead_matches` | :1791 | forward test | void | run_settlement | same predicate |
| S10 | `_void_board_on_dead_matches` | :1942 | picks_board | void | run_settlement | same predicate |
| S11 | `resettle_wrongly_voided_bets` | :2458 (SQL :2344) | sim + shadow | re-grades voids on finished matches, clears void_reason, bankroll delta | settle_ready :2330 | skips only `void_reason LIKE 'quarantine%'` |
| S12 | `void_ungradeable_1h_bets` | :2404 | sim + shadow | void no_ht_score | settle_ready :2339 | |
| S13 | `_apply_clv_autovoid` | :4769 (call :2829) | **simulated_bets only** | void, pnl 0, `reasoning += 'CLV-AUTOVOID'`, **no void_reason** | run_settlement | ratio odds_at_pick/closing ≥ 1.65 |
| S14 | `corners_paper_bot.settle_picks` | corners_paper_bot.py:261 (void), :300 | shadow (corners bot) | result, pnl (flat €10) | scheduler.py:4058 hourly :50 | the only settler for corners |
| S15 | `team_total_paper_bot.settle_picks` | team_total_paper_bot.py:175 (:208) | shadow (TT bot) | result, pnl | scheduler.py:4062 :55 | **duplicate** of generic `_r_team_total` (:426), "belt-and-braces" |
| S16 | `first_half_1x2_paper_bot.settle_picks` | first_half_1x2_paper_bot.py:230 (:250) | shadow (1H bot) | result, pnl | scheduler.py:4070 :57 | **duplicate** of generic `_r_1x2_1h` (:293) |
| S17 | `results_check` un-settle | results_check.py:128 | forward test | resets outcome to NULL on a score correction | */2 h :40 | #121 tie-break |
| S18 | `scripts/match_status_sweeper.py` | :97/:103 | sim + shadow | void postponed | **NOT scheduled** (no add_job) | the only path that voids zombies on already-postponed matches |
| — | many `scripts/backfill_*`, `cleanup_*`, `resettle_*` | — | various | one-off | manual | out of scope; listed in 5a inventory |

### 2b. CLV producers (every definition that lands in a column or view)

| # | Quantity | Where computed | Price | Close | Age limit | De-vig | Stored in |
|---|---|---|---|---|---|---|---|
| C1 | own-book raw CLV | S2 via `get_closing_odds(…, own_book)` :802 | odds_at_pick | pick's own book, last pre-KO | **none** | none | simulated_bets.clv (redefined in place 09-21) |
| C2 | own-book raw CLV | S3 | odds_at_pick | own book | none | none | shadow_bets.clv |
| C3 | own-book margin-corrected | S3 / S5 via `closing_book_margin` :1759 | odds_at_pick / published | own book | none | per-row margin | shadow/fwd `clv_margin_corrected` |
| C4 | Pinnacle de-vig CLV | S2/S3/S4 via `get_devigged_pinnacle_close_prob` :904 | odds_at_pick | Pinnacle latest pre-KO, **each side fetched separately** | **none** | Shin (`workers/model/devig.py:194`) | sim `clv_pinnacle` + `clv_pinnacle_devig` (same value since 09-07; historic `clv_pinnacle` rows RAW), shadow `clv_pinnacle`, real `clv_pinnacle` |
| C5 | same at executable price | S2/S3 | odds_at_pick_live | as C4 | none | Shin | `clv_pinnacle_live`, `clv_live` |
| C6 | **sharp-anchor CLV** | `workers/jobs/clv_sharp.py` (`job_clv_sharp` 01:40 daily) | `COALESCE(odds_at_pick_live, odds_at_pick)` (basis stored) / published | Pinnacle complete set, sides within ±2 min, ≤ 60 min pre-KO (:54-55) | **60 min** | Shin | `leg_clv_sharp.clv_sharp`, `p_close` |
| C7 | consensus-anchor CLV | clv_sharp.py via `workers/utils/anchor.py` | same | ≥ 5 books excl. Pinnacle & own book | 60 min | per anchor.py | `leg_clv_sharp.clv_cons`. **Only legs ≤ 7 days old** (`cons_days=7`, :150). See §6 P5 |
| C8 | thin consensus | same | same | 3–4 books | 60 min | — | `clv_cons_thin` (measurement only) |
| C9 | view-level anchor CLV | mig 430 `picks_forward_test_anchor_clv`, `picks_forward_test_bot_record` (431), checkpoint script `scripts/picks_forward_test_checkpoint.py:75,171` | published | C6 else C7 | — | — | views |
| C10 | #159 WIP `clv_anchor_public` / `clv_anchor_own` | mig 433 `bot_ledger` | **recomputed** `odds_public × p_close − 1` and `odds_own × p_close − 1` | C6 else C7 | — | — | view columns. Note: `clv_anchor_own` ≈ `leg_clv_sharp.clv_sharp`, same price, only rounding differs. `clv_anchor_public` is new (higher price, so higher CLV) |
| C11 | pseudo-CLV (open vs close) | supabase_client.py:1352, settlement.py `_compute_pseudo_clv_batched` :2960 | opening odds | closing | — | none | matches.pseudo_clv_* (model feature, not a bot score) |

### 2c. ROI / P&L producers

| # | Where | Stake | Price | Population | Shown on |
|---|---|---|---|---|---|
| R1 | stored `pnl` (S1) | Kelly € (sim), €10 (shadow), 1u (fwd) | odds_at_pick (high-water pre-09-02) | per leg | many readers (below) |
| R2 | `_EXEC_PNL` SQL | settlement.py:3259 | **staked** | COALESCE(live, at_pick) | sim | dashboard_cache hero `roi_pct`/`active_roi_pct`, `pro/elite_value_bets_30d`, (today) `bot_breakdown` |
| R3 | `execPnl` TS | odds-intel-web `engine-data.ts:190` | staked | same | sim | /performance Pro leaderboard (`buildPublicBotStats`), getPlaceableBets bot ROI (:952, per-bet mean of pnl/stake, a THIRD aggregation) |
| R4 | `getCalibratedHeadlineStats` | engine-data.ts (HEAD :1559) | **flat €10** | odds_at_pick_live; rows with no live price DROPPED | sim, headline cohort | /performance hero |
| R5 | `bot_scoreboard.roi_unit` (mig 410) | flat 1u | **odds_at_pick (recorded)** | bot_ledger | /admin/bots |
| R6 | `bot_weekly.pnl_unit` (411) | flat 1u | recorded | bot_ledger 12 wk | /admin/bots strip, admin Overview charts |
| R7 | `picks_forward_test_summary*` / `_bot_record` | flat 1u | published | forward test | /picks panel, /performance fwd rows |
| R8 | `shadow_bot_scoreboard` (360) | flat €10 | odds_at_pick_live else at_pick | `shadow_bets_unique` (incl. sim bots' shadow copies) | /admin/shadow-bots Pick queue chip |
| R9 | `getPublicPerformanceExtras.botRecentRoi` | staked | **stored pnl (R1)** | sim 30 d | computed, **never rendered** (dead) |
| R10 | `getModelV2Stats` | staked | stored pnl | sim | passed to `PerformanceHero` as a prop that is **never read** (dead fetch) |
| R11 | `compute_model_evaluations` | settlement.py:4395 | staked | stored pnl | sim | table `model_evaluations`. Readers: only manual scripts (`morning_update.py`, `threshold_check.py`) |
| R12 | `run_report` / `run_post_mortem` | :4704 / :4464 | staked | stored pnl | sim | console / post-mortem |
| R13 | `weekly_bot_review.py` | per-bet | stored pnl | sim + `shadow_bets_unique` | weekly email (PROMOTE/DEMOTE) |
| R14 | `weekly_digest.py:74` | staked | stored pnl | sim | **not scheduled** (def only, scheduler.py:950) |
| R15 | #159 WIP `bot_performance` | flat 1u (+ `roi_staked` secondary) | `odds_at_pick_available` → live → recorded (counted in `n_*_recorded`) | bot_ledger in_record | everything after #159 |

### 2d. Scoring views (current DB)

| View / table | Mig | Reads | Readers | After 433 |
|---|---|---|---|---|
| `bot_ledger` | 410 (+422?) | sim, shadow (DISTINCT ON earliest pick_time, no shadow rows for bots with sim rows), fwd live/consensus/junk | bot_scoreboard, bot_weekly, bot_market_stats, bot_ledger_display, /api/admin/bot-ledger, /api/performance/bot-legs, #157 counts | + public/own price, anchor CLV, in_record |
| `bot_scoreboard` | 410 | bot_ledger | /admin/bots, bot-board.ts, admin-overview | projection of bot_performance |
| `bot_weekly`, `bot_market_stats`, `bot_ledger_display` | 411 | bot_ledger | /admin/bots sheet, fleet strip, Overview | re-created |
| `bot_config` (TABLE) | 410 | written nightly by `scripts/export_bot_config.py` (03:40) | /admin/bots | `admissible_metric` → clv_anchor (script WIP) |
| `bot_capabilities` | 410+ | caps/switches | /admin/bots, export_bot_config | untouched |
| `leg_clv_sharp` (TABLE), `clv_sharp_legs` | 386–394 | three ledgers | 433, 430 views, checkpoint, research scripts | untouched |
| `picks_forward_test_summary`, `_by_market`, `_arm_summary`, `_public` | 342…430 | forward test by rule_version | /picks panel, stop-rule machinery | untouched |
| `picks_forward_test_anchor_clv` | 430 | + leg_clv_sharp | engine-data (/performance fwd CLV) | untouched |
| `pick_rule_recheck` (TABLE), `picks_forward_test_arm_rule`, `_record_leg`, `_bot_record` | 431 | writer `scripts/recheck_forward_test_picks.py` | /performance fwd rows; 433 in_record | untouched |
| `shadow_bot_scoreboard` | 360 | shadow_bets_unique | **Pick queue** (`shadow-bots/queries.ts:407`), `scripts/admin_fixtures/queue.py`, `void_phantom_sharp_picks.py` | **untouched by #159** |
| `shadow_bets_own_book_clv` | 355 | shadow | `pick_trigger_matcher.py` (gate input), shadow-bots verdict.ts | untouched |
| `shadow_bets_unique` | 101+ | shadow | weekly_bot_review, shadow_bot_scoreboard | untouched |
| `dashboard_cache` (TABLE) | 157… | written by `write_dashboard_cache` every :15/:45 + end of settlement | /performance (anon rows), track-record API (HEAD) | bot_breakdown from bot_performance. **11,012 rows, 133 MB, never pruned** |

### 2e. Verdict / maturity / flag logic

| # | Where | Metric | Threshold | Output | Consumer |
|---|---|---|---|---|---|
| V1 | `verdictOf` — web `admin/bots/bot-board-model.ts:105` | HEAD: `bot_config.admissible_metric` (clv_mc for fwd/sharp/shadow, clv_pinnacle for model_sim). WIP: clv_anchor | n ≥ 30 (`MIN_N`), t ≥ 2 beats, t ≤ −2 loses | beats/loses/inconclusive/early/noclv | /admin/bots |
| V2 | `botVerdict` / `botTrack` — web `lib/shadow-bots/verdict.ts:298/409` | clv_margin_corrected (own book) from shadow_bot_scoreboard | PREREG_MIN_N 300, retire mean < −2%; LEAD/CANDIDATE at n ≥ 30 & mean > 0 | per-pick "lead bot / losing bot" chip | **Pick queue (real-money manual Place)** |
| V3 | `scripts/weekly_bot_review.py` (weekly job, scheduler.py:1346) | `COALESCE(clv_pinnacle_live, clv_live, clv_pinnacle, clv)` (:251) — **four definitions coalesced**; else per-bet ROI | n ≥ 100 CLV / 200 ROI, t ≥ +1.65 PROMOTE, ≤ −1.65 DEMOTE, real-money tripwire | email | owner (promotion is a hand migration) |
| V4 | `scripts/picks_forward_test_checkpoint.py:144/156` | anchor CLV Δ vs junk control, stratified bootstrap | pre-registered checkpoints 200/400/800 | STOP/CONTINUE text | manual (not scheduled) |
| V5 | `job_trigger_calibrator_watch` scheduler.py:2441 | Pinnacle CLV on trigger bots | own gate | Telegram page | owner |
| V6 | **§3.3 retirement flag** (n ≥ 50 settled, sharp-anchor CLV CI entirely < 0 → "review this bot") | — | — | **not implemented anywhere** (grep: no hit in admin-attention.ts, bot-board, health_alerts) | owed by #155 |
| V7 | `health_alerts.check_stale_retirement_flags` :648 | retired_reason set but not retired | — | email | owner (unrelated "flag" meaning, naming trap) |
| M1 | `bots.maturity_label` writers | **hand-written migrations only** (375, 402, 413–434) | — | status | pick_generator.py:405, coolbet_placer.py:585/727/2686, coolbet_signaler.py:142 (calibrated gate), coolbet_prekickoff_alert.py:155, settlement.py:3492 & 3330, engine-data HEADLINE_MATURITY_LABELS, bot-aggregates PUBLIC_MATURITY_LABELS |

### 2f. Surfaces → source (production HEAD, then #159 WIP)

| Surface | n | ROI | CLV | After #159 WIP |
|---|---|---|---|---|
| /performance leaderboard, anon/free | bot_breakdown.settled | bot_breakdown.roi_pct = R2 staked, our books | bot_breakdown.avg_clv = C1 legacy | bot_performance roi_public / clv_public |
| /performance leaderboard, Pro | getAllBets | R3 staked, our books | C1 (Elite only) | same bot_performance (via `bot-performance.ts`) |
| /performance hero | R4 | R4 flat, our books, unpriceable dropped | median C1, median sim `clv_pinnacle` (raw on old rows) | `getHeadlineFlat` (bot_ledger pnl_unit_public) |
| /performance fwd rows | `_bot_record` | R7 | C9 anchor | bot_performance (in_record) |
| /performance 30/90 d curve | dashboard_cache.daily_pnl_curve_* | R2 staked | — | flat €10 public (WIP diff) |
| **/picks forward-test panel** | picks_forward_test_summary | R7 | **C3 clv_margin_corrected + CI** | **unchanged** (§6 P1) |
| **/picks per-pick row** | — | — | **C3/raw `clv`** (`picks/page.tsx:285`) | **unchanged** |
| /admin/bots | bot_scoreboard | R5 flat **recorded** | V1 metric: clv_mc or clv_pin (C4 no age limit) | roi_own + roi_public, clv_anchor_own |
| **/admin/shadow-bots Pick queue** | shadow_bot_scoreboard | R8 | C3 | **unchanged** (§6 P2) |
| /admin/real-bets | real_bets | staked € (right for money) | C4 no age limit + own ≤ 60 min | unchanged (§6 P7) |
| /api/v1/track-record | own query | flat (HEAD) | C1 / sim clv_pinnacle | WIP rewrite in progress |
| Telegram/email footer `get_elite_30d_clv` | dashboard_cache.elite_value_bets_30d | — | C1 legacy (becomes NULL in the WIP) | callers: `inplay_bot.py:438`, `email_digest.py`. Both effectively dead (§7) |

---

## 3. Measured disagreement on real bots (SELECTs, 2026-09-25)

### bot_v10_1x2 (CALIBRATED, headline bot), 397 settled singles

| Figure | Value | Source / surface |
|---|---|---|
| ROI flat @ recorded `odds_at_pick` | **+13.38%** | bot_scoreboard.roi_unit → /admin/bots |
| ROI flat @ our books (`odds_at_pick_live`, 393/397 priced) | **+8.06%** | = #159 roi_own (and roi_public until `odds_at_pick_available` is back-filled) |
| ROI staked @ our books | **+5.20%** | dashboard_cache.bot_breakdown → /performance anon; execPnl → /performance Pro |
| ROI staked @ stored pnl | +10.37% | R9–R13 (reports, model_evaluations) |
| ROI flat €10 on shadow copies | +5.02% (n 355) | shadow_bot_scoreboard → Pick queue |
| CLV legacy own-book `clv` | **+7.03%** (n 350) | bot_breakdown.avg_clv → /performance (Elite) |
| CLV Pinnacle de-vig, no age limit | **+0.81%** (n 369, bot_scoreboard) / +1.13% (n 370 unfiltered) | /admin/bots headline today. ⚠️ The handover calls this "fresh close". It is NOT fresh |
| CLV raw sim `clv_pinnacle` | +5.29% | hero `medianClvPinPct` input |
| CLV **sharp-anchor, fresh Pinnacle ≤ 60 min** | **+3.03% (n 170 of 397; 0 consensus)** | leg_clv_sharp → #159's number |
| CLV own-book margin-corrected on shadow copies | −1.57% (n 308) | Pick queue chip |

### bot_high_roi_global_v2 (BETA), 52 settled

ROI: flat recorded +21.25% (/admin/bots) · flat ours +17.29% · staked ours +21.47% (/performance) · stored
staked +25.17% · shadow copies +10.19% (n 36). CLV: legacy +6.94% (/performance) · Pinnacle no-age +0.88%
(/admin/bots) · **anchor −0.09% (n 23)** · Pick queue mc −1.60%. So `/performance` shows +6.9% CLV for a bot
whose honest CLV is about 0.

### bot_consensus_c_v1 (TESTING, forward test)

| Surface | n settled | ROI | CLV |
|---|---|---|---|
| /admin/bots (bot_scoreboard: current rule_version native only) | **4** | **+29.25%** | mc −14.17% |
| /performance (`picks_forward_test_bot_record`, #158 native + rechecked_pass) | **35** | ≈ −14.6% | **anchor −0.05% (n 34)** |
| /picks panel (summary, by rule_version) | v1 34 / v2 4 | −18.5% / +29.3% | mc −5.8% / −14.2% |

### bot_sharp_1x2_v1 (TESTING)

/performance anchor +2.38% (n 64). /admin/bots mc −2.73% (V1 verdict metric in HEAD). **/picks panel (arm `live`
v4, 1x2 + O/U pooled, n 88) mc −2.61%, ROI +3.91%.** The pre-registered arm is split into two bots by market
(bot_sharp_1x2_v1 / bot_sharp_ou_v1), but `/picks` shows the pooled arm. That is a legitimate but
unlabelled difference in population.

### VIP bot_combined_1x2_ev5_v1 (maturity **experimental** in DB; handover wants "VIP · TESTING")
2 settled: bot_breakdown ROI **−53.8% staked**, CLV legacy **+18.9%**. bot_scoreboard flat −3.0%, Pinnacle +8.8%.
Tiny n, but the staked-vs-flat gap shows how far the stake basis alone moves a number.

---

## 4. Per-number map: every place each figure is computed

| Number | Computation sites | Agree? |
|---|---|---|
| **ROI** | R2 settlement.py:3259 (×5 uses: hero :3333, active :3343, 30d cohort :3369 dead, `_value_bets_cohort` :3467, `_value_bets_cumulative` :3525 dead), R3 engine-data.ts:190 (+ :952 mean-of-ratios variant), R4 hero flat, R5 bot_scoreboard, R6 bot_weekly, R7 fwd summary views (×4), R8 shadow_bot_scoreboard, R9–R14, R15 WIP | No. Three stake bases (Kelly-staked, flat, mean-of-ratios) × three price bases (recorded, our-books, available) × three populations (sim, shadow copies, #158 record) |
| **CLV** | C1–C10 | No. 5 values for one bot (§3) |
| **P/L €** | stored pnl; R2 × stake; flat €10 × units (R4, WIP bot_breakdown.total_pnl); real_bets pnl | No. bot_breakdown.total_pnl today = staked € at our books (+€144.47 for v10) |
| **n settled** | every view counts `result IN (won,lost)`. dashboard_cache `total_bets` = `result != 'void'` (includes pending); `_value_bets_cumulative` includes push; bot_scoreboard fwd = native only; `_bot_record` = native + rechecked_pass | For sim bots yes. For forward-test bots no (4 vs 35) |
| **n picks (totals, #157)** | bot_ledger (16,761 rows today: sim 4,762 / shadow 11,193 / fwd 806), dashboard_cache total_bets (4,379, sim only), raw shadow_bets 168k | Differ by design. The 219 postponed zombies (§6 P8) sit in the "pending" part |
| **hit rate** | dashboard_cache.hit_rate (sim, non-experimental, **incl. retired**, 41.6%, no web reader), bot_breakdown won/settled, admin views won/settled, `_value_bets_cohort.win_rate_pct` | Row-level yes; the headline figure uses a different cohort |
| **verdict** | V1–V6 | No. Thresholds n 30/50/100/200/300, t 1.65/2, CI; metrics mc / Pinnacle-no-age / coalesced four / anchor |
| **data-fault guard** | S13 autovoid at odds/close ≥ 1.65 (sim only, changes ROI); view guard \|clv\| > 1 (excludes CLV only); clv_sharp none; pseudo-CLV \|x\| ≤ 0.5 | No. Different thresholds and effects; shadow and forward test have no autovoid |
| **de-vig of the close** | `devig.py` Shin (C4, C6); consensus via `anchor.py` (C7); `ou_sharp_outlier.power_devig`, `combined_ou.power_devig`, `corners/team_total _devig_two_way` (pick-side, not scoring) | Scoring side agrees (Shin). Pick-side copies belong to auditor B/prices |

---

## 5. Duplication (the core of the refactor)

| ID | Same thing | Copies (file:line) | Agree? | Covered by #159? |
|---|---|---|---|---|
| D1 | per-bot ROI | R2, R3, R5, R8, R15 (+ dead R9/R10) | no | **yes** for R2 (bot_breakdown)/R3/R5. **no** for R8 (Pick queue) and the hero aggregates R2 (:3333/:3343, still staked `_EXEC_PNL` in WIP) |
| D2 | per-bot CLV | C1 via bot_breakdown, C4 via bot_scoreboard, C3 via shadow_bot_scoreboard + /picks panel, C9/C10 anchor | no | yes for /performance + /admin/bots; **no** for /picks and Pick queue |
| D3 | executable-price SQL vs TS | `_EXEC_PNL` settlement.py:3259 ≡ `execPnl` engine-data.ts:190, kept in step by smoke `PERF-ONE-PRICE-BASIS` | yes (by smoke) | superseded; both become dead once hero + Pro path read bot_performance |
| D4 | sharp-anchor CASE (`status='ok' → p_close / clv_sharp, else cons_status='ok' → cons`) | mig 430 `picks_forward_test_anchor_clv`, mig 431 `_bot_record`, `picks_forward_test_checkpoint.py:75` and `:171`, mig 433 `bot_ledger` ×3 branches | yes today | **433 adds three more copies**. Candidate K3: one SQL function / column |
| D5 | "forward-test arm → bot name" CASE | `clv_sharp_legs` view, mig 410/433 `bot_ledger`, web engine-data fwd mapping, bot_registry | yes (verified on the 5 names) | no — K4 |
| D6 | grading | generic registry `_r_team_total` / `_r_1x2_1h` vs `team_total_paper_bot.settle_picks` / `first_half_1x2_paper_bot.settle_picks` | grade yes. pnl uses STAKE_EUR × odds_at_pick = same. **CLV no** (self-settled rows get none) | no — K5 |
| D7 | void-on-dead-match | S7 (sim + shadow, on transition only), S8, S9, S10, S18 (unscheduled) | predicates agree (postponed, cancelled). **Coverage differs**: sim/shadow have no standing sweep | no — K6 |
| D8 | shadow dedupe | `shadow_bets_unique` view, `bot_ledger` inline DISTINCT ON (same key/order) | key yes. **Population no**: bot_ledger drops shadow copies of sim bots (13,052 rows); shadow_bot_scoreboard / weekly_bot_review keep them | no — K2 |
| D9 | verdict | V1–V5 (+ V6 unbuilt) | no | partly (V1 metric → anchor). V2/V3/V6 not |
| D10 | status → distribution | `bots.maturity_label` + `show_on_performance` + `show_on_picks` + `vip` + `hide_pending` + `is_active` + `retired_at` + `bot_config.published/telegram` | today: 2 RETIRED bots with `show_on_picks = true` (`bot_sharp_forward_test_v1`, `bot_v10_ou`), 1 retired with `show_on_performance` (`bot_high_roi_global_v2_newplus_v1`); VIP bots are `experimental` | #155 (not mine; noted for auditor B) |
| D11 | Pinnacle close lookup | `get_devigged_pinnacle_close_prob` :904 (no age, per-side), `get_pinnacle_closing_odds` :1015 (raw), clv_sharp `assemble_close` (fresh, full set) | no — the first two have no age limit | #159 deprecates the columns but S2/S3/S4 keep **writing** them |
| D12 | dashboard_cache vs live views | bot_breakdown (materialised every 30 min) vs bot_performance (live) | #159 makes the values equal, but they are two copies with up to 30 min lag | parity smoke planned by #159 (b); K7 |

---

## 6. Drift from policy §3

| ID | Rule | Finding | Evidence |
|---|---|---|---|
| **P1** | §3.5 one shared computation per number | The public **/picks** forward-test panel and per-pick rows show own-book `clv_margin_corrected` / raw `clv`. /performance shows sharp-anchor CLV for the same arms. Gotcha #85: own-book CLV is negative BY CONSTRUCTION for these outlier strategies, so /picks publishes a structurally pessimistic number while its stop rule was moved to anchor (#156) | `odds-intel-web/src/components/picks-forward-test-panel.tsx:76,104,141`, `src/app/picks/page.tsx:285`; sharp arm −2.6% vs +2.4% |
| **P2** | §3.5 + 🤖 OWN | **The Pick queue** (where the owner places real money by hand, memory `project_realmoney_shadow_signals`) labels each pick's bot "lead / losing / unproven" from shadow copies and mc-CLV (V2). /admin/bots will say something else once #159 lands | `lib/shadow-bots/queries.ts:407`, `verdict.ts:298-409` |
| **P3** | §3.3 retirement is a flag at n ≥ 50 & anchor CI < 0 | Not implemented. Three other rules exist that would say "retire" on other metrics (V2 mc < −2% at n 300; V3 DEMOTE t ≤ −1.65 on coalesced CLV; V1 "loses" t ≤ −2 at n 30). **Open detail:** 433 gives two anchor CLVs (`clv_public`, `clv_own`). The rule must name one. They differ for sim bots and are identical for the forward test | grep across both repos |
| **P4** | §3.5 fix the writer | #159's writer fix records `odds_at_pick_live` / `_available` at pick time (`supabase_client.store_bet`, WIP). But **`settle_bet_result` still prices stored `pnl`, `bankroll_after` and `bots.current_bankroll` at `odds_at_pick`** (settlement.py:610, :3970, :4014). Kelly staking still compounds off the high-water bankroll. #159 deliberately leaves this (live staking input, #074) | pick_price.py docstring |
| **P5** | handover §4 "CLV = Pinnacle, else ≥ 5-book consensus" | For sim and shadow legs older than ~7 days there is no consensus close and never will be (retention, §59). The definition silently equals "fresh Pinnacle only" for all model-bot history. bot_v10_1x2: 170/397 legs scored. #159's `clv_n` / `clv_n_consensus` show it, but no surface states "43% coverage" | leg_clv_sharp status table: simulated_bets 1,006 ok / 2,415 no_fresh_close / 17 with consensus |
| **P6** | §3.2 anything sent is counted | `picks_board` watchlist legs are SHOWN publicly on /picks ("worth taking at X or better") and graded (1,290 outcomes), but counted in no record by design (mig 354 header). Owner call: is a watchlist line "sent"? | `supabase/migrations/354_picks_board_watchlist.sql` |
| **P7** | §4 CLV definition, 🤖 OWN | `real_bets.clv_pinnacle` uses the no-age-limit close (C4). real_bets is not a `leg_clv_sharp` ledger. The operator's own money is scored on the definition #159 deprecates: real CLV −4.40% (n 767) no-age vs own-book −0.10% (n 89) | settlement.py:1548-1588; clv_sharp.py `_LEGS_SQL` has 3 ledgers |
| **P8** | §3.2 counting integrity | 219 shadow legs pending on POSTPONED matches (bot_dc_* 206, from 08-23..08-29; plus a few sharp triggers 09-11..09-23) and 28 on "scheduled" matches more than 1 day old. The only sweeper for already-postponed matches is unscheduled | SELECT in §2a S18 |
| **P9** | settlement integrity (feeds every number) | **CLV-AUTOVOID resurrection.** S13 voids with `reasoning` only. S11 re-grades any void whose `void_reason` is not 'quarantine%' — so 90 of 93 autovoids are won/lost again (61 won, net +€591.7). S13 never re-voids them, because its idempotence check skips rows already tagged. Affected: `bot_aggressive` 43 (+€451, retired), **`bot_v10_1x2` 10 (+€45.7, CALIBRATED headline bot)**, `bot_lower_1x2`, `bot_opt_home_lower`, inplay_c/e, … Whether each autovoid was right is a separate question (the 1.65 ratio used the pre-09-14 arbitrary-book close), but today two sweeps undo each other with no decision recorded | `SELECT … WHERE reasoning LIKE '%CLV-AUTOVOID%'` → won 61 / lost 29 / void 3 |
| P10 | settlement rule | ABD/WO with no score is graded as a 0-0 finish (S7 :2193). Books void abandoned matches, so Unders/draws get graded won on a match that did not happen. Frequency not measured | settlement.py:2187-2194 |

---

## 7. Dead / retired / unused (evidence)

| Item | Evidence | Action |
|---|---|---|
| `_cohort_fields` + `cohort_rows` query | comment settlement.py:3395 "unreferenced"; query at :3369 still RUNS each cache write | delete query + fn |
| `_value_bets_cumulative` | defined :3525, **never called** (grep: only the def) | delete |
| `_build_upcoming_model_summary` | :3081, unreferenced (:3457 comment) | delete |
| dashboard_cache fields with no web reader | `market_breakdown`, `model_accuracy_pct`, `prediction_sample_size`, `pseudo_clv_count`, `live_snapshot_matches`, `alignment_settled_count`, `hit_rate`, `total_staked`, `active_total_staked`, `retired_bot_breakdown` (0 `.field` refs in web src); columns `prematch_*`, `inplay_*`, `recent_top_wins`, `upcoming_model_summary`, `elite_value_bets_cumulative` no longer written | drop from payload, then columns |
| `pro_value_bets_30d` / `elite_value_bets_30d` | only reader `telegram.get_elite_30d_clv` → `clv_footer_line`, called from `inplay_bot.py:438` (in-play betting retired 2026-08-21) and `email_digest.py` (jobs retired 2026-09-03, scheduler.py:2300). The daily_pipeline_v2.py:4490 fetch is unused after the operator-alert change | delete the chain (verify inplay_bot alert path is off) |
| dashboard_cache history | 11,012 rows / 133 MB since 2026-05-04; readers take the latest row only | prune to N days |
| `getModelV2Stats` | passed to `PerformanceHero` (`performance-hero.tsx:38`), prop never read | delete fetch (web, #159 file — after #159) |
| `botRecentRoi` in `getPublicPerformanceExtras` | computed, not rendered (`performance-extras.tsx` uses calibration/streaks only) | delete |
| `weekly_digest.py` | `job_weekly_digest` defined scheduler.py:950, no add_job | delete or mark |
| `compute_model_evaluations` → `model_evaluations` | runs nightly (run_settlement), readers only manual scripts | keep or make on-demand; its ROI/CLV are legacy |
| `scripts/match_status_sweeper.py` | not scheduled, but it is the only fix for P8 | schedule it or fold into S7 (K6), don't delete |
| `get_pinnacle_closing_odds` (raw) :1015 | NOT dead — it is the per-side fetch inside `get_devigged_pinnacle_close_prob` (:944, :963); dies with C4 | retire together with C4 (K10) |
| legacy CLV columns `simulated_bets.clv/clv_pinnacle/clv_pinnacle_devig/clv_pinnacle_live`, `shadow_bets.clv_pinnacle*`, `clv_live` | #159 deprecates readers but S2/S3 keep writing. **Live model consumer: `clv_pinnacle_devig` is the meta-model training label** (`scripts/train_b_ml3.py`, `validate_meta_b_ml3.py`, `weekly_meta_validate_email.py`, scheduler.py:1235) | do not stop writing until the meta label moves to leg_clv_sharp (owner / model call) |
| `shadow_bot_scoreboard` (360) | only reader = Pick queue chip + 2 scripts | replace with bot_performance (K2) then drop |
| `bot_config.admissible_metric` | after #159 every non-in-play family = clv_anchor, so the column is a constant | drop after #159 (keep `lift` for in-play as a family rule) |

---

## 8. Refactor candidates

Risk = what can break and who depends on it. Rule = the §3 rule it serves. "Twin" = it touches a live bot's
rules, so it needs a twin plus owner OK. None of these change a bot's pick rule; K1 and K9 change settled
results, which is data, and need an owner OK.

| K | Change | Serves | Risk / dependants | Depends on |
|---|---|---|---|---|
| **K1** | **Fix CLV-AUTOVOID vs resettle.** Give S13 a `void_reason = 'quarantine: clv-autovoid — …'`, then decide per row whether the 93 stay voided (re-run the 1.65 test against the own-book close, since the 09-14 close fix). Add a smoke test that says every void carries a reason, and that S11 never touches a quarantine | §3.5 root cause, record integrity | Changes bot_v10_1x2's settled P/L (10 legs) and retired totals (#157). **Owner OK** (data restatement of a CALIBRATED bot). Bankroll delta via the S11 mechanism in reverse | none; independent of #159 but its numbers move |
| **K2** | Pick queue chip reads `bot_performance` (anchor CLV, same population as /admin/bots). Retire V2 `botVerdict`/`botTrack` and `shadow_bot_scoreboard` | §3.5, 🤖 OWN (manual Place trusts this chip) | Changes which bot reads "lead" on a real-money queue, so the owner needs to see it. `void_phantom_sharp_picks.py` / admin fixture read the old view | **after #159** (bot_performance must exist) |
| **K3** | One `anchor_clv(ledger, leg_id, price)` SQL function (or a stored `clv_anchor_source` + `p_anchor` in leg_clv_sharp) used by 430/431/433 views and the checkpoint script | §3.5 | The pre-registered checkpoint must keep its exact numbers: parity test old vs new on all 753 settled fwd legs. The views are read by /performance | **after #159 + #161** (both touch these views) |
| **K4** | One arm→bot mapping (a table, or `bot_registry` export into `bot_config`) replacing the CASE in `clv_sharp_legs`, `bot_ledger`, engine-data | §3.5 | A new arm (434 twins) today needs 3–4 edits | after #161 |
| **K5** | Delete the self-settlers in `team_total_paper_bot` and `first_half_1x2_paper_bot` (the generic registry grades both). Move corners grading into the registry with a stats hook, so corners get closes/CLV too. Remove the three schedules (4058/4062/4070) | §3.5 one settlement rule | Corners bot has no CLV at all today. Adding it changes nothing shown (corners bots are retired/experimental). Risk: `_r_corners_ou` needs `corners_home/away` passed, and the generic path passes no stats (:460). Must add the stats fetch first | none (bots retired, still writing for recovery evidence) |
| **K6** | One `void_dead_matches()` for all five ledgers (sim, shadow, real, fwd, board): status IN (postponed, cancelled) plus a staleness rule, run each settle_ready. Fold `match_status_sweeper.py` in. Void the 219 zombies. Decide ABD/WO (P10) in the same place | §3.2 counting | Real-money void (real_bets) already works — keep its notes text. Resettle S11 must keep treating 'postponed' as re-gradable | none |
| **K7** | dashboard_cache: prune history, drop dead fields/queries (§7). After #159, decide whether bot_breakdown should exist at all or the anon page should read bot_performance through a server route (the view is private, so the server service client is fine) | §3.5 (one copy, not a materialised second) | anon /performance latency (the reason for the cache, PERF-VPS-2026-07-07) | **after #159** (settlement.py dashboard_cache is in its diff) |
| **K8** | /picks panel + per-pick row: lead with sharp-anchor CLV (the #156 definition) and show own-book mc-CLV as the labelled secondary, like /performance | §3.5, 👥 PICKS | Public copy + methodology page wording. The panel reads `picks_forward_test_summary`, which the stop-rule machinery also reads. Add columns, don't change them (pre-registration) | engine-data `getPicksForwardTestSummary` is a #159 file → **after #159** |
| **K9** | real_bets into `leg_clv_sharp` (4th ledger), and /admin/real-bets shows anchor CLV beside the own-book CLV | §4 definition, 🤖 OWN | `leg_id` mapping; half-win results; AH needs the line (`clv_sharp` marks AH unsupported). Admin-only | none |
| **K10** | Stop writing the legacy CLV columns (C1 on sim is also the published column today). Precondition: move the meta-model label off `clv_pinnacle_devig` | §3.5 deprecate the wrong source | **Model risk**: the meta-model trains on it. Needs a model-side decision and a research note (CLAUDE.md "research before you train") | after #159 readers are gone |
| **K11** | Retirement flag V6: one SQL view `bot_review_flag` over `bot_performance` (n ≥ 50 settled, upper 95% CI of anchor CLV < 0) read by admin-attention.ts and /admin/bots. Delete V2. Rebase V3 (weekly_bot_review) onto the same view, so the email and the page cannot disagree | §3.3 | Owner must pick the basis (clv_public vs clv_own, P3) and whether V3 keeps its PROMOTE branch (#155 promotion criteria) | **after #159 and #155** (admin-attention.ts is a #155 file) |
| **K12** | Hero/headline aggregates (settlement.py :3333/:3343, `_value_bets_cohort`) onto bot_ledger `pnl_unit_public`, the same as the leaderboard. Today (WIP) they are still staked `_EXEC_PNL` while the rows become flat-available | §3.5 | Needs a check that #159 isn't already doing it (it is mid-edit in that block) | **#159's file — hand to #159 as a comment, don't edit** |
| K13 | Name the stake basis in every column (`roi_flat_public`, `roi_flat_own`, `roi_staked_public`) — today `roi_unit` means flat-own after 433 and flat-recorded before it | §3.5 legibility | Readers across both repos (65 admin smoke pins) | after #159 settles |

Suggested order after the in-flight rows close: K1, K6, K5 (settlement integrity, no dependency) → K8, K2,
K7, K12 (after #159) → K3, K4 (after #161) → K11 (after #155) → K9, K10, K13.

---

## 9. Files other sessions are editing (do not touch before their rows close)

| File | Row | My candidates that wait on it |
|---|---|---|
| `workers/jobs/settlement.py` (write_dashboard_cache, hero block — edited during this audit) | #159 | K7, K12. K1/K5/K6 are in the same file but in the settlement functions. **Coordinate a merge window**, as memory `feedback_shared_checkout_commits` warns |
| `supabase/migrations/433_*`, `434_*` (uncommitted) | #159, #161 | K3, K4, K11 build on 433's `bot_performance` |
| `scripts/backfill_odds_at_pick_live.py`, `workers/utils/pick_price.py`, `workers/api_clients/supabase_client.py` (store_bet) | #159 | none |
| `scripts/export_bot_config.py`, `workers/registry/bot_registry.py`, `docs/SYSTEM_MAP.md` | #159/#155/#157 | K4, admissible_metric drop |
| `scripts/publish_picks_forward_test.py`, `scripts/picks_forward_test_checkpoint.py` | #161 | K3 |
| web `engine-data.ts`, `bot-aggregates.ts`, `bot-performance.ts` (new), `performance/*`, `performance-leaderboard.tsx`, `admin/bots/*`, `api/v1/track-record` | #159 | K2 (touches shadow-bots, not in the list, but its data comes from bot_performance), K7, K8 |
| web `admin-attention.ts` | #155 | K11 |

---

## 10. Owner questions this audit raises (none are implementation details)

1. **K1:** the 93 CLV-autovoided sim bets. Re-void all, re-judge each against today's own-book close, or accept
   them as settled? It moves bot_v10_1x2's record (10 legs, +€45.7) and the retired totals.
2. **P3/K11:** the retirement flag. Which anchor CLV, at the public "available" price or at our books? It
   matters only for sim/shadow bots.
3. **P5:** should /performance and /admin/bots state anchor-CLV coverage (for example "CLV on 170 of 397 picks")
   next to the figure? For model bots, consensus can never fill the history.
4. **P6:** does a `picks_board` watchlist line count as "sent" under §3.2?
5. **P10:** abandoned matches with no score. Void (book practice) or 0-0?
6. **K10:** the meta-model label (`clv_pinnacle_devig`, no age limit) outlives the deprecation. Move it to leg_clv_sharp
   (a model change, so it needs the research-first rule) or keep writing the legacy column for it alone?
