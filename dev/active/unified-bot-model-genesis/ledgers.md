Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** (`PRIORITY_QUEUE.md`), phase 5 (DB-level `picks` table).

# Ledger genesis: why each bet/pick table exists, and what a merge must keep

Written 2026-09-24. Read-only research: migrations, git history, `PRIORITY_QUEUE.md`,
`docs/ANALYSIS_GOTCHAS.md`, `docs/RELIABILITY_LEDGER.md`, `dev/archive/`, `dev/active/*preregistration*`,
plus SELECT-only checks against the live DB. Every number marked "DB" was measured on 2026-09-24.
This file covers history and intent. Other agents are inventorying today's readers and writers.

---

## 0. The short version

1. **`simulated_bets` (2026-04-27, migration 001)** was the whole product: one bankroll-aware paper
   ledger per bot, Kelly-staked, publicly readable. Over time it took on four jobs: the public track record,
   the real-money candidate queue, the operator's workflow state, and the model A/B record.
2. **`shadow_bets` (2026-05-13, mig 101)** was built for one experiment: re-run every bot at every
   timing window, so that timing and strategy effects could be separated. That experiment (BET-TIMING-MONITOR
   Phase 3) never ran. The table was repurposed three times: as retired-bot recovery evidence
   (SHADOW-RETIRED-OK, 05-20), as the ledger for bots that must never be public (from 08-18), and as the
   real-money placement source (UI placer). The shadow copies of `simulated_bets` picks are not 1:1.
3. **`picks_forward_test` (2026-09-14, mig 342)** was built to be the thing the other two are not: a
   pre-registered, immutable, flat-stake, model-free ledger that has a negative control. Its header is the
   clearest statement of why it must not be a bot's ledger: *"would silently re-contaminate the track record
   we just finished cleaning"*.
4. The main reason for separate tables was **who can see the rows and who can be misled by them**.
   Table choice encoded capabilities: `simulated_bets` is public (anon SELECT, whole table), `shadow_bets`
   is not public, and the forward test is public only through `arm`-filtered views. A merged table has to
   rebuild these separations explicitly, because they currently come for free from the table choice.
5. The **same column names mean different things across the three tables**: `clv_pinnacle` is raw
   Pinnacle in sim and de-vigged Pinnacle in shadow. `edge_percent` holds a probability difference for most
   bots but expected ROI for 7 shadow bots and for the forward test. `stake` and `pnl` are Kelly euros, flat
   €10, and flat 1 unit respectively. `clv` was redefined in place on 2026-09-21.
6. **The draft `bot_ledger` rule of excluding `^[0-9]{4}$` shadow cohorts would drop 11,447 unique picks
   (DB).** For many bots, those HHMM rows are the bot's only record: the sweep, pin and dc families,
   `bot_coolbet_value_v1`, and retired recovery evidence. The same rule would also double-count 403 picks,
   where named-cohort shadow rows duplicate `simulated_bets` rows. The correct rule already exists as
   gotcha 18: pick **one ledger per bot**.
7. The draft `is_inplay = match_minute_at_pick IS NOT NULL` would misclassify **865 of 1,490** in-play sim
   rows as pre-match (DB). `xg_source` is the complete flag.
8. **None of these ledgers has been append-only in practice**, except the forward test. Past examples:
   rows deleted (Kambi orphans, OU cleanups, 199 combos, `bot_coolbet_trigger_v1`), `bot_id` re-attributed
   (mig 375), `stake`/`pnl` rescaled (107, inplay ×5), `match_id` re-pointed (047), and `clv` rewritten
   (09-21 backfill). The forward test annotates rows but never deletes them. That difference is deliberate and
   must survive.
9. **Pre-registration is not unique to `picks_forward_test`.** Four shadow bots run under locked
   pre-registrations: `bot_trigger_1x2_sharp_tight_v1`, `bot_unified_gate_1x2_paper_v1`, and the two
   sharp-anchor-sweep per-book 1x2 bots. The 09-21 CLV backfill deliberately left 65 rows of the last two
   untouched, to protect a locked population.
10. Nine columns in other tables point at ledger row ids, and several of those references are soft (no FK).
    `picks.id` must reuse the source UUIDs, or placement, Telegram, CLV-sharp and operator-mark lineage all
    break.

---

## 1. Timeline of every table that ever held a bot's bet or pick

| Date | Event | Source |
|---|---|---|
| 2026-04-27 | `bots` + `simulated_bets` + `user_bets` created. Before this, paper picks lived in JSON (`data/daily/bets_2026-04-27.json`, deleted 05-06 `4c155767`) | mig 001, `69ab9176` "Live paper trading pipeline" |
| 2026-04-27 | Dedup key `UNIQUE (bot_id, match_id, market, selection)` | mig 003 |
| 2026-04-29 | `user_picks`: user-made picks, not bots | mig 016 |
| 2026-05-06 | Kambi orphans deleted, which removed every April `simulated_bets` row (earliest surviving pick is 2026-05-01, DB) | mig 047/054 |
| 2026-05-10 | `real_bets`: real money, FK → `simulated_bets` | mig 092 |
| 2026-05-13 | `shadow_bets`: timing experiment | mig 101 |
| 2026-05-17 | `simulated_bets_pre_inplay_normalize_2026_05_17`: backup table of 457 rows before in-play stake ×5 | `scripts/normalize_inplay_stake_to_5.py` |
| 2026-05-17 | Combos written into `simulated_bets` (`combo_legs`, `system_type`) | mig 108/109 |
| 2026-06-05 | `published_picks`: model-accuracy log, explicitly "NOT real-money tracking" | mig 185 |
| 2026-06-08/10 | Per-vertical ledgers `tennis_value_bets`, `lol_bets`, `cs2_bets`, `cs2_simulated_bets`, `cs2_real_bets` | mig 190/194/196/201/233 |
| 2026-08-26 | CS2 vertical dropped (33 tables; dump kept off-repo) | mig 286 |
| 2026-09-07 | `corners_paper_picks` (a private table in the first cut) dropped the same day in favour of `shadow_bets` | mig 308 |
| 2026-09-08 | 199 paper combos deleted from `simulated_bets` (30 real-money combos kept in `real_bets`) | PQ COMBO-OU-LEG-RESETTLE / COMBO-BOTS-RETIRED |
| 2026-09-14 | `picks_forward_test` | mig 342 |
| 2026-09-15 | `picks_board`: watchlist of legs that did *not* qualify. "MUST NEVER BECOME" the ledger | mig 354/355 |
| 2026-09-15 | `promo_ledger`: promo bets with EV computed before the bet. 0 rows | mig 356 |
| 2026-09-16 | `picks_public_all`: the first UNION across ledgers (sim model arm + forward test) | mig 361 |
| 2026-09-23 | `leg_clv_sharp`: one CLV definition across all three ledgers, keyed `(ledger, leg_id)` | mig 386 |
| 2026-09-24 | `bot_ledger` / `bot_scoreboard` views drafted (untracked mig 409) | design doc |

Row counts (DB): `simulated_bets` 4,673 · `shadow_bets` 167,848 (≈14.9k unique picks) ·
`picks_forward_test` 760 · `real_bets` 992 · `published_picks` 50,050 · `picks_board` 1,290 ·
`leg_clv_sharp` 171,357. `simulated_bets` currently has **0 pending rows**, and its last pick was 2026-09-22.

---

## 2. `simulated_bets`

### 2.1 Birth

- **Migration 001**, commit `69ab9176`, 2026-04-27, "Live paper trading pipeline + Supabase integration".
  The commit reported "5 bot users with different strategies running in parallel … First live run: 19
  matches, 12 predicted, 10 bets across 4 bots".
- Why it was created: no DB existed before this. It was the first schema, under the section header
  `PAPER TRADING / BOTS`. Its purpose was to simulate a bankroll per bot. That is why it has `stake`,
  `bankroll_after`, and `bots.starting_bankroll/current_bankroll` (default 10,000), and why `stake > 0` and
  `model_probability` are NOT NULL.
- Result vocabulary: the `bet_result` enum `('won','lost','void','pending')`, shared with `user_bets`.
  There is no `push`. Pushes (AH whole line, DNB draw) are stored as `void`.

### 2.2 Evolution (every schema change, in order)

| Mig | Date | Change | Why (quoted/condensed) |
|---|---|---|---|
| 003 | 04-27 | `UNIQUE (bot_id, match_id, market, selection)` | "re-running the morning pipeline never creates duplicates" |
| 006 | 04-27 | `calibrated_prob`, `odds_at_open`, `odds_drift`, alignment fields, `kelly_fraction`, `model_disagreement`, `news_impact_score`, `lineup_confirmed` | P1–P4 model improvements logged per bet |
| 008 | 04-28 | `af_*_prob`, `af_agrees` | "does AF agreement predict bet outcomes?" |
| 032 | 05-01 | `timing_cohort` CHECK (morning/midday/pre_ko), backfilled 'morning' | BOT-TIMING: which window cohort placed it |
| 037 | 05-04 | RLS on (pro/elite only). Later replaced by a `Public read USING (true)` policy | tier gating |
| 039 | — | `ai_explanation(_at)` | explanation cache |
| 050 | 05-06 | `clv_pinnacle FLOAT` = `odds/pinnacle_close − 1` (**raw, no de-vig**) | PIN-5 "industry standard" |
| 057 | 05-07 | `xg_source` ('live'/'shot_proxy'; NULL = prematch) + backfill | in-play segmentation |
| 079/085 | 05-09/10 | 182 `inplay_e` shot-proxy bets voided. The first attempt used a non-existent enum value `'voided'`; by the time the fix ran, settlement had already graded the rows, so 085 re-voided settled rows | clean baseline for the chart |
| 087 | 05-10 | `model_version` (backfilled `v9a_202425`) | "A/B harness … without it only dates can be compared" |
| 094 | 05-11 | `recommended_bookmaker` | which accessible book had the best price |
| 107 | 05-17 | `bot_aggressive_v2` stakes and pnl ÷10, `bankroll_after` recomputed | a 10k default bankroll would have weighted it 10× in portfolio ROI |
| — | 05-17 | In-play stakes and pnl ×5 (script plus backup table) | in-play weight in the headline |
| 106 | 05-17 | CLV nulled on all historical in-play bets | "CLV is meaningless for inplay" |
| 108/109 | 05-17 | `combo_legs`, `combo_size`, `system_type`. `match_id` = first leg ("kept for the NOT NULL") | acca bots |
| 115 | 05-20 | `trigger_notify_inplay_bet_fired` (AFTER INSERT, NOTIFY when `xg_source` is not null) | Coolbet in-play slippage snapshots |
| 116 | 05-21 | `timing_cohort` CHECK widened to 'all' + HHMM | BOT-COHORTS-ALL had made every `store_bet()` fail silently |
| 121 | 05-22 | `market = LOWER(market)` in both bet tables | case drift |
| 130 | 05-25 | `meta_clv_score` | Stage-3 meta-model score per bet |
| 136 | 05-27 | `admin_offered_at` | first time the bet appeared on /admin/place |
| 148 | 05-29 | `strategy_profile` (also on shadow) | "fat" bots with named profiles |
| 173 | 06-03 | `match_minute_at_pick`, `score_*_at_pick`. **Historical in-play rows left NULL** | UI staleness |
| 175 | 06-03 | `pin_cross_drift_shadow_flag` | veto logged, bet still placed ("shadow mode" as a column) |
| 246/248 | 06-12 | `signaled_at`, `user_placed_at`, `user_skipped_at`, `signal_message_id` | Telegram signaler plus operator ✅/⏭. "Why a column instead of real_bets: the operator places manually" |
| 282 | 08-24 | `void_reason` (free text), backfilled 'quarantine' for every void before 07-01 | a re-settler could otherwise resurrect quarantined fake P&L |
| 283 | 08-26 | `clv_pinnacle_devig`, `closing_bookmaker` | "`simulated_bets.clv_pinnacle` is the RAW variant … this column is the one whose sign is meaningful". `clv` was "deliberately left alone" |
| 291 | 09-02 | `odds_at_pick_live` (additive) | STALE-BEST-ODDS: `odds_at_pick` was a high-water mark. **"`odds_at_pick` and `pnl` are NOT overwritten … The audit trail is the product"** |
| 300 | 09-04 | `clv_live`, `clv_pinnacle_live` | CLV repriced at the live quote. Again new columns, not a rewrite |
| 349 | 09-14 | `recommended_bookmaker` backfilled for single-book bots only | "a backfill of a FACT … must NOT be applied to multi-book bots" |
| 363 | 09-21 | `closing_minutes_before_ko` | freshness of the close (40% of closes are >3h old) |
| — | 09-21 | **`clv` rewritten in place to the own-book close**: 3,123 rows, 2,056 recovered, 1,067 set to NULL, mean +5.63% → +2.46% | DIRECT-BOOK-CLV-SHADOW-BACKFILL (PQ L483) |
| 366 | 09-21 | `settled_at` + BEFORE UPDATE trigger `trg_simulated_bets_settled_at`; history NULL | "four separate UPDATE sites … a trigger catches every writer including future ones" |
| 367 | 09-22 | `edge_percent numeric(5,2)` → `numeric(6,4)`. **History not backfilled** | #031 rounding admitted 114 picks below floor; "rewriting edge_percent would rewrite what each bot is recorded as having cleared" |
| 375 | 09-22 | `bot_v10_all` split: **historical `bot_id` UPDATEd** by market to `bot_v10_1x2`/`bot_v10_ou`, `bankroll_after` rebuilt. `bots.name` not renamed; `display_name` added | CLV +2.50% vs −3.85%: "ONE MARKET CARRYING THE OTHER" |
| — | 09-10 | Vocabulary canonicalised in stored rows (sim 1,421 / shadow 21,478 / real 403 rows) | MARKET-VOCAB-CANONICAL Phase 2 |

Constraints today (DB): PK, the 003 unique key, `odds>0`, `stake>0`, `0≤model_probability≤1`, and the
`timing_cohort` CHECK. Triggers: in-play NOTIFY (115) and `settled_at` (366). Views on top:
`picks_public_all` (model arm, `bots.show_on_picks`), `clv_sharp_legs`, `dashboard_cache` (materialised by
job, not a view). Access: **anon SELECT on the whole table plus `Public read USING (true)`**. Migration 404
kept that grant because `engine-data.ts` reads the table directly.

### 2.3 Semantics then vs now (DB-verified)

| Column | At birth | Now | Evidence |
|---|---|---|---|
| `odds_at_pick` | "best accessible odds" at pick | Before 2026-09-02: max over the fixture's **whole snapshot history**, a price that may never have been on offer at the moment of the pick. After: the live price. Old rows unchanged | mig 291, gotcha "pnl priced at a snapshot nobody could take". Stored pnl −€39.79 vs −€487.15 at the live price |
| `stake` | Kelly × bankroll | Median €5.00 (May–Aug), €9.75 (Sep). Range €1–€17.38. Rescaled in place twice (107, in-play ×5). Kelly-vs-flat restatement **deliberately not applied** (PQ L302) | DB by month |
| `pnl` | `stake·(odds−1)` / `−stake` | Same, at `odds_at_pick`. Exceptions: **182 voided rows still carry non-zero pnl (sum −8.49)** from 079/085. 41 in-play wins differ by ≤0.8% (rounding) | DB: void+`quarantine` pnl −8.49 |
| `result` | won/lost/void/pending | Same enum. `void` covers push, postponement, and quarantine; only `void_reason` tells them apart (376 'quarantine', 4 '#002 wrong-fixture', 3 'postponed') | DB |
| `clv` | vs **any** book's close (arbitrary tie-break) | vs the **pick's own book** close (`closing_bookmaker = recommended_bookmaker` holds on 100% of rows, DB), NULL when that book has no close. Rewritten in place on 09-21 | PQ L483; gotcha §63, §70 (raw break-even = margin) |
| `clv_pinnacle` | raw Pinnacle | **Still raw.** Mean +1.92% vs `clv_pinnacle_devig` −2.72% on the same 1,998 prematch rows (DB) | mig 283 comment |
| `edge_percent` | "percent" | A **fraction** in probability points (`cal_prob − 1/odds`, 99.8–100% of rows per month, DB). Rounded to 0.01 before 09-22 | gotchas 48, 61 |
| in-play flag | n/a | `xg_source IS NOT NULL` on all 1,490 in-play rows. `match_minute_at_pick` NULL on 865 of them | DB |
| `timing_cohort` | window that placed the bet | 'all' since 05-20. NULL = in-play bots. The window meaning is gone | DB |
| `bot_id` | immutable owner | Re-attributed by 375 (the v10 split) | mig 375 |
| `match_id` | immutable | Re-pointed by 047 (Kambi fixture merges) | mig 047 |

### 2.4 Incidents that teach something for a merge

- **Silent CHECK rejection:** BOT-COHORTS-ALL wrote 'all' into a CHECK that did not allow it, and every
  `store_bet()` failed silently for a day (mig 116). The twin of this on shadow is mig 112.
- **Void as a one-way door:** postponed then played matches were never reopened. An ad-hoc SQL mass-void on
  08-23 left `pnl` NULL with no commit trail (BET-VOID-INTEGRITY, PQ L2784). This is the reason `void_reason`
  exists.
- **Bankroll coupling:** `bots.current_bankroll` is read-modify-written by settlement and used as the live
  staking baseline. Smoke `BOT-BANKROLL-DRIFT` asserts starting + Σpnl. Any row re-attribution must
  recompute it (375 §4b).
- **Public contamination via the table's audience:** the Ludogorets pick reached Telegram while being
  absent from /picks (356 → 361), and `bot_v10_all`'s losing O/U half was hidden for five months inside one
  bot record (375).
- **Precision destroys the gate:** `numeric(5,2)` on `edge_percent` (RELIABILITY_LEDGER §18).
- **Settlement race:** there was no `settled_at` until 366, so cache-vs-live reconciliation was flaky.

---

## 3. `shadow_bets` (+ `shadow_bets_unique`, the timing cohorts)

### 3.1 Birth

- **Migration 101**, commit `da7b0d5d`, 2026-05-13, "BET-TIMING-MONITOR — shadow_bets pipeline for per-bot
  timing analysis". The plan is in `dev/archive/bet-timing-monitor-plan.md`.
- Why: *"cohort A/B test is confounded — different bots sit in different cohorts, so the 4× ROI gap between
  cohorts could be strategy not timing."* The approach was to evaluate every bot at every window and write
  the result to **a separate table** "without disrupting any user-facing data or placed bets".
- Why it could not be `simulated_bets`: the rows are hypothetical re-evaluations with no bankroll. The
  migration says it "Mirrors simulated_bets minus bankroll fields"; stake is fixed at 10.00 "Not a real wager
  … Stored only so the existing settle_bet_result() can compute pnl". It also needed its own dedup key
  `(shadow_run_id, …)`, because the same pick appears once per run.
- **Birth invariants (plan §Invariants):** (1) *"shadow_bets is never queried by simulated_bets-aware code
  paths (frontend, daily_picks, bot_perf_report)"*; (2) no bankroll effect; (3) settled by the *same*
  `settle_bet_result()`; (4) OU bots stay in their cohort.
- **The purpose was never delivered.** Phase 3 (`scripts/bot_timing_recommendation.py`, target 06-15) was
  never built. The script does not exist, and PQ "Batch 2 — Per-bot Timing Session" lapsed.

### 3.2 Repurposing, in order

| Date | New role | Source |
|---|---|---|
| 05-19 | Runs every 30 min, cohort label becomes HHMM ("per-hour ROI"). `morning/midday/pre_ko` survive only for manual or one-shot runs | `3f81c5c7`, mig 112 |
| 05-20 | **Recovery evidence for retired bots** (SHADOW-RETIRED-OK): retired bots keep writing shadow rows so the "≥30 bets at ≥3% ROI in shadow_bets" un-retire criterion stays measurable | `b4467dee`, PQ L3006, gotcha 22 |
| 05-25 | Virtual bots: `bot_acca_leg_shadow` writes hypothetical singles | mig 131 |
| 08-18 | **Ledger for bots that must never be public or staked:** "Writes to shadow_bets only — never places a real or simulated bet, never touches bankroll" (`bot_no_pin_shadow_v1`). The sweep, pin, coolbet_value, trigger, paper and in-play-slowstate families all followed | mig 271, 272, 287, 308, 320–331, 357 |
| 09-07 | A bot's own ledger chosen *over* a private table, because it gets tracked on the admin page and stays auto-excluded from public pages | mig 308 (`corners_paper_picks` dropped) |
| 09-08 | **Mirror of sim picks in a different vocabulary** for placement: `bot_coolbet_ou_model_v1` copies calibrated `simulated_bets` O/U picks into shadow with `market='over_under_25'` so the UI placer can use them | mig 309 |
| 09-13/15 | **Real-money source:** the UI placer passes `shadow_bets.id` into `real_bets`, which led to a new FK `real_bets.shadow_bet_id` | mig 354, RELIABILITY_LEDGER §14 |
| 09-14 → | **Pre-registered instruments** live here: sharp-anchor sweep, `bot_trigger_1x2_sharp_tight_v1`, `bot_unified_gate_1x2_paper_v1` | `dev/active/*preregistration*.md` |

### 3.3 Evolution (schema)

| Mig | Date | Change | Why |
|---|---|---|---|
| 112 | 05-20 | cohort CHECK loosened to HHMM | every scheduled shadow run since 05-19 had failed silently with CheckViolation |
| 114 | 05-20 | duplicates deleted. Unique index re-keyed to `(shadow_cohort, bot_id, match_id, market, selection)` | Railway rolling deploys ran two schedulers and produced duplicate runs |
| 121/130/148 | 05-22..29 | lower-case market, `meta_clv_score`, `strategy_profile` | as for sim |
| 282 | 08-24 | `void_reason`. Also the first `shadow_bets_unique` view (latest pick_time) | "shadow_bets has no deliberate quarantines at all" (at that date) |
| 283 | 08-26 | `clv_pinnacle` (**de-vigged**, Shin), `closing_bookmaker`. View re-keyed to the **earliest** pick_time | `clv` against an arbitrary book read +9–11% next to negative ROI |
| 289 | 08-28 | `pair_gap_hours` | cross-book staleness recorded at pick time |
| 290 → 293 | 09-02 | second dedup view created then dropped | "one definition beats two" |
| 291/300 | 09-02/04 | `odds_at_pick_live`, `clv_live`, `clv_pinnacle_live` | additive restatement |
| 295 | 09-03 | view rebuilt with the full column list; smoke `SHADOW-VIEW-COLUMN-DRIFT` | the view had silently lost 289/291's columns |
| 298 | 09-03 | view exposes `bot_retired_at`, `bot_is_active`, `bot_name`, **without filtering** | 89.3% of rows came from retired bots; "filtering here would break the recovery analysis the writes exist for" |
| 308…331, 362, 370, 374 | 09-07..22 | cohort CHECK extended ~12 times with **bot-family tags** (`corners_paper`, `coolbet_trigger`, `inplay_slowstate`, `unified_gate_1x2`, …) | new writers. 362: 3 days of zero recorded picks because the tag was missing. 374: a NOT VALID CHECK blocked UPDATEs on 156,795 legacy rows |
| 334 | 09-13 | `edge_percent` ÷100 for three paper bots | they had written `edge*100` |
| 349/350 | 09-14 | `recommended_bookmaker` backfilled for single-book bots only | own-book CLV needs a book |
| 355 | 09-15 | `closing_margin`, `clv_margin_corrected`, `decision_quote_age_min`; view `shadow_bets_own_book_clv` | raw `clv` break-even is the margin |
| 357/358 | 09-15 | `inplay_minute`, `inplay_score_*`. **Two bots for two arms because the unique view dedups on (bot, match, market, selection)** | in-play slow-state rig |
| 360 | 09-15 | `shadow_bot_scoreboard` | the admin ROI computed on a CLV-complete subset had flipped the sign on 4 of 11 bots |
| 363/364 | 09-21 | `closing_minutes_before_ko`, `closing_fresh` in view | freshness before the CLV backfill |
| — | 09-21 | `clv` rewritten to own-book (93,884 recovered, 4,546 set to NULL). **65 rows of 2 pre-registered bots deliberately skipped** | PQ L483 |
| 375/402 | 09-22/24 | `bot_id` re-attributed for the v10 split | as sim |
| 386–390 | 09-23 | `leg_clv_sharp` / `clv_sharp_legs` with `dup_rank` | "shadow_bets legs were counted ~7x" |

### 3.4 What the cohorts actually are today (DB)

- `shadow_cohort` holds three different kinds of value: **HHMM** (the 30-min pipeline pass; 157k rows),
  **named windows** (`morning/midday/pre_ko`; the morning run and one-shot runs), and **bot-family tags**
  (one per standalone writer). It is used as a type column as much as a time column.
- **HHMM rows are not always copies of another table.** The sweep, pin, no-pin and coolbet_value passes run
  *inside* the pipeline's shadow pass with the HHMM tag (`daily_pipeline_v2.py:4211-4215`, `4341-4345`), so
  for those bots the HHMM rows are the bot's own decisions. For pipeline bots whose primary ledger is
  `simulated_bets`, the HHMM rows are re-evaluations of the same decision, and they are **not 1:1** with it:

  | bot | sim picks | shadow unique | in both | shadow earliest − sim pick_time | odds differ |
  |---|---|---|---|---|---|
  | bot_v10_1x2 | 403 | 360 | 343 | +89 min | 3% |
  | bot_high_alignment | 544 | 2,407 | 457 | +55 min | 2% |
  | bot_dc_value | 126 | 3,131 | 102 | +68 min | 17% |
  | bot_ah_home_fav | 148 | 309 | 111 | +141 min | 12% |

  The shadow pass skips the bankroll, the exposure cap and the `stake<1` drop, and it keeps evaluating
  after retirement. So shadow ⊄ sim and sim ⊄ shadow.
- Within one (bot, match, market, selection), copies carry **different odds (≈12–18% of groups) and
  sometimes a different book**. `shadow_bets_unique` keeps the earliest row. The later rows are the price path.

### 3.5 Deliberate separations (shadow vs sim)

- *"shadow_bets never feeds [the public performance/picks pages]"*. That is why model-edge bots were
  registered as shadow bots: to be "automatically kept OFF the public performance/picks pages" (mig 309, same
  in 316, 320, 322).
- *"Why a SEPARATE bot (not folded into bot_coolbet_ou_model_v1): that bot is real-money ON … Bolting 3.5
  onto it would place 3.5 with REAL MONEY before it is fold-robust"* (mig 316). **A bot is the unit of P&L
  accounting and of capability.**
- *"Kept as SEPARATE bots (not a flag on the existing ones) because a bot is the unit of P&L accounting"*
  (mig 322). The live and control in-play arms are two bots for the same reason (357).
- *"Pick ONE ledger per bot — do not union simulated_bets and shadow_bets"* (gotcha 18): 27 of 41 bots
  wrote both, and a union double-counts.

### 3.6 Incidents

- Silent CheckViolation three times (112, 116, 362). A new writer against an old CHECK looks exactly like
  "0 picks, quiet day".
- Duplicate scheduler instances created duplicate rows (114).
- Duplication is **outcome-correlated**: winners carried 22.7 copies vs 7.6 for losers, so 18 picks
  presented as "242 bets at +122.7%, t=11.7" (gotcha 5).
- 89% of rows are retired-bot output. Unfiltered aggregates inverted the sign: −3.44% pooled vs +8.19% for
  live bots (gotcha 22, mig 298).
- A retirement changed the DB but not the code, so retired bots kept writing (RELIABILITY_LEDGER "A
  retirement that only changes the DB").
- An FK ate the ledger: a shadow id was passed into `real_bets.simulated_bet_id`, and "Money moved; the
  ledger stayed silent" (RELIABILITY_LEDGER §14, mig 354).
- Units drift: `edge_percent` ×100 (334).
- The dedup view silently lost columns twice (295, 358, 364).

---

## 4. `picks_forward_test` (+ public/summary views, `picks_public_all`, the projection)

### 4.1 Birth

- **Migration 342**, commit `b72afef7`, 2026-09-14, "ledger table for the pre-registered forward test".
  Rule and stopping criteria are in `dev/active/picks-forward-test-preregistration.md`.
- Why it exists (prereg): *"The O/U Platt calibrator manufactured ~8–9pp of published 'edge' for months and
  nobody caught it, because there was no pre-committed criterion that could fail."*
- **Why a new table, in its own words (mig 342):**
  - not `published_picks`: *"This rule uses no model at all … Writing P_shin into a column named
    model_probability is exactly the kind of vocabulary collapse that made 'edge' mean two different things
    for months."*
  - not `simulated_bets`: *"bot-scoped and carries staking/Kelly semantics … Attaching them to a synthetic
    bot row would put them in every bot-cohort query ever written and silently re-contaminate the track
    record we just finished cleaning."*
  - mig 345 extends this to `shadow_bets`: "by the same argument not in shadow_bets either".
- The column it says matters most is `alignment_gap_minutes`, stored per row at publish time, *"Recomputing
  it later from odds_snapshots cannot work: snapshots are pruned"*. `kickoff_at`, the anchor triple
  (`anchor_odds jsonb`) and both quote timestamps are frozen for the same reason.

### 4.2 Evolution

| Mig | Date | Change | Why |
|---|---|---|---|
| 343 | 09-14 | 8 degenerate junk rows re-stamped `rule_version='…+DEGENERATE_JUNK_DAY1'`, **not deleted** | *"removing rows from a pre-registered ledger is worse than annotating them"* |
| 344 | 09-14 | `picks_forward_test_public`, `_summary`: `arm='live'` enforced **in the DB** | *"filtering it in TypeScript would mean one forgotten .eq() away from publishing bets chosen by a deliberately meaningless number. A view cannot forget."* |
| 345 | 09-14 | `bots` row `bot_sharp_forward_test_v1` (bankroll 1) as a **HANDLE, not a writer**; view `picks_forward_test_shadow` shaped like shadow_bets | the registry needs every active bot. *"Do NOT pool these numbers with shadow_bets' — same column names, different units"* |
| 346 | 09-15 | `rule_version` added to the GROUP BY of both summaries | v1's 8 picks would otherwise count toward v2's n=200 checkpoint |
| 361 | 09-16 | `picks_public_all` = sim model arm (`show_on_picks`) ∪ forward test, with an `edge_kind` label | the two edges are "NOT the same quantity and must never be rendered under one label" |
| 368/369/371 | 09-22 | third arm `consensus_anchor`. CHECK **widened, not dropped**. Views list published arms by name, junk excluded by name | *"if it is published, its record is published"*; *"an arm list that silently admits whatever is added next is how a negative control ends up in front of customers"* |
| 372 | 09-22 | consensus bot identity. `show_on_picks` FALSE on purpose ("Setting it TRUE would publish it twice") | two rules must not be averaged |
| 373 | 09-22 | postponed hidden (`<> 'postponed'`, not an allow-list) | an allow-list would hide picks at kickoff |
| 379/380/381 | 09-23 | `grade`, `grade_reasons`; bots split **in the views, not the ledger**; grades re-tiered in place (B→C, C→D) | *"Giving each grade its own arm instead would break the unique index … a leg graded B at 12:15 and C at 12:45 would be claimed — and SENT — twice"* |
| 402 | 09-24 | sharp bot split by market, again **bookkeeping only**. Stopping-rule view untouched; new `_summary_by_market` | same as 380 |

Constraints (DB): `arm IN (live, junk_anchor, consensus_anchor)`, `grade IN (B,C,D)`,
`outcome IN (won,lost,push,void)`, `UNIQUE (match_id, market, selection, arm)`. There is no FK cascade from
`matches`, so a match delete is blocked. There is no RLS; the base table has no anon grant, and anon reads
go through the views. Arms and rule versions are in the table below (DB).

| arm | rule_version | n | pending |
|---|---|---|---|
| live | sharp_edge_v1_2026_09_14 | 8 | 0 |
| live | sharp_edge_v4_2026_09_15 | 88 | 4 |
| junk_anchor | …v1+DEGENERATE_JUNK_DAY1 | 8 | 0 |
| junk_anchor | sharp_edge_v4_2026_09_15 | 586 | 12 |
| consensus_anchor | consensus_edge_v1_2026_09_22 / v2_2026_09_24 | 70 | 13 |

### 4.3 Semantics (DB-verified)

- `edge` = `p_sharp·odds − 1` (**expected ROI**) on 100% of rows. This is the opposite convention to sim's
  `edge_percent`.
- `pnl` is in **units** at a flat stake of 1: won → `round(odds−1, 2)`. Three rows differ from `odds−1`
  by <0.01, because Coolbet quotes 4-dp odds (gotcha 62b).
- `clv` is raw against the own-book close (`closing_bookmaker = bookmaker` on 100%).
  `clv_margin_corrected` uses the **per-row** margin (prereg implementation note: *"a NULL is honest, a
  guessed margin biases the n=200 and n=400 stopping rules"*).
- `odds` / `bookmaker` = best across **all** books incl. non-EMTA, by the owner's ruling of 09-14 (sim and
  shadow use the accessible set).
- Bot ownership is **derived at read time** from (arm, grade, market). No row stores a bot.

### 4.4 Separations stated around it

- `picks_board` (354/355): *"THIS TABLE IS NOT THE LEDGER, AND MUST NEVER BECOME IT … Pooling the two would
  inflate n with bets nobody was told to take … no arm column to tempt anyone into a UNION."*
- Consensus arm (prereg §"A second arm"): the live arm claims first, so *"a pre-registered pick always wins
  the tie"*. The consensus arm has an 8% edge ceiling that the live arm *"must not gain"*.
- Change discipline: *"Any change to the rule, the stopping criterion or the success criterion after the
  first pick is published invalidates the test and starts a new one with a new start date"* (v1→v4).

### 4.5 Incidents

- The junk arm was degenerate on day 1 (343). The fix was to annotate, not delete.
- Summaries pooled rule versions (346).
- A published arm was invisible on /performance (371), and a bot pick was missing from /picks (361): the
  same bug in both directions, "because the surfaces are gated independently instead of from one fact".
- Postponed matches were published (373).
- The consensus arm's CHECK fired *inside `claim()`, before the Telegram send* (369). Claim-before-send
  means the ledger and the channel never disagree.

---

## 5. Satellites and dead ledgers (brief)

| Table | Why it exists / existed | Merge relevance |
|---|---|---|
| `real_bets` (092) | Real money, "parallel to simulated_bets (paper trading)". Result vocabulary adds `half_won/half_lost`. `clv` redefined 09-11 (own book, fresh ≤60 min or NULL, gotcha §63) | Kept. FKs `simulated_bet_id` (812 rows) and `shadow_bet_id` (145). 361: one real bet per shadow pick. **Nothing is ever deleted** (361 header) |
| `published_picks` (185) | Outcome-accuracy log per *model*, immutable `picked_at`, `is_backfilled` flag. "NOT real-money tracking" | Not a bot ledger. Used as the independent 20,281-pick dataset in the Kelly test. Keep out of `picks` |
| `picks_board` (354/355) | Watchlist of non-qualifying legs, now tracked | Must never pool with the forward test |
| `user_picks` / `user_bets` / `user_pick_marks` (016/001/278) | Human picks and operator ticks. `user_pick_marks.pick_id` has **no FK "because we may want marks to survive rows migrating tables"** | Reuse ids |
| `corners_paper_picks` | Private table in the first cut. Dropped because gotcha 18 said bots belong on one shared ledger | Precedent: *one table won over a private one* |
| `cs2_*`, `lol_bets`, `tennis_value_bets` | Per-vertical copies of the sim shape (different fixture tables). CS2 dropped 08-26 | Precedent for "same shape, separate table per fixture universe" |
| `simulated_bets_pre_inplay_normalize_2026_05_17` | Backup before an in-place rescale | Shows that history was rewritten, with a backup |
| `pick_triggers`, `inplay_bot_stats`, `candidate_funnel` | Inputs and telemetry, not picks | — |
| `leg_clv_sharp` (386) | One CLV definition across the 3 ledgers, as a **separate table** so "settlement is untouched" and the forward test "keeps its own decision variable" | Keyed `(ledger, leg_id)`, which needs an id mapping |
| `promo_ledger` (356) | Promo bets, EV before the bet. 0 rows | FK `real_bet_id` only |

**Columns pointing at ledger row ids (DB):** `real_bets.simulated_bet_id` (FK, SET NULL),
`real_bets.shadow_bet_id` (FK, SET NULL), `bet_telegram_alerts.simulated_bet_id` (FK, CASCADE, 1,569 rows),
`manual_placement_queue.simulated_bet_id` (FK, CASCADE), `price_verifications.shadow_bet_id` (FK, CASCADE),
`coolbet_placement_attempts.simulated_bet_id` / `shadow_bet_id` (soft), `leg_clv_sharp.leg_id` (soft,
with `ledger`), `user_pick_marks.pick_id` (soft). Telegram message ids also live on the rows themselves:
`simulated_bets.signal_message_id` and `picks_forward_test.telegram_message_id`.

---

## 6. Consolidated invariants the merged `picks` table must preserve

1. **Invariant: a row's public visibility is an explicit, DB-enforced property, not a reader's filter.**
   Today it is the table itself: `simulated_bets` is anon-readable (whole table, mig 404), `shadow_bets` is
   not, and the forward test is visible only via `arm`-named views (344, 368, 371). A merged table must
   not carry an anon grant. Public reads must go through views that name what they admit. (mig 309, 344,
   371, 404)
2. **Invariant: controls and unpublished grades are never public.** `junk_anchor` and grade D are excluded
   by name. (343, 368, 371, 381)
3. **Invariant: pre-registered rows are never deleted, and their decision fields are never updated.**
   Annotations go in label columns (`rule_version` suffix, `grade`, `void_reason`), each with a migration
   that states the reason. (343, 381, prereg "Recording")
4. **Invariant: pre-registration covers more than the forward test.** `bot_trigger_1x2_sharp_tight_v1`,
   `bot_unified_gate_1x2_paper_v1` and the sharp-anchor-sweep per-book bots are locked populations. Any
   backfill or re-derivation must be able to exclude them (the 09-21 CLV backfill skipped 65 rows for this
   reason). An `immutable` flag on forward-test rows only is not enough. (PQ L483, `dev/active/*preregistration*.md`)
5. **Invariant: a rule change creates a new population, never a continuation.** `rule_version` stays
   in every aggregate's GROUP BY. (346, prereg)
6. **Invariant: one ledger per bot.** A bot's record is `simulated_bets` if it has rows there, otherwise its
   shadow rows, deduped. Never the union. (gotcha 18)
7. **Invariant: re-evaluations are observations, not picks.** For sim-primary bots, shadow rows are a
   price and time path. They must not add to n, and they are not 1:1 with sim (§3.4). For shadow-primary
   bots, the earliest row per (bot, match, market, selection) is the pick, whatever its cohort label.
   (gotcha 5, mig 283, 290/293)
8. **Invariant: the dedup rule is EARLIEST `pick_time` per (bot, match, market, selection)**, and there
   is exactly one definition of it. (283, 293)
9. **Invariant: SHADOW-RETIRED-OK.** Retired pipeline bots' re-evaluations are kept and exposed (with
   `bot_retired_at`), not filtered at the source, because they are the only un-retire evidence. Whether to
   keep producing them is an open owner decision (audit B4). Deleting what exists is not an option.
   (gotcha 22, mig 298)
10. **Invariant: restatements are additive.** `odds_at_pick` and `pnl` stay as recorded, and honest values
    go in new columns (`odds_at_pick_live`, `clv_live`). *"The audit trail is the product."* (291, 300)
    Counter-precedents exist: `clv` was rewritten 09-21, stakes were rescaled in 107, `bot_id` was
    re-attributed in 375. Each was a decision recorded in a migration or queue row.
11. **Invariant: history of `edge_percent` is not backfilled.** It is the record of what cleared each floor.
    (367)
12. **Invariant: the two edge quantities never share an unlabeled column.** Probability points (`cal_prob −
    1/odds`) and expected ROI (`p·odds − 1`) need an explicit `edge_kind` per row. (361, prereg "On the
    word edge", SYSTEM_MAP §1)
13. **Invariant: CLV columns keep their definition in their name.** Raw own-book (break-even = margin),
    margin-corrected, raw Pinnacle, de-vigged Pinnacle, sharp-close (`leg_clv_sharp`). In-play has none.
    (283, 355, gotchas 14/70, §63)
14. **Invariant: per-row provenance frozen at decision time:** kickoff, anchor quote and timestamps,
    alignment gap, quote age, pair gap, book. Snapshots are pruned, so these fields cannot be recomputed later.
    (342 header, 289, 355)
15. **Invariant: a bot is the unit of P&L accounting and of capability.** Anything that should be graded
    or switched independently is a separate bot, not a flag. Forward-test bot ownership is derived at read
    time, not stored. (316, 322, 357, 380, 402)
16. **Invariant: `bots.name` is the join key everywhere.** `display_name` is display-only and "Nothing may
    ever join on it". Retired bot rows stay. (375)
17. **Invariant: claim before send.** The ledger row exists before any publication, and the unique key
    prevents a re-qualifying leg (median 6 re-qualifications) from being sent twice. (prereg v4, 369)
18. **Invariant: `void` is not one thing.** `void_reason` separates push, postponement and quarantine, so a
    re-settler never resurrects quarantined P&L. (282)
19. **Invariant: placement lineage.** One `real_bets` row per shadow pick. Real-money rows are never
    deleted. The FKs must point at the table that actually holds the id. (354, 361, RELIABILITY_LEDGER §14)
20. **Invariant: in-play rows are judged without CLV** and are identified by `xg_source` (sim) or
    `inplay_minute` (shadow). (106, 157, gotcha 14)

---

## 7. What a naive merge would break (with numbers)

| # | Naive step | What breaks | Source / measurement |
|---|---|---|---|
| 1 | Drop shadow rows with `shadow_cohort ~ '^[0-9]{4}$'` (the draft 409 rule) | **11,447 unique picks vanish** from 37 bots (DB). These are the entire HHMM-written record of `bot_pin_1x2_home_v1` (258), `bot_coolbet_value_v1` (258), `bot_sweep_ou25/35_v1` (185/145), the dc bots (7.5k) and `bot_high_alignment` (1,950). They include SHADOW-RETIRED-OK recovery evidence and shadow-only bots whose passes run under HHMM tags | §3.4, `daily_pipeline_v2.py:4211` |
| 2 | Union sim with the non-HHMM shadow rows | **403 picks double-counted** where named-cohort shadow rows duplicate a sim pick (`bot_aggressive` 265, `bot_btts_all` 52, …) (DB) | gotcha 18 |
| 3 | Rely on vocabulary mismatch to prevent double counts | Gotcha 23's safeguard **no longer exists**. Stored vocab was canonicalised 2026-09-10, so sim and shadow now join cleanly and a union counts twice | PQ MARKET-VOCAB-CANONICAL, DB (1 legacy `'o/u'` row left) |
| 4 | Map `clv_pinnacle` → one column | Mixes raw (sim, +1.92%) with de-vigged (shadow, −2.33%). The gap is ~4.6pp on the same rows | DB, mig 283 |
| 5 | Map `edge_percent`/`edge` → one `edge` | Mixes probability points with expected ROI. 7 shadow bots (`bot_pin_1x2_home_v1`, `bot_coolbet_value_v1`, `bot_sweep_ou25/35_v1`, corners/team-total/1h paper) and the whole forward test store ROI-form (DB); everything else stores probability points. Floors of "3%" admit roughly 2× more picks in one form | DB, prereg |
| 6 | `is_inplay = match_minute_at_pick IS NOT NULL` (draft 409) | 865 of 1,490 in-play sim rows read as pre-match, so their meaningless CLV enters pre-match stats | DB, mig 173 |
| 7 | Sum `pnl` or `stake` across sources | € at Kelly stakes (sim), € at a flat 10 (shadow), units at 1 (forward test). Also 182 voided sim rows keep a non-zero pnl (Σ −8.49) | DB, mig 345 |
| 8 | Recompute `pnl` from `odds` for the reconciliation | Forward-test pnl is rounded to 2 dp (3 rows differ by <0.01). Voided sim rows carry legacy pnl. Row-for-row equality needs tolerance plus a void rule | DB |
| 9 | Settle everything with one result enum | Forward test has `push`; sim and shadow encode push as `void`; `real_bets` has half results; `void_reason` is free text with at least 6 families | DB |
| 10 | Grant the merged table to anon, as `simulated_bets` is today | Publishes 160k shadow rows, the junk control, grade D, retired bots and pre-registered paper instruments | mig 404, 344/371 |
| 11 | Store forward-test `bot_id` on rows | Turns a read-time derivation into a stored re-label. 380/402 split bots "in the views, not the ledger" | mig 380, 402 |
| 12 | Give each consensus grade its own arm or key | Breaks the dedup that stops a leg being claimed and sent twice | mig 380 |
| 13 | New surrogate ids for `picks` | Breaks 9 id references (5 FKs, 4 soft), placement reconciliation (`REAL-BETS-ATTEMPTS-RECONCILED`) and operator marks | §5 |
| 14 | One unique key `(bot_id, match_id, market, selection)` | Collides for cohort copies (shadow key includes `shadow_cohort`) and for the two forward-test arms on the same leg (key includes `arm`, no bot) | DB constraints |
| 15 | A CHECK/enum on `cohort`/`source` added NOT VALID or without legacy values | Three silent-rejection incidents (112, 116, 362) and one UPDATE lockout on 156,795 rows (374). Settlement UPDATEs every row | RELIABILITY_LEDGER §15 |
| 16 | Join kickoff from `matches.date` for all rows | Forward test freezes `kickoff_at`. Sim and shadow move with reschedules: ~150 pre-match sim rows now show `pick_time > kickoff` (DB) | mig 342 |
| 17 | One `settled_at` / trigger | Only sim has the 366 trigger. Shadow has none. Historical NULLs mean "settled before tracking" | mig 366 |
| 18 | Move the bankroll with the rows | `bots.current_bankroll` is the live staking baseline, settlement read-modify-writes it, and `BOT-BANKROLL-DRIFT` asserts it. Only sim rows may feed it (shadow "never touches bankroll") | mig 101, 375 §4b |
| 19 | Treat the ledgers as append-only for reconciliation | Deletes (054, 114, 321, cleanup scripts, 199 combos), re-attributions (375) and rescales (107, in-play ×5) happened. Reconcile against **current** rows, not history | §2.2, §3.3 |
| 20 | Rebuild the dedup view with `SELECT *` over a new table | The view freezes its column list, and column drift has already caused silent loss three times | 295, 358, 364 |
| 21 | Change a CLV definition during migration | Pools two quantities under a running comparison. Every earlier redefinition was either additive or done with the pre-registered populations excluded | §63, PQ L483 |

---

## 8. Doc and design drift found while doing this (flag, not fix)

- **`docs/ANALYSIS_GOTCHAS.md` §23** ("Do NOT normalise the 1X2/1x2 case split — it is load-bearing") is
  stale. Stored vocabulary was canonicalised on 2026-09-10 (MARKET-VOCAB-CANONICAL Phase 2), so the
  accidental double-count guard it describes is gone. §18's reason "joining on market returns zero overlap"
  is stale for the same reason, though its rule still stands.
- **`dev/archive/bet-timing-monitor-plan.md` invariant 1** ("shadow_bets is never queried by
  simulated_bets-aware code paths") no longer describes reality: the placer, `real_bets`,
  `leg_clv_sharp`/`clv_sharp_legs` and the admin pages all read it.
- **Draft migration 409 (untracked)** has two rules that contradict measured data:
  - the HHMM exclusion (§7 #1, #2);
  - the `is_inplay` rule (§7 #6).
  
  Its comment "timing-cohort copies" is accurate only for sim-primary bots.
- `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` proposes `immutable bool` for forward-test rows only. That
  does not cover the pre-registered shadow bots (§6 #4).
