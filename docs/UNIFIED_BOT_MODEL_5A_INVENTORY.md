# Unified bot model: step 5a inventory (#139, 2026-09-24)

> **CHANGED 2026-09-24 (#139 IA P6/P7):** `/admin/shadow-bots/[bot]` is RETIRED — it redirects to `/admin/bots?bot=<name>&tab=picks` (the sheet carries Bet made + current prices; the model-edge "Min odds" was dropped, #139 finding b). `/admin/shadow-bots` is now the Pick queue (picks + Place only): its safety strip, scoreboard and promotions were removed (promotions moved to /admin/real-bets).


> **CHANGED 2026-09-24 (#139 IA moves P2/P3):** the legacy `/api/admin/coolbet-placer-bots` and `/api/admin/coolbet-daemons-pause` routes and their `CoolbetPlacerToggle` / `CoolbetDaemonsPause` components were DELETED. Per-bot real-money eligibility is switched on /admin/bots only; the footprint pause (now "Coolbet sweeping") on /admin/feeds only — both through the audited `admin_set_control`. Owner decision the same day: the footprint pause stops odds sweeping, never real bets. The text below is the point-in-time audit.


Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in `PRIORITY_QUEUE.md`. Design: `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` § Phase 5.
Genesis research: `dev/active/unified-bot-model-genesis/`.

This is a **read-only inventory** taken before any Phase 5 migration.
- All DB figures come from SELECT-only queries run on 2026-09-24 between 18:00 and 18:45 EEST.
- Code references were checked against `main` at `ab26c403`, plus the working tree.

The document describes what exists today. It is not a backlog: every follow-up belongs in #139 or its own `PRIORITY_QUEUE.md` row.

**How liveness is labelled**
- **LIVE**: reached from a registered `add_job` in `workers/scheduler.py`, including the LivePoller and the subprocess scripts the scheduler launches. Also counts: a systemd unit (`oddsintel-inplay-collector`), a scheduled GitHub workflow, or a web page or route that is actually served.
- **PAUSED**: `local/launchd/paused/` holds `place_coolbet_ui.py --execute` and `best_price_router --execute`.
- **RETIRED**: `coolbet_mac_daemon.py` (plist in `local/launchd/retired/`, not loaded; retired 2026-09-10).
- **DORMANT**: the code exists but nothing live reaches it (an env flag, never registered, or no callers).
- **ONE-OFF**: run by hand.

> **Correction to the brief.** The "Mac placer reading `shadow_bets_unique`" is `scripts/place_coolbet_ui.py:644`, and it is **PAUSED**. The Mac daemon itself is retired. The only live `real_bets` writers are:
> - the Telegram "Record" button → `_drain_manual_placement_queue` → `coolbet_placer.place_bet_by_id`. These are paper rows: `MANUAL_PLACE_EXECUTE=False`, so `placed_real` is FALSE.
> - the web RPC `record_manual_real_bet` (migration 407).
>
> The placer must still be migrated as if it were live, because un-pausing it is one plist away.

---

## 0. Headline findings (read these first)

1. **Most `shadow_bets` rows are HHMM timing copies.**
   - 156,956 of 167,846 rows (93.5%) have `shadow_cohort ~ '^[0-9]{3,4}$'`. These are re-inserts from the 30-minute `shadow_interval` run, up to 48 per logical pick per day.
   - They are still being written today, including for 38 retired bots (SHADOW-RETIRED-OK, by design).
   - 13,365 of the 23,912 unique (bot, match, market, selection) keys exist **only** as HHMM copies. For the dc, sweep and pin families these copies are the bot's whole record.
   - Consequence for Phase 5: the design contract's first rule, "exclude timing copies", would have dropped them. Migration 410 already reversed that rule (see §3.3).
2. **The same pick lives in both `simulated_bets` and `shadow_bets` for the sim bots.**
   - `bot_v10_1x2`: 343 of its 403 sim picks also sit in shadow.
   - `bot_aggressive`: 395 of 713.
   - Migration 410 handles this "by bot": a bot that writes `simulated_bets` contributes no shadow rows. The unified `picks` table needs the same rule, **plus** a `cohort` column so the copies are kept as re-evaluations and not as extra picks.
3. **`picks_forward_test` is not immutable at the database level.**
   - There is no trigger enforcing it.
   - Rows have already been changed after publication four ways: `results_check._regrade` clears outcome, pnl and settled_at, then re-settles; migration 343 relabelled `rule_version`; migration 381 re-tiered grades (C→D, B→C); `scripts/backfill_consensus_grades.py` filled NULL grades.
   - The Phase 5 `immutable` trigger must allow the settlement and regrade columns and forbid the decision columns.
4. **Sim CLV basis drift (new).**
   - Migration 410 uses `simulated_bets.clv_pinnacle_devig` as the model family's admissible metric.
   - That column has **no live writer**: its only writer is the one-off `backfill_simulated_clv_devig.py`. Only 63 of 191 September settled rows have it.
   - The live settler writes `clv_pinnacle`, and the two columns disagree on 2,186 rows.
   - So `bot_v10_1x2`'s verdict metric is going stale as of now. Decide which column is canonical before backfilling `picks`.
5. **Two live write defects found by this inventory were fixed today by the lead session:**
   - `simulated_bets` CLV was NULL on every row settled from 2026-09-14. The cause was that `_PENDING_BETS_SQL` did not select `recommended_bookmaker`. Fixed in `2cf14433`; 14 of 14 rows are now filled.
   - The Epicbet and Tonybet trigger cohorts violated the `shadow_cohort` CHECK. Fixed in migration 409 (`f20654b7`).
6. **`user_pick_marks.pick_id` is a soft foreign key into `shadow_bets.id`.**
   - All 715 rows point at shadow ids (0 at sim, 0 at forward test).
   - The same holds for `leg_clv_sharp (ledger, leg_id)`: 171k rows, polymorphic, no FK.
   - Both need an id-mapping strategy. **The recommended strategy is to keep the source UUID as `picks.id`.**
7. **Anonymous users can read the whole of `simulated_bets` and `bots`.**
   - `GRANT SELECT` is given to `anon`, and RLS is `Public read`.
   - A unified `picks` table must **not** inherit that grant. Anonymous users should see only a `*_public` view (#072).
   - `shadow_bets` has an RLS anon policy but no anon grant, so it is not readable.
8. **Upsert writers rewrite rows that `real_bets` points at.** `pick_generator`, `pick_trigger_matcher` and `ou35_model_shadow` use `ON CONFLICT DO UPDATE` on odds, probability and edge, with no `result='pending'` guard.
9. **There are three more ledger-like tables the brief did not name.**
   - `published_picks`: 50,050 rows, a frozen model argmax per match and market.
   - `picks_board`: 1,290 rows, the /picks watchlist.
   - `user_bets`: 0 rows.
   - `published_picks` and `picks_board` are **not** bot picks and should stay out of `picks` (§2.3). Their names should be kept apart from the new table.

---

## 1. The seven tables in scope

### 1.1 Size, shape, access

| table | kind | rows (exact) | size | date range | RLS | anon | owner |
|---|---|---|---|---|---|---|---|
| `simulated_bets` | table | 4,673 | 6.9 MB | 2026-05-01 → 2026-09-22 16:35 | on: `Public read` (true) | **SELECT** | oddsintel_owner |
| `shadow_bets` | table | 167,846 | 114 MB | 2026-05-13 → now | on: `shadow_bets_anon_read` (true) | no grant | oddsintel_owner |
| `shadow_bets_unique` | view | 23,912 | — | — | — | no | — |
| `picks_forward_test` | table | 760 | 0.5 MB | 2026-09-14 → now | **off** | no (only via `*_public`/summary views) | oddsintel_owner |
| `real_bets` | table | 992 | 0.7 MB | 2026-05-11 → 2026-09-15 | on, **no policy** (service only) | no | oddsintel_owner |
| `user_picks` | table | 6 | 80 kB | 2026-04-29 → 2026-05-28 | on, no policy | no | oddsintel_owner |
| `bots` | table | 108 (20 active) | 272 kB | 2026-04-27 → 2026-09-24 | on: `Public read` + `public_read` (duplicate) | **SELECT** | oddsintel_owner |
| `coolbet_placer_bots` | table | 2 (both `ui_place_enabled=false`) | 32 kB | 09-13 / 09-14 | on: deny anon/auth | no | oddsintel_owner |

**Grants.** `authenticated` has SELECT, INSERT, UPDATE and DELETE on every table and view above. This is harmless in practice because the browser cannot authenticate to the VPS PostgREST, but it should be tidied. `service_role` and `oddsintel_owner` have ALL.

**Row composition that matters for the merge**

| ledger | result vocabulary (DB) | counts | stake | pnl basis |
|---|---|---|---|---|
| `simulated_bets` | enum `bet_result` {pending, won, lost, void} | won 1,785 · lost 2,505 · void 383 · pending 0 | variable Kelly: 1,004 distinct values, €1.00–17.38, median 5; in-play flat 5 | stake×(odds_at_pick−1) or −stake, 2 dp. **182 quarantined voids keep a non-zero pnl** (void_reason `quarantine`): a sum of pnl over all rows ≠ a sum over won/lost. 41 won rows differ from the formula only by rounding. |
| `shadow_bets` | same enum | won 85,918 · lost 80,391 · void 1,067 · pending 471 | flat €10.00 on every row | stake×(odds_at_pick−1); **410 settled voids have pnl NULL** (0 elsewhere). Uses `odds_at_pick` (the high-water mark on upserted rows), not `odds_at_pick_live`. |
| `picks_forward_test` | `outcome` text CHECK {won, lost, push, void}; NULL means open | won 264 · lost 459 · void 8 · open 29 · push 0 | no column (1 unit) | odds−1 or −1; push/void 0 |
| `real_bets` | text CHECK {pending, won, lost, void, half_won, half_lost} | won 371 · lost 597 · void 24 | actual € | (actual_odds−1)×stake |
| `user_picks` | CHECK {pending, won, lost, void}, 1x2 only | 6 rows | — | none |

**Selection vocabularies match** across all four ledgers: `1x2` home/draw/away, `over_under_25` over/under, AH as `home -0.5`.
- Sim holds 6 quarter-line AH picks (−0.25, −0.75, −1.25, −1.75). Their half results are encoded **only in pnl**, because `bet_result` has no half values.

**Picks per shadow cohort**

| `shadow_cohort` | rows | bots | unique keys | last write |
|---|---|---|---|---|
| HHMM copies (`^[0-9]{3,4}$`) | 156,956 | 40 | 14,941 | today |
| morning | 2,654 | 25 | 2,654 | 09-08 |
| coolbet_trigger / unibet_trigger | 1,554 / 1,136 | 5 / 5 | = rows | today |
| corners_paper / team_total_paper / fh_1x2_paper | 1,212 / 1,099 / 486 | 1 each | = rows | today (retired bots, still writing) |
| inplay_slowstate | 914 | 2 (both bots share one cohort) | 914 | today |
| ou35_model, trigger_1x2_model, unified_gate_1x2, midday, pre_ko, trigger_ou_model, trigger_1x2_sharp, coolbet_ou_model, coolbet_1x2_model, trigger_ou_sharp | 458, 416, 203, 230, 196, 190, 74, 33, 19, 18 | 1–13 | = rows | mixed |

**How `shadow_bets_unique` behaves.** It is `DISTINCT ON (bot_id, match_id, market, selection) ORDER BY pick_time`, so the **earliest** copy wins. 13,507 of its 23,912 rows are HHMM copies.

**Which ledger each active bot writes (20 bots)**
- **sim only**: none.
- **sim plus shadow HHMM**: `bot_v10_1x2` (403 sim / 3,310 shadow), `bot_high_roi_global_v2` (52 / 399).
- **shadow only**: the 4 per-book triggers, `bot_trigger_1x2_sharp_tight_v1`, `bot_trigger_{1x2,ou}_sharp_v1`, `bot_coolbet_{1x2,ou}_model_v1`, `bot_ou35_model_v1`, `bot_unified_gate_1x2_paper_v1`, `bot_inplay_slowstate{,_afctl}_v1`.
- **forward test only** (no bot_id on the rows; mapped through arm, grade and market): `bot_sharp_{1x2,ou}_v1`, `bot_consensus_{b,c,d}_v1`.
- **Retired bots still writing shadow rows in the last 7 days**: 21 bots, about 2,900 rows. Examples: `bot_high_alignment` 855, `bot_team_total_paper_shadow_v1` 500, `bot_corners_paper_shadow_v1` 490, `bot_1h_1x2_paper_shadow_v1` 200.

**Column fill (shadow_bets, n=167,848)**

| column | filled |
|---|---|
| clv | 146,941 |
| clv_pinnacle | 141,250 |
| clv_margin_corrected | 43,761 |
| clv_live | 133,052 |
| odds_at_pick_live | 160,047 |
| inplay_minute | 914 |
| decision_quote_age_min | 645 |
| pair_gap_hours | 2,477 |
| meta_clv_score | 19,124 |
| strategy_profile | 914 |
| model_version | 164,285 |
| kelly_fraction | 137,273 |

**Column fill (simulated_bets, n=4,673)**

| column | filled |
|---|---|
| clv | 2,056 |
| clv_pinnacle | 3,243 |
| clv_pinnacle_devig | 2,982 |
| clv_live | 2,113 |
| odds_at_pick_live | 3,633 |
| settled_at | 7 (trigger added recently) |
| signaled_at | 510 |
| user_placed_at | 4 |
| admin_offered_at | 636 |
| dimension_scores | 3,182 |
| combo_legs | **0** |
| timing_cohort | named 3,141; NULL 1,532 |

**In-play and timing anomalies in `simulated_bets`**
- 865 in-play rows have `match_minute_at_pick` NULL but `xg_source` set.
- **153 picks from pre-match bots have `pick_time > kickoff`.**

**How `real_bets` links to its source pick**

| link | rows | placed_real |
|---|---|---|
| via `simulated_bet_id` | 812 | NULL |
| via `shadow_bet_id` | 143 | TRUE |
| via `shadow_bet_id` | 2 | NULL |
| **no link** | 35 | NULL |

The 35 unlinked rows come from `reconcile_account_to_real_bets` and older manual inserts.

### 1.2 Indexes

| table | index | definition | size |
|---|---|---|---|
| simulated_bets | `simulated_bets_pkey` | UNIQUE (id) | 264 kB |
| | `uq_bet_per_bot_match_market_selection` | UNIQUE (bot_id, match_id, market, selection), also a constraint | 528 kB |
| | idx_…_bot_id, _bot_result, _match_id, _pick_time, _result, _timing_cohort, _version_picktime (model_version, pick_time) | btree | 80–288 kB |
| | idx_…_pick_time_result | (pick_time) WHERE result IN (won, lost) | 176 kB |
| | idx_…_settled_at, _calibrated_prob, _alignment_class, _af_agrees, _combo, _pin_cross_drift_shadow | partial | ≤176 kB |
| shadow_bets | `shadow_bets_pkey` | UNIQUE (id) | 10 MB |
| | `uq_shadow_bet_per_cohort` | UNIQUE (shadow_cohort, bot_id, match_id, market, selection) | **21 MB** |
| | idx_shadow_bets_bot_match_mkt | (bot_id, match_id, market, selection) | 5.2 MB |
| | idx_shadow_bets_cohort_picktime | (shadow_cohort, pick_time) | 9.2 MB |
| | idx_shadow_bets_run | (shadow_run_id) | 2.6 MB |
| | idx_shadow_bets_pending | (match_id) WHERE result='pending' | 176 kB |
| | idx_shadow_bets_pair_gap | partial | 136 kB |
| picks_forward_test | pkey; `picks_forward_test_unique` | UNIQUE (match_id, market, selection, arm) | 88 kB |
| | _published (published_at DESC); _unsettled (kickoff_at) WHERE outcome IS NULL | | |
| real_bets | pkey; `real_bets_one_per_shadow_pick` | UNIQUE (shadow_bet_id) WHERE NOT NULL | |
| | idx on bot_id, match_id, placed_at DESC, pending partial, shadow_bet_id | | |
| user_picks | pkey; UNIQUE (user_id, match_id); idx user_id; idx match_id | | |
| bots | pkey; `bots_name_key` UNIQUE (name) | | |
| coolbet_placer_bots | pkey (bot_name) | | |
| leg_clv_sharp | pkey (ledger, leg_id); idx match_id | | 11 MB / 2 MB |

### 1.3 Constraints and triggers

**CHECK constraints**
- `simulated_bets`: odds_at_pick > 0; model_probability in [0, 1]; stake > 0; `timing_cohort` NULL, or in {morning, midday, pre_ko, all}, or `^[0-9]{4}$`.
- `shadow_bets`: odds > 0; probability in [0, 1]; stake > 0; `shadow_cohort` in the 23-name allow-list or `^[0-9]{3,4}$` (migration 409).
- `picks_forward_test`: arm in {live, junk_anchor, consensus_anchor}; grade NULL or in {B, C, D}; outcome in {won, lost, push, void}.
- `real_bets`: result CHECK (see §1.1); stake > 0.
- `bots`: `maturity_label` NULL or in {experimental, beta, calibrated, testing, retired}; starting_bankroll > 0.
- `user_picks`: selection in {home, draw, away}; result CHECK.

**Triggers**

| trigger | when | what it does | impact on Phase 5 |
|---|---|---|---|
| `simulated_bets.trg_simulated_bets_settled_at` | BEFORE UPDATE | `set_simulated_bet_settled_at()` stamps settled_at on the first move off pending | Must be carried to `picks` |
| `simulated_bets.trigger_notify_inplay_bet_fired` | AFTER INSERT | `pg_notify('inplay_bet_fired', …)` when `xg_source IS NOT NULL` | Its in-play consumer is dormant. **Drop, or port with a family guard.** |
| `bots.bots_maturity_retired_invariant` | BEFORE INSERT/UPDATE OF is_active | `bots_set_retired_label()`: is_active false → maturity 'retired' and retired_at stamped | Keep on the extended `bots` |
| `bots.trg_bots_updated_at` | | updated_at | Keep |

`shadow_bets`, `picks_forward_test`, `real_bets`, `user_picks` and `coolbet_placer_bots` have no triggers.

### 1.4 Foreign keys (in and out)

| FK | on delete | note |
|---|---|---|
| simulated_bets.bot_id → bots | **CASCADE** | |
| simulated_bets.match_id → matches | **CASCADE** | |
| shadow_bets.bot_id → bots | **CASCADE** | |
| shadow_bets.match_id → matches | **CASCADE** | `cleanup_match_dupes.py` does **not** re-point shadow_bets, real_bets or picks_forward_test, so deleting a duplicate match cascades a delete of shadow picks |
| picks_forward_test.match_id → matches | NO ACTION | |
| real_bets.simulated_bet_id → simulated_bets | SET NULL | |
| real_bets.shadow_bet_id → shadow_bets | SET NULL | |
| real_bets.bot_id → bots; match_id → matches; bookmaker → accessible_bookmakers | NO ACTION | |
| user_picks.match_id → matches | CASCADE | |
| bet_telegram_alerts.simulated_bet_id → simulated_bets (1,569 rows, last 09-11) | CASCADE | |
| manual_placement_queue.simulated_bet_id → simulated_bets (0 rows; drained every 10 s) | CASCADE | |
| price_verifications.shadow_bet_id → shadow_bets (2 rows, today) | CASCADE | |
| coolbet_placement_attempts.bot_id → bots; real_bet_id → real_bets | NO ACTION | |
| promo_ledger.real_bet_id → real_bets (0 rows) | | |

**Soft references with no FK. All of them must survive the id change.**
- `coolbet_placement_attempts.{simulated_bet_id, shadow_bet_id, bot_name}`: 2,167 rows, last 09-13.
- `leg_clv_sharp (ledger ∈ {simulated_bets, shadow_bets, picks_forward_test}, leg_id)`: 4,294 / 166,339 / 724 rows. Written only by `workers/jobs/clv_sharp.py` at 01:40.
- `user_pick_marks.pick_id`: 715 rows, all shadow ids. Web route `/api/me/pick-marks`.
- `shadow_bets.shadow_run_id`: no table behind it.
- `coolbet_placer_bots.bot_name` and `bot_config_history.bot_name` (16 rows, 2026-08-24): keyed by name, not id. `bot_config_history` is an existing precursor of the planned `bot_config_versions`; reuse it rather than adding a second one.

### 1.5 DB views and functions that reference these tables

There are no materialized views in `public`. Dependency tree (`pg_depend`, recursive):

| view | built on | read by | public? |
|---|---|---|---|
| `shadow_bets_unique` | shadow_bets ⨝ bots | web shadow-bots pages, `place_coolbet_ui` (PAUSED), `book_price_fidelity` (Mon 08:15), `trigger_calibrator_check` (07:15), `weekly_bot_review` (Sun), 410 `bot_ledger`, ~15 one-off scripts | no |
| `shadow_bot_scoreboard` | shadow_bets_unique | `/admin/shadow-bots` | no |
| `shadow_bets_own_book_clv` | shadow_bets_unique | one-off `sharp_tight_slope`; no web caller (a stale comment says otherwise) | no |
| `picks_forward_test_public` | picks_forward_test ⨝ matches, leagues, teams (arms live + consensus) | `/performance` (`getPicksForwardTestBets`) | **anon** |
| `picks_forward_test_summary` | picks_forward_test by (rule_version, arm, grade) | `/performance:408` | **anon** |
| `picks_forward_test_summary_by_market` | same plus market | `/performance` | **anon** |
| `picks_forward_test_arm_summary` | picks_forward_test by (rule_version, arm), **includes junk_anchor** | smoke tests only | no |
| `picks_forward_test_shadow` | picks_forward_test × the **retired** `bot_sharp_forward_test_v1` | smoke tests only; effectively dead | no |
| `picks_public_all` | UNION of forward test (live + consensus, excluding postponed and unsent grade D) and `simulated_bets` (`bots.show_on_picks AND retired_at IS NULL AND combo_legs IS NULL AND match_minute_at_pick IS NULL`, not postponed) | `/picks`, `/api/v1/upcoming` | **anon** |
| `clv_sharp_legs` | leg_clv_sharp ⨝ each of the three ledgers ⨝ bots | observatory / #121 | no |
| `bot_ledger`, `bot_scoreboard`, `bot_capabilities` (+ table `bot_config`) | migration 410 (today): all three ledgers | new `/admin/bots` (being built) | no (service_role) |

**Functions.** Only `record_manual_real_bet(...)` (migration 407, plpgsql). It takes an advisory lock, checks for a same-day (match, market, selection) duplicate, and inserts `real_bets` with `simulated_bet_id` or `shadow_bet_id`. The web routes `/api/admin/real-bet` and `place-action.tsx` call it. No other `pg_proc` source mentions the ledgers.

**Arm-to-bot mapping is written three times, and the copies differ.**
- `picks_public_all`: ungraded consensus → `bot_consensus_b_v1`; any non-O/U live row → sharp_1x2.
- `clv_sharp_legs`: ungraded consensus → `consensus_ungraded`.
- 410 `bot_ledger`: follows `picks_public_all`.

A `picks.bot_id` stored on the row would remove all three copies.

---

## 1A. Engine readers and writers by table

The full per-line lists are in §1B (sim) and §1C (shadow and the others). This is the functional summary.

### Writers (every INSERT/UPDATE path that is LIVE)

| ledger | INSERT | settlement | CLV | other UPDATEs |
|---|---|---|---|---|
| `simulated_bets` | `supabase_client.store_bet` :2367 (only insert; no ON CONFLICT, the dup exception is swallowed), from `daily_pipeline_v2.run_morning` :4071 (04:00 morning plus `betting_refresh` :05/:35). The in-play caller `inplay_bot.py:391` is DORMANT. | `settlement._settle_pending_bets` :3965 (plus `bots.current_bankroll`); `fix_stale_live_matches` :2204; `void_ungradeable_1h_bets` :2431; `resettle_wrongly_voided_bets` :2574; `_apply_clv_autovoid` :4768; `results_check._regrade` :120 (every 2 h); `scripts/match_status_sweeper.py:97` (**GH workflow every 30 min**) | written inside `_settle_pending_bets`; `scripts/backfill_odds_at_pick_live.py:128` (LIVE every 30 min); side table `leg_clv_sharp` via `clv_sharp.py` | `coolbet_signaler` :422 signaled_at, :532 signal_message_id; **`news_checker` :338 overwrites model_probability, reasoning, news_* after the pick**; web Telegram webhook writes `user_placed_at` / `user_skipped_at`; web `/admin/place` writes `admin_offered_at` |
| `shadow_bets` | `bulk_store_shadow_bets` :2437 (ON CONFLICT DO NOTHING) from `run_morning(shadow_mode)` (:10/:40 HHMM copies, all 35 `BOTS_CONFIG` bots) plus the no_pin and sweep passes; **DO UPDATE upserts**: `pick_generator` :322, `pick_trigger_matcher` :201, `ou35_model_shadow` :136; DO NOTHING: `corners_paper_bot` :204, `team_total_paper_bot` :154, `first_half_1x2_paper_bot` :118, `inplay_collector` :256 (systemd) | `_settle_pending_shadow_bets` :4175 (excludes corners_ou_%); `fix_stale_live_matches` :2213; the 1H void; resettle; `results_check`; `match_status_sweeper.py:103` (GH); **each paper bot's own `settle_picks`** (corners :300, team_total :208, fh :250). team_total and 1H rows are settled twice, a race. | in `_settle_pending_shadow_bets` (clv, clv_pinnacle, clv_live, clv_pinnacle_live, closing_margin, clv_margin_corrected, closing_minutes_before_ko); `backfill_odds_at_pick_live` (LIVE) | none |
| `picks_forward_test` | `scripts/publish_picks_forward_test.py:791 claim()` ON CONFLICT (match, market, selection, arm) DO NOTHING; scheduler :2739 live, :2763 consensus, :2796 junk, at :05/:35 | `settle_picks_forward_test` :1870; `_void_forward_test_on_dead_matches` :1800; `results_check._regrade` :128 (**clears outcome, pnl and settled_at, then re-settles**) | written at settlement (clv, clv_margin_corrected); `leg_clv_sharp` | `attach_message_id` :825 (telegram_message_id after the claim) |
| `real_bets` | `supabase_client.store_real_bet` :5791, from `coolbet_placer.place_bet_by_id` (LIVE on demand, paper); `coolbet_ui_placer.stage_bet` and `best_price_router` (PAUSED); `place_coolbet_ui.py:622` direct INSERT with no source link (PAUSED); web `record_manual_real_bet` RPC and `/api/admin/record-combo` (unlinked page) | `_settle_real_bets_for_matches` :1632; `_settle_real_combo_bets` :2074; `_void_real_bets_on_dead_matches` :1676 | at settlement (clv and clv_pinnacle at the venue's own feed) | none |
| `user_picks` | none in the engine or the web (6 legacy rows) | `_settle_user_picks_for_matches` :2668; `_settle_user_picks` :4243 | — | — |
| `bots` | `ensure_bots` :51 (re-creates any missing `BOTS_CONFIG` bot on every run) | `current_bankroll` from `_settle_pending_bets` :3998, resettle :2606, `results_check` :123 | — | migrations (retire, promote); web: none |
| `coolbet_placer_bots` | none in the engine | — | — | web `/api/admin/coolbet-placer-bots` :89 UPDATE `ui_place_enabled` (the toggle on `/admin/shadow-bots`) |

**`bots` lookups by name that do NOT filter `retired_at`:** corners :118, team_total :82, fh :51, ou35 :42. This is why retired paper bots keep writing.

### Readers by area (LIVE only; see §1B/§1C for columns)

**Placement and signalling**
- `coolbet_placer.load_qualified_*` :477/:710/:2648 (sim; the filter branch is live through the manual queue).
- `place_coolbet_ui.load_picks` :644 (`shadow_bets_unique`, PAUSED).
- `coolbet_signaler.load_signal_candidates` :144 (sim).
- `coolbet_prekickoff_alert` :143 (sim).
- `pick_generator._candidates_from_pipeline` :401 (reads sim, writes shadow).
- `supabase_client.store_real_bet` :5729/:5767 (probability lookup and FK routing across both ledgers).
- `placement_gate.ui_place_enabled_bots` :89 (`coolbet_placer_bots`, PAUSED callers).
- `coolbet_control.placement_readiness` :216 (08:00 summary).

**Operator paths read the wrong ledger.** The signaler, prekickoff alert and `place_bet_by_id` read `simulated_bets` only, while every real-money-capable bot writes `shadow_bets`.

**Publishing and public figures**
- `settlement.write_dashboard_cache` :3289–3639 (sim ⨝ bots → `dashboard_cache` → `/performance` hero).
- `scripts/export_track_record_snapshot.py:137` (GH 22:45 → `ledger/*.json`).
- `scripts/_our_stats.py:61` (GH daily → `ledger/comparison_*.json` → landing competitor table).
- `scripts/dump_oddsintel_picks_csv.py:64` (GH).
- `publish_daily_picks` (`published_picks`, not a ledger).

**Monitoring**
- `health_alerts` :102/:138/:371/:606/:916 (sim).
- `write_ops_snapshot` (sim :6114…, shadow :6141, user_picks :6418).
- `coolbet_daily_summary` :100–122 (sim, real).
- `results_check.run` :185.
- `settle_reconcile` :59/:88.
- `live_poller` :123.
- `book_price_fidelity` :109.
- `trigger_calibrator_check` (07:15).
- `weekly_bot_review` :188/:240/:287 (Sun).
- `threshold_check` (weekly).
- `observatory_metrics` :140 (02:15).
- `coolbet_feed_watchdog` :224 (Mac; not loaded).

**Training (sim only)**
- `fit_platt.py` :165/:210 (Wed and Sun).
- `fit_platt_live.py` :86.
- `train_b_ml3.py` :578 (Sun 04:00).
- `validate_meta_b_ml3.py` :228 (Sun 05:00).
- `aln1_tune_analysis.py` :53 (monthly).
- `settlement.compute_model_evaluations` :4395.
- `run_post_mortem` :4486.

`news_checker` rewrites `model_probability` after the pick, so the Platt labels read a news-adjusted value.

**CLV** (the side table has three sources): `clv_sharp.py` `_LEGS_SQL` :113/:123/:135 (01:40).

### 1B. `simulated_bets`: complete engine list

147 files. Of those, `scripts/smoke_test.py` accounts for 193 lines (see §1D). The others:

| file:line | R/W | columns | function | live? |
|---|---|---|---|---|
| workers/api_clients/supabase_client.py:2367 | W INSERT | bot_id, match_id, market, selection (canonicalised), odds_at_pick, pick_time, stake, model_probability, edge_percent (re-derived as cal−1/odds), result='pending', reasoning, model_version; optional: calibrated_prob, kelly_fraction, odds_at_open, odds_drift, dimension_scores, alignment_count/total/class, model_disagreement, news_impact_score, lineup_confirmed, timing_cohort, xg_source, recommended_bookmaker, meta_clv_score, match_minute_at_pick, score_home/away_at_pick, pin_cross_drift_shadow_flag. **Silently dropped:** strategy_profile, af_*_prob, af_agrees, implied_prob. | `store_bet` | LIVE |
| workers/jobs/daily_pipeline_v2.py:4071 | W via store_bet | | `run_morning` | LIVE |
| daily_pipeline_v2.py:5686 | R | id, bot_id, stake, market, selection, match_id (pending, today) | `_check_exposure_concentration` | LIVE |
| daily_pipeline_v2.py:5749 | R | result, pnl | `run_report` | CLI |
| workers/jobs/inplay_bot.py:391 / :874 | W / R | in-play fields | `run_inplay_strategies` | DORMANT (`INPLAY_STRATEGIES_ENABLED` off since 09-03) |
| workers/jobs/settlement.py:49 `_PENDING_BETS_SQL` (used at :1379, :2686) | R | id, bot_id, match_id, market, selection, stake, odds_at_pick, model_probability, edge_percent, result, pnl, clv, calibrated_prob, alignment_class, kelly_fraction, odds_drift, news_impact_score, reasoning, bankroll_after, closing_odds, pick_time, combo_*, recommended_bookmaker, odds_at_pick_live (the last two added by 2cf14433) | | LIVE |
| settlement.py:3965 | W | result, pnl, bankroll_after, closing_odds, clv, clv_pinnacle, clv_pinnacle_live, closing_bookmaker | `_settle_pending_bets` | LIVE (21:00/23:30/01:00, settle_ready */15, LivePoller) |
| settlement.py:2204 | W | result='void', pnl=0, void_reason='postponed' | `fix_stale_live_matches` | LIVE |
| settlement.py:2402/2431 | R+W | void_reason='no_ht_score' | `void_ungradeable_1h_bets` (`{table}` loop) | LIVE |
| settlement.py:2495/2574 | R+W | result, pnl, closing_odds, clv, closing_bookmaker, void_reason=NULL (skips `quarantine%`) | `resettle_wrongly_voided_bets` | LIVE |
| settlement.py:4768 | W | result='void', pnl=0, reasoning+='CLV-AUTOVOID' | `_apply_clv_autovoid` | LIVE |
| settlement.py:3289, 3310, 3319–3348, 3373, 3408, 3467, 3528, 3606, 3639 | R | bot_id, result, stake, pnl, odds_at_pick, odds_at_pick_live, combo_legs, clv, pick_time, market, alignment_class | `write_dashboard_cache` (+ `_value_bets_*`) | LIVE (:15/:45) |
| settlement.py:4395 / 4486 / 4714 | R | | `compute_model_evaluations` / `run_post_mortem` / `run_report` | LIVE / LIVE / CLI |
| workers/jobs/results_check.py:114/120/185 | R+W | result, pnl (+ bankroll) | `_regrade`, `run` | LIVE (2 h) |
| scripts/match_status_sweeper.py:97 | W | void postponed | | LIVE (GH */30) |
| scripts/settle_reconcile.py:59/88 | R | | | LIVE 21:30 |
| workers/live_poller.py:123 | R | match_id pending | `_refresh_active_bets` | LIVE |
| workers/jobs/clv_sharp.py:123–133 | R | id, match_id, market, selection, COALESCE(odds_at_pick_live, odds_at_pick), recommended_bookmaker, result | `_LEGS_SQL` | LIVE 01:40 |
| scripts/backfill_odds_at_pick_live.py:68/128 | R+W | odds_at_pick_live | `apply_backfill` | LIVE (*/30) |
| workers/automation/coolbet_signaler.py:144 / 422 / 532 | R / W / W | candidates; signaled_at; signal_message_id | | LIVE (after every betting run) |
| scripts/export_track_record_snapshot.py:137 | R | id, match_id, created_at, market, selection, odds_at_pick, odds_at_pick_live, recommended_bookmaker, stake, pnl, combo_legs, result, closing_odds, clv, clv_pinnacle (calibrated bots only) | | LIVE (GH 22:45) |
| scripts/_our_stats.py:61 | R | result, odds_at_pick, odds_at_pick_live, stake, pnl, created_at, market (calibrated/beta/active, **no btts**) | | LIVE (GH 02:00) |
| scripts/dump_oddsintel_picks_csv.py:64 | R | | | LIVE (GH) |
| scripts/backtest_day_ahead_picks.py:112 | R | | | GH daily, date-gated (dormant) |
| workers/automation/coolbet_placer.py:477 / 710 / 2648 / 2925 | R | id, match_id, market, selection, odds_at_pick, edge_percent, calibrated_prob, model_probability, kelly_fraction, stake, bot_id, combo_* (+ pick_time) | `load_qualified_*`, `place_bet_by_id` | filter branch LIVE (manual queue); auto branch (:506, :532, :560, :605, :732, :2682) DORMANT |
| workers/automation/pick_generator.py:401 | R | match_id, market, selection, calibrated_prob, model_probability, edge_percent | `_candidates_from_pipeline` | LIVE |
| supabase_client.py:5729/5760 | R | calibrated_prob, model_probability; existence | `store_real_bet` | LIVE |
| supabase_client.py:6114, 6162, 6200 | R | | `write_ops_snapshot` | LIVE |
| supabase_client.py:3490 / 3504 / 3578 | W / R / R | | `settle_bet`, `get_pending_bets`, `get_bot_performance` | DEAD |
| workers/jobs/health_alerts.py:102, 138, 371, 606, 916/974 | R | | `check_morning_bets`, `check_track_record_continuity`, `check_settlement`, `check_meta_score_drift`, `check_signal_silence` | LIVE |
| workers/jobs/coolbet_prekickoff_alert.py:143 | R | | | LIVE */5 |
| workers/jobs/coolbet_daily_summary.py:122 | R | | | LIVE 08:00 |
| workers/jobs/news_checker.py:208 / 338 | R / **W** | W: reasoning, model_probability, news_triggered, news_impact_score | | LIVE (5×/day) |
| workers/jobs/coolbet_daemon_healthcheck.py:147 | R | | | DEAD (de-registered) |
| workers/automation/coolbet_mac_daemon.py:403/441 | R / W user_placed_at | | | RETIRED |
| scripts/place_coolbet_ui.py:537 | R | | reconcile | PAUSED |
| workers/automation/coolbet_explorer.py:1347 | R | | `--bets-only` | CLI |
| workers/jobs/weekly_digest.py:75; email_digest.py:129/165/205 | R | | | DEAD |
| scripts/threshold_check.py (12 sites) | R | still filters `market='o/u'` (the pre-canonical name) | | LIVE weekly |
| scripts/weekly_bot_review.py:188/489 | R | | | LIVE Sun |
| scripts/fit_platt.py:165/210; fit_platt_live.py:86; train_b_ml3.py:578; validate_meta_b_ml3.py:228; aln1_tune_analysis.py:53 | R | labels | | LIVE (weekly/monthly) |
| scripts/compute_line_velocity.py:119 | R | | backtest | CLI |
| scripts/export_bot_config.py | R | | phase-1 export | LIVE (03:40, new today) |

**One-off writers of `simulated_bets`.** Each could still be run by hand, and each must be retired or re-pointed before the old table becomes read-only:
- `resettle_after_btts_fix`
- `backfill_simulated_clv_devig`: the **only** writer of `clv_pinnacle_devig`.
- `backfill_clv_pinnacle`
- `backfill_clv_pinnacle_devig`
- `backfill_clv_ou_line_fix`
- `backfill_shadow_direct_book_clv --table simulated_bets`
- `backfill_canonicalize_vocab`: DELETEs.
- `cleanup_match_dupes`: DELETE and re-point.
- `cleanup_ou_bets_after_quality_fix`: DELETEs.
- `cleanup_ou_bets_unvoid_inplay`
- `cleanup_ou_pinnacle_cap`: DELETEs.
- `normalize_inplay_stake_to_5`: made the backup table `simulated_bets_pre_inplay_normalize_2026_05_17` (457 rows).
- `resettle_wrongly_voided_bets`
- `funnel_diagnostic`, `probe_betting_slow`, `recover_today`: these call `run_morning`, so they insert.

**One-off readers.** About 80 analysis, backtest and audit scripts (list in the genesis notes). They would break after the drop and should be re-pointed at `picks`, or left to fail loudly.

### 1C. `shadow_bets`, `picks_forward_test`, `real_bets`, `user_picks`, `coolbet_placer_bots`, `bots`: remaining engine lines

Section 1A already covers every LIVE writer. The LIVE readers not listed there:

| file:line | table | R | function | live? |
|---|---|---|---|---|
| settlement.py:77 `_PENDING_SHADOW_BETS_SQL` (used :1407, :2286, :2833) | shadow_bets | id, bot_id, match_id, market, selection, stake, odds_at_pick, model_probability, edge_percent, result, closing_odds, pick_time, shadow_cohort, timing_cohort, recommended_bookmaker (excludes corners_ou_%) | settle | LIVE |
| settlement.py:1733 | picks_forward_test | outcome IS NULL and match finished | settle | LIVE |
| settlement.py:1594 / 2024 / 1664 | real_bets | singles / combos / dead matches | settle | LIVE |
| publish_picks_forward_test.py:560 / 598 / 660 | picks_forward_test | dedup / daily 60 breaker / board keys | publish | LIVE |
| first_half_1x2_paper_bot.py:165 | shadow_bets (+ price_verifications) | | `verify_epicbet_picks` | LIVE |
| corners/team_total/fh `report` (:316/:327/:224/:265) | shadow_bets | | report | CLI |
| coolbet_placer.py:2908, 491/554/586/721/2665 | real_bets | dedup | | on demand / DORMANT |
| coolbet_signaler.py:103; coolbet_prekickoff_alert.py:121 | real_bets ⨝ bots | already placed | | LIVE |
| weekly_bot_review.py:287 | real_bets | | | LIVE Sun |
| daily_pipeline_v2.py:2701, 4389, 4665 | bots | bankroll, is_active, id-by-name | | LIVE |
| pick_generator.py:151; pick_trigger_matcher.py:99; inplay_collector.py:238 | bots | id WHERE name AND retired_at IS NULL | `_bot_id` | LIVE |
| settlement.py:3804 | bots | id, name, current_bankroll | | LIVE |
| health_alerts.py:661 | bots | | `check_stale_retirement_flags` | LIVE |
| supabase_client.py:6103/6128/6199 | bots | | ops snapshot | LIVE |
| `_our_stats`, `export_track_record_snapshot`, `train_b_ml3`, `validate_meta_b_ml3`, `weekly_bot_review:168`, `trigger_calibrator_check` | bots | name, maturity_label | | LIVE |

**One-off writers**

| table | scripts |
|---|---|
| shadow_bets | `backfill_canonicalize_vocab` (DELETE), `backfill_clv_ou_line_fix`, `backfill_shadow_clv_margin`, `backfill_shadow_clv_pinnacle`, `backfill_shadow_direct_book_clv`, `void_phantom_sharp_picks` (quarantine), `resettle_wrongly_voided_bets` |
| picks_forward_test | `backfill_consensus_grades` |
| real_bets | `place_coolbet_bets` (CLI placer), `reconcile_placed_attempts_to_real_bets`, `backfill_real_bets_clv`, `backfill_real_bets_clv_edge`, `backfill_real_bets_direct_clv`, `backfill_real_bets_edge_formula`, `backfill_canonicalize_vocab` |
| bots | six cleanup scripts (`current_bankroll` only) |

**One-off readers of `shadow_bets*`.** About 29 scripts, including audit_mirrored_1x2, bot_status_board, executable_shadow_eval, promotion_gate_simulation, own_* and ops/status.

### 1D. Web (`odds-intel-web/src`), smoke tests, workflows, ledger export

**Public figures derived from these tables. These are the migration's riskiest readers.**

| file:line | table | what it feeds | notes |
|---|---|---|---|
| lib/engine-data.ts:1594 `getCalibratedHeadlineStats` | simulated_bets ⨝ bots!inner | **/performance headline ROI** (constants also used by `/api/v1/track-record`) | maturity in (calibrated, beta, active), not inplay_%, markets 1x2 / o/u / over_under_25 / btts, won/lost, since 2026-05-04 |
| app/api/v1/track-record/route.ts:121 / :157 | simulated_bets ⨝ bots | **public API plus the landing headline** (`app/page.tsx:63`) | same cohort, paged, with an exact count |
| engine-data.ts:420 `getAllBets` | simulated_bets (+ bot, match embeds) | /performance leaderboard and history; /admin/bots | every row, paged |
| engine-data.ts:384 `getAllBotsFromDB` | bots | /performance, /admin/bots | |
| engine-data.ts:1525 `getPublicCohortBotNames` | bots | /performance | |
| engine-data.ts:2151 `getPublicPerformanceExtras` | simulated_bets | /performance calibration, streaks, per-bot recent ROI | last 90 days |
| engine-data.ts:2323 `getModelV2Stats` | simulated_bets | /performance | model_version = 'v20260524_market' |
| engine-data.ts:1939 `getRecentSettledBets` | simulated_bets | /performance teaser for anonymous visitors | **no cohort filter**, so it can show retired and in-play bots |
| engine-data.ts:1370 `getTrackRecordStats` | simulated_bets | /performance hero | fallback when `dashboard_cache` is empty |
| engine-data.ts:2498 / 2585 | picks_forward_test_summary(_by_market) / picks_forward_test_public | /performance forward-test rows | |
| lib/forward-test-picks.ts:262 `fetchPublicPicks` | picks_public_all | **/picks** (page.tsx:380), `/api/v1/upcoming` :94 | |
| forward-test-picks.ts:338 | picks_board_public | /picks watchlist | not a ledger |
| `dashboard_cache` (engine-built from sim) | — | /performance hero via `getDashboardCache` | |
| `ledger/comparison_*.json` (from `_our_stats`) | — | landing competitor table (`app/page.tsx:181`, fetched from GitHub raw) | |

**The public cohort from `simulated_bets` is defined five ways.** Pick one before the reader switch:
1. headline and API: calibrated/beta/active, 4 markets.
2. `_our_stats`: the same without btts.
3. `export_track_record_snapshot`: calibrated only.
4. `getRecentSettledBets`: no filter.
5. `getModelV2Stats`: version-filtered, maturity filtered after the fetch.

**Admin readers and writers**

| file:line | table | R/W | page |
|---|---|---|---|
| engine-data.ts:711 / 784 / 798 / 950 | sim R; sim W `admin_offered_at`; real_bets R; sim R | R/W | /admin/place (route exists but is unlinked) |
| engine-data.ts:1223 `getRealBets` | real_bets (+ bots, `paper:simulated_bet_id`) | R | /admin/real-bets |
| engine-data.ts:2068 `getStalePendingBets` | sim | R | /admin/ops |
| lib/shadow-bots/queries.ts:217 / 228 / 230 / 240 / 250 / 447 | bots; coolbet_placer_bots; real_bets (today); **shadow_bets_unique** ×2 (pending future; in-play); shadow_bot_scoreboard | R | /admin/shadow-bots |
| app/(app)/admin/shadow-bots/[bot]/page.tsx:283 / 304 / 475 | bots; shadow_bets_unique (≤5000); real_bets | R | /admin/shadow-bots/[bot] |
| lib/upcoming-picks.ts:308 | user_pick_marks (pick_id = shadow id) | R | /admin/shadow-bots |
| app/api/me/pick-marks/route.ts:33 / 74 / 81 | user_pick_marks | R/W | pick-bet-mark component |
| app/api/admin/bot-book-odds/route.ts:67 | sim | R | /admin/bots |
| app/api/admin/coolbet-placer-bots/route.ts:56 / 89 | coolbet_placer_bots | R (dead GET) / **W** toggle | /admin/shadow-bots |
| app/api/admin/real-bet/route.ts:87 | real_bets via `record_manual_real_bet` | W | /admin/place, shadow-bots place-action |
| app/api/admin/record-combo/route.ts:83 | real_bets INSERT (simulated_bet_id) | W | /admin/place |
| app/api/telegram/webhook/route.ts:139 / 365 / 375 / 522 | real_bets R; sim R; **sim W user_placed_at/user_skipped_at on all sibling rows**; real_bets R (dedup) | R/W | operator Telegram |
| app/(app)/admin/cs2/page.tsx:217 | bots (`bot_cs2_%`) | R | unlinked |
| lib/bot-board.ts:147–188, app/api/admin/bot-ledger | bot_scoreboard / bot_config / bot_capabilities / bot_ledger | R | new #139 phase-1 work (in progress) |

**Dead web code:** `fetchUpcomingPicks` (upcoming-picks.ts:226, sim), `fetchForwardTestSummary` (forward-test-picks.ts:369), `components/picks-forward-test-panel.tsx`, and the GET handler of coolbet-placer-bots. The web app never references `user_picks`.

**Smoke tests (`scripts/smoke_test.py`).**
- **231 of about 1,215 tests** name one of these tables.
- **130 would break or need edits on a rename.** In these the name is in SQL, a source-inspection string or a file read.
- 100 mention the table in prose only.
- Tests that pin the **table name in executable SQL or source strings**, which must change in the reader-switch step:
  - **settlement and CLV:** SHADOW-SETTLE-WIRED 3803, QUARANTINE-VOIDS-SURVIVE-THE-RESETTLER 4603, SETTLE-VOID-POSTPONED 13383, SIMULATED-CLV-OWN-BOOK 45446, CLV-ALWAYS-OWN-BOOK 45510, CLV-PINNACLE-ONE-DEFINITION 36979, SETTLEMENT-REGISTRY 37176, REAL-BETS-SETTLE-ANY-FINISHED 47819, MATCH-STATUS-SWEEPER 27076, BET-VOID-INTEGRITY 27535, SHADOW-CLV-NO-ARBITRARY-FALLBACK 43829, THIN-CONSENSUS-CLV 53039.
  - **public surfaces:** PUBLISHED-ARM-HAS-A-RECORD 24741, PICKS-FORWARD-TEST-SURFACE 43142, PICKS-FORWARD-TEST-SETTLED 42939, PICKS-FORWARD-TEST-BOT-NOT-IN-BET-LEDGERS 43434, PERF-PUBLIC-IS-CALIBRATED-OR-BETA 44526, PICKS-SHOW-BOTH-BOTS 44671, PICKS-COHORT-ALIGN 26178, LANDING-PERF-ROI-BASIS 32602, LEDGER-EXEC-PRICE-BASIS 34188, ANON-LEAST-PRIVILEGE 53668, PICKS-BOARD-SETTLEMENT 44806, PICKS-BOARD-WATCHLIST 44919, SHARP-BOT-SPLIT-BY-MARKET 49735, PERF-HONEST-HEADLINE-ACTIVE-FIELDS 11909, OWN-BOTS-OFF-CUSTOMER-SURFACES 47835.
  - **placement:** COOLBET-OWN-BETTING-ARCH 5736, REAL-BETS-SHADOW-LINK 47890, MANUAL-REAL-BET-ATOMIC 53575, ROUTER-REAL-MONEY-CUTOVER 38354, COOLBET-PREKICKOFF-CATCHNET 8826, SELF-USE-VALIDATION 789/840, SETTLEMENT-POSTPONED-VOID 965 (inserts `real_bets` fixtures), TELEGRAM-EDGE-UNITS 49947.
  - **shadow views:** SHADOW-VIEW-COLUMN-DRIFT 30920, SHADOW-RETIRED-INVISIBLE 31647, CLV-GATE 32165, SHADOW-PAGE-ROI-INFLATED 32441, SHADOW-BOTS-DETAIL-TRUNCATION 30538, SHADOW-BOTS-ROI-OVER-ALL-SETTLED 48706, SHADOW-CLV-MARGIN-CORRECTED 47943, SHADOW-BETS-AGGREGATES-USE-UNIQUE 48997, SHADOW-EVAL-DEDUP 40133, OWN-SEGMENT-SEARCH-METHOD-GUARDS 45634, BOT-GATE-REACHABLE 21395, SHADOW-PICKS-ATTRIBUTABLE 46403, SHADOW-CLOSE-BOUNDED 24463.
  - **writers:** COOLBET-MODEL-{OU,1X2}-SHADOW 5457/5601, CORNERS-PAPER-FORWARD 37076, SHARP-TIGHT-FRESHNESS-REFUSES-STALE 47911, EPICBET-1H-PRICE-VERIFY 52217, MARKET-VOCAB-ENFORCED 37819, EDGE-PCT-TAKEN-RECORDED 37975, EDGE-IS-DERIVED-NOT-STORED 50130, EDGE-UNIT-COLLISION 34464.
  - **bots:** SYSTEM-MAP-REGISTRY-NOT-DRIFTED 5163, BOT-BANKROLL-DRIFT 10789, BOT-AGGREGATES-SSOT 10838, BOT-MATURITY-UNEARNED 35793, MATURITY-LABEL-CANONICAL 45757, RETIRED-BOTS-KEPT-GENERATING 42073, EVERY-REGISTRY-BOT-IS-VISIBLE 41289, RETIRED-BOT-LEAK-FIX 25462 (inserts `zz_smoke_*` bots and sim rows), COOLBET-PLACER-CONTROL 5996, OU-CALIBRATOR-DOMAIN-MISMATCH 41950.
  - **training and meta:** B-ML3-BETS-MODE 22311, META-MFV-HOME-ONLY 33755, META-MODEL-CLV-TARGET 27830, CLV-DEVIG-STALE-PRICE 32860, OU-LIVE-PRICE-BLIND 31570, INPLAY-EDGE 2694, INPLAY-O-QUARANTINE 21042.
  - **Migration-source tests** (they assert on old migration text and stay valid): the BOTS-RETIRE-* family, SIM-BETS-COHORT-CHECK, COMBO-RESTRUCTURE-*, REAL-BETS-CLV-EDGE-SCHEMA and similar.
- The full classified list is in the scratch output of this inventory (`smoke_lb.md` / `smoke_prose.md`). Regenerate it at step 5d rather than trusting line numbers, which move.

**GitHub workflows**

| workflow | schedule | touches |
|---|---|---|
| `match_status_sweeper.yml` | */30 | **writes** sim and shadow (void postponed). This writer lives **outside the VPS scheduler**, so it is easy to miss in a dual-write. |
| `track_record_ledger.yml` | 22:45 | `export_track_record_snapshot.py` → commits `ledger/YYYY-MM-DD.json`, `latest.json`, `index.json` and OpenTimestamps `.ots` files. **This is a signed public record, so its row identity and fields must not change silently.** |
| `competitor_audits_weekly.yml` | daily 02:00 | `_our_stats.py`, `dump_oddsintel_picks_csv.py` → `ledger/comparison_*.json`, `picks_*.csv`; `update_frontend_comp_fallback.py` rewrites `COMP_FALLBACK` in the web `page.tsx` |
| `day_ahead_backtest_rerun.yml` | 03:00 | reads sim (date-gated) |
| `smoke_tests.yml`, `deploy.yml`, `migrate.yml` | push | smoke suite, deploy, migrations |

`deploy/vps/backup-oddsintel.sh` dumps the whole database, so a table rename does not affect it. There are no grant scripts outside the migrations.

---

## 2. Predictions side

### 2.1 Tables holding model outputs, probabilities or calibration

| table | rows | size | grain / key | writes | status |
|---|---|---|---|---|---|
| `predictions` | ~1.10 M | 437 MB | UNIQUE (match_id, market, source, model_version); **upsert overwrites within a version**; no `updated_at` (6,134 inserts against 204,468 updates since the stats reset) | `bulk_store_predictions` (supabase_client.py:1173), from `daily_pipeline_v2` :3308 poisson, :3336 xgb, :3360 xgb shadow, :3434 ensemble, :3452 ensemble shadow, :3476 poisson AH (flushed :4175); `fetch_predictions.py:102` (source `af`); `write_national_team_predictions.py:217` | LIVE, main store |
| `matches.af_prediction` (jsonb) | 46,162 filled | inside `matches` | one per match | `bulk_update_match_af_predictions` :1232 | LIVE, **no reader** |
| `published_picks` | 50,050 | 21 MB | UNIQUE (match_id, market, model_version), DO NOTHING (frozen first value) | `publish_daily_picks.py:146`; settled at :226 via settlement :1430 | LIVE; really a pick table (model argmax), not a bot ledger; no web reader |
| `pick_triggers` | 1,971 | 4.3 MB | UNIQUE (match_id, market, selection, strategy), upsert; rows deleted once kickoff is more than a day old | `pick_triggers.py:306/410` | LIVE; the trigger bots' decision object |
| `book_fair_probs` | 20,210 | 8 MB | per (match, book, market, selection, line), upsert | `tonybet_feed.py:358` | LIVE, **no reader** |
| `model_calibration` | 1,808 | 0.6 MB | append-only; **no `model_version`**; five parameter kinds told apart by the `market` string (Platt, blend_weight, shrinkage_alpha, dc_rho, in-play) | `fit_platt`, `fit_blend_weights`, `fit_league_rho`, `fit_platt_live` | LIVE; web reads it at engine-data.ts:946 |
| `model_evaluations` | 603 | 0.5 MB | per date × market (delete then insert); mixes `1x2` and `1X2`, and `O/U` and `over_under_25` | `settlement.compute_model_evaluations` / post_mortem | LIVE |
| `model_versions` | 51 | 0.2 MB | PK version; `promoted_at` / `demoted_at` (**5 rows marked promoted at once**; the real routing is in VPS env vars) | `storage.py:308`, `weekly_eval_and_compare.py:282` | LIVE |
| `match_feature_vectors` | 176,693 | 128 MB | one per match, upserted; holds **copies** of `ensemble/poisson/xgboost/af_pred_prob_home` and `model_disagreement` (`ensemble_prob_draw/away` are never written) | nightly MFV builder | LIVE, feature store |
| backups: `model_calibration_ou_domain_mismatch_backup` (12), `mfv_pre_elo_fix_backup` (49k), `mfv_…_early` (41k), `simulated_bets_pre_inplay_normalize_2026_05_17` (457) | | ~28 MB | | none | archive or drop |
| frozen or other sport: `wc_monte_carlo_results`, `wc_market_consensus`, `wc_group_predictions`, `lol_upcoming_matches`, `tennis_value_bets`, `feature_importance`, `team_roster_strength` | | | | none live | archive |
| ratings (features, not outputs): `team_elo_daily` (269k), `team_elo_international` (13k), `team_form_cache` (234k), `match_signals` (4.27 M EAV, 1.19 GB) | | | | LIVE | out of scope |

**Missing tables that live code writes to or reads from:**
- `prediction_snapshots` does not exist. `store_prediction_snapshot` is called from `daily_pipeline_v2.py:4137` and `news_checker.py:389/421`; the errors are swallowed.
- `cs2_predictions` is read by web `admin/cs2/page.tsx:243`.

**Broken live reader:** `health_alerts.check_model_drift` (:558) queries a `probability` column that doesn't exist, so it errors on every run.

**`predictions` readers (LIVE)**
- `pick_generator.py:544/622` and `pick_triggers.py:356`: `DISTINCT ON … ORDER BY model_version DESC` **with no source filter**. For today's upcoming 1x2 this picked 21 AF rows and 4 raw XGBoost rows as "the model".
- `pick_triggers.py:183`: the 1x2 isotonic fit uses **every source and every version**.
- `ou35_model_shadow.py:55/110`.
- `daily_pipeline_v2.py:2516/4441/4770`.
- MFV builder, `supabase_client.py:1537/1549`: which ensemble row wins is not deterministic.
- `settlement.py:3626`: dashboard accuracy; an ensemble join with no version filter fans out on 2,491 matches.
- `inplay_bot.py:822`, `match_previews.py:75`, `publish_daily_picks.py:77`.
- `fit_platt.py:63`: filtered to `$MODEL_VERSION = v20260712`, whose 1x2 rows stopped on 08-26. **The nightly 1x2 Platt refit is being fitted on a model that no longer scores 1x2.**
- `fit_blend_weights.py:129`, `threshold_check.py`.
- `supabase_client.py:6043` (ops).

The web app never reads `predictions` directly.

### 2.2 Model version tags on bets do not join back to `predictions`

Live routing is per market:

| market | version tag | env var |
|---|---|---|
| 1x2 | `v20260830` | `MODEL_VERSION_1X2` |
| O/U 2.5 | `v20260903_cut0820` | `MODEL_VERSION_OU` |
| O/U 1.5 / 3.5, BTTS | `v20260712` | global `MODEL_VERSION` |
| shadow A/B | `v20260705` | `SHADOW_MODEL_VERSION` (pinned) |

`shadow_bets.model_version` stores the global tag, **even on 1x2 bets**. Only 333 of 14,110 recent 1x2 shadow bets tagged `v20260712` have a matching `predictions` row.

Other tables store free-text "versions" that are not model bundles at all: `+selcal1`, `pinnacle_shin_devig+selcal1`, `corners_paper_devig_v1`, the forward test's `rule_version`. So `model_version` on a pick is **not a usable foreign key** today.

### 2.3 Same information in several tables: candidate unifications

| information | where it lives now | verdict |
|---|---|---|
| Our model's probability for (match, market, selection) | `predictions` (latest per version, overwritten); MFV `*_prob_home` (copy); `published_picks.model_probability` (frozen); `pick_triggers.cal_prob` (calibrated, latest); `simulated_bets` and `shadow_bets` `.model_probability` / `.calibrated_prob` (value at pick time, sometimes overwritten by `news_checker`); `candidate_funnel.raw_prob` | **Unify**: an append-only prediction history (add `computed_at` and stop overwriting, or add a `prediction_runs` table). `picks` stores `prediction_id` plus `calibrator_id`, alongside **a frozen copy** of the probability used, because the forward test must stay self-contained. The MFV columns become a join. |
| Sharp (Pinnacle Shin de-vig) fair probability | Never stored as its own row. Copies in `pick_triggers` (sharp), `picks_forward_test.p_sharp`, `picks_board.p_sharp`, `shadow_bets.calibrated_prob` (sharp bots), `leg_clv_sharp.p_close*`, `match_signals`, MFV | Same shape as the model probability. Put it in the same history as `source='sharp_devig'` with a method column. The copy on the pick stays (it is the decision value). |
| Book fair probability | `book_fair_probs` (Tonybet; no reader); paper-bot de-vig in `shadow_bets.calibrated_prob` | Same history, `source='book_fair:<book>'`, or drop `book_fair_probs` since nothing reads it. |
| API-Football prediction | `matches.af_prediction` (raw), `predictions` source `af` (**tagged with our model version**), MFV `af_pred_prob_home`, sim `af_*_prob` (**silently dropped** by `store_bet`) | Keep as `source='api_football'` with its own version; stop tagging it with ours. |
| Calibration state | `model_calibration` (no version; mixed parameter kinds); pick-trigger isotonic (in memory only); OU logistic | Unify into a versioned calibrator registry (`model_version`, market, `param_kind`, revision). |
| Model and rule identity | `model_versions`, free text in 5 tables, `rule_version` | One "generator" registry (model bundle, de-vig method, forward-test rule), referenced by an FK from `picks`. |
| Evaluation | `model_evaluations`, `model_versions.cv_metrics`, `model_calibration.ece_*` | Normalise the names; share the version FK. Low priority. |

**Genuinely different; keep separate**
- `predictions` vs `match_feature_vectors`: outputs vs inputs and labels, different grain.
- `pick_triggers`: a decision object with an odds window and floors. It belongs to the **picks** domain and should reference a prediction.
- `published_picks`: a per-market argmax pick with no bot and no stake. It is a fourth "pick" concept. **Decision for the owner:** either fold it into `picks` as bot `model_argmax_published`, or leave it alone. It feeds nothing on the web today, so leaving it costs nothing.
- `picks_board`: a display watchlist, replaced every 30 minutes. Keep it separate. It must **never** be read as a ledger; smoke PICKS-BOARD-SETTLEMENT already pins that.
- Ratings, signals, and the WC/LoL/tennis tables: out of scope.

**Scope recommendation.** Phase 5 should unify the **bet ledgers first** and treat the predictions work as a separate sub-epic (5P), with one prerequisite: `picks` gets a nullable `prediction_id` from the start, filled once prediction history exists. Merging `predictions` (437 MB, about 200k updates a day) in the same change would double the blast radius for no reader-visible gain.

---

## 3. Column mapping onto the unified `picks` schema

The proposed `picks` schema combines the migration 410 `bot_ledger` columns with the design's extras (stake, probability, edge, anchor_*, quote_age_min, cohort, immutable).

Symbols: → maps to · ✚ needs a new column · ✗ dropped (reason) · ⚠ semantic mismatch.

### 3.1 Identity and decision

| picks column | simulated_bets | shadow_bets | picks_forward_test | notes |
|---|---|---|---|---|
| `id` uuid PK | id | id | id | **Keep the source UUIDs** (no collisions: all gen_random_uuid). Then `real_bets.*_bet_id`, `leg_clv_sharp.leg_id`, `user_pick_marks.pick_id`, `coolbet_placement_attempts.*_bet_id`, `bet_telegram_alerts` and `price_verifications` stay valid with no remap. |
| ✚ `source_ledger` text | 'sim' | 'shadow' | 'forward_test' | provenance; also makes `leg_clv_sharp.ledger` derivable |
| `bot_id` uuid → bots | bot_id | bot_id | ✚ **derived** from arm/grade/market (see §1.5), stored once | ⚠ three views derive this differently today. Give `control_junk_anchor` and `consensus_ungraded` real `bots` rows. |
| `match_id` | match_id | match_id | match_id | FK ON DELETE: **not CASCADE** (see risk R7) |
| `market`, `selection` | ✓ | ✓ | ✓ | same vocabulary |
| `pick_time` | pick_time | pick_time | published_at | ⚠ sim `pick_time` is written as a **naive** `datetime.now()` string. Confirm the time zone before comparing with shadow and forward-test timestamptz values. |
| `created_at` | created_at | created_at | published_at | |
| ✗ `kickoff` | — | — | kickoff_at | derive from `matches.date`; the forward test's `kickoff_at` copy is kept in `decision_snapshot` (below) |
| `odds` (decision price) | odds_at_pick | odds_at_pick ⚠ | odds | ⚠ on shadow **upsert** rows (pick_generator, trigger matcher, ou35) `odds_at_pick` is overwritten on every re-run, so it holds the **latest**, not the first, decision price |
| ✚ `odds_exec` | odds_at_pick_live | odds_at_pick_live | — (= odds) | the public ROI basis (`_EXEC_PNL`, ledger export) |
| `bookmaker` | recommended_bookmaker | recommended_bookmaker (NULL for ou35) | bookmaker | |
| ✚ `stake` | stake (Kelly €) | 10.00 flat | ✚ 1 | ⚠ three stake schemes. Keep `stake` native **and** add `stake_scheme` ('kelly_bankroll', 'flat10', 'unit'). `pnl_unit` is computed. |
| ✚ `probability_raw` | model_probability ⚠ | model_probability | NULL | ⚠ `news_checker` overwrites the sim value after the pick. Freeze it at insert (see §3.4). |
| `probability` (decision / fair) | calibrated_prob | calibrated_prob ⚠ | p_sharp | ⚠ the meaning depends on the family: ensemble+Platt / isotonic `selcal1` / Pinnacle Shin / book de-vig / in-play book prob. ✚ `prob_source` text. |
| `edge` | edge_percent (fraction; re-derived as cal−1/odds by `store_bet`) | edge_percent | edge | ⚠ units: check each writer is a fraction, not a percent (smoke EDGE-UNIT-COLLISION) |
| ✚ `kelly_fraction` | ✓ | ✓ | — | |
| `model_version` | ✓ | ✓ ⚠ | — | ⚠ free text, not an FK (§2.2) |
| `rule_version` | — | — | rule_version | 410 folds `model_version` and `rule_version` into one column. **Keep them separate** in `picks`. |
| ✚ `cohort` | timing_cohort | shadow_cohort | arm | ⚠ **overloaded**. Split it: `cohort` = the writer family (shadow_cohort, or the arm for the forward test); ✚ `timing_slot` = HHMM for re-evaluation copies; `timing_cohort` (the bot's *assigned* window) is dropped, since every bot is now 'all'. |
| ✚ `is_primary` bool | true | false for HHMM rows whose key also has a base row **and** for every shadow row of a sim-writing bot; true otherwise | true | Replaces "ledger = one table". Unique key: `(bot_id, match_id, market, selection) WHERE is_primary`, plus `(bot_id, match_id, market, selection, cohort, timing_slot)` for all rows. **Resolves the sim/shadow double-record.** |
| ✚ `arm`, `grade`, `grade_reasons` | — | — | ✓ | forward-test and consensus only |
| ✚ `anchor_bookmaker`, `anchor_odds` jsonb, `anchor_overround`, `anchor_quoted_at`, `odds_quoted_at`, `alignment_gap_minutes` | — | pair_gap_hours (~ alignment gap, 2,477 rows) | ✓ | |
| `quote_age_min` | — | decision_quote_age_min (645 rows) | derived: published_at − odds_quoted_at | |
| `is_inplay` | ⚠ `match_minute_at_pick IS NOT NULL` misses **865 rows**; use minute OR xg_source OR `inplay_%` (410 already does) | inplay_minute IS NOT NULL | false | store it, don't derive it |
| ✚ `inplay_minute`, `inplay_score_home/away` | match_minute_at_pick, score_*_at_pick | inplay_* | — | |
| `telegram_message_id` | signal_message_id | — | telegram_message_id | ⚠ different channels: the operator signal vs the public post. Name them `operator_signal_msg_id` / `public_post_msg_id`. |
| ✚ `immutable` bool | false | false | **true** | trigger enforcement: see §3.4 |
| ✚ `meta_clv_score` | ✓ (964 rows) | ✓ (19k) | — | ⚠ no meta-model version is stored |
| ✚ `prediction_id` (nullable) | — | — | — | for the future 5P |

### 3.2 Settlement and CLV

| picks column | simulated_bets | shadow_bets | picks_forward_test | notes |
|---|---|---|---|---|
| `result` | result (enum) | result (enum) | COALESCE(outcome, 'pending') | ✚ new enum {pending, won, lost, void, push, half_won, half_lost}. ⚠ sim and shadow store push as void. Sim quarter-line half results live only in pnl. |
| `pnl` (native stake) | pnl ⚠ | pnl ⚠ | pnl (units) | ⚠ 182 sim quarantine voids keep a non-zero pnl; 410 shadow voids have NULL pnl. The backfill must **normalise void → 0** or reconciliation sums will differ. Decide explicitly and record it. |
| `pnl_unit` (generated) | — | — | — | won → odds−1 (on which odds? `odds` or `odds_exec`: pick one, see R3), lost → −1, else 0 |
| `settled_at` | settled_at (7 rows; trigger) | ✗ none, ✚ backfill impossible (use created_at of the settle run: unknown) | settled_at | ⚠ shadow has no settle timestamp; leave it NULL for history |
| `void_reason` | ✓ | ✓ | — (void via outcome) | protected prefix 'quarantine' (resettle and regrade skip it) |
| `closing_odds`, `closing_bookmaker`, `closing_minutes_before_ko` | ✓ | ✓ | closing_odds, closing_bookmaker | |
| ✚ `closing_margin` | — | ✓ | — (computed in settlement, not stored) | |
| `clv_raw` | clv | clv | clv | own-book raw ratio |
| ✚ `clv_live` | clv_live | clv_live | — | |
| `clv_mc` | — | clv_margin_corrected (43,761 rows) | clv_margin_corrected | sim has none; could be computed in the backfill (needs closing_margin) |
| `clv_pinnacle` | ⚠ **two columns**: `clv_pinnacle` (live writer) and `clv_pinnacle_devig` (one-off only; 2,186 rows disagree) | clv_pinnacle (de-vigged) | — | **Decision needed.** 410 uses `_devig` for sim. Pick the canonical one and recompute both in the backfill from `odds_snapshots`. |
| ✚ `clv_pinnacle_live` | ✓ | ✓ | — | |
| (side table) `leg_clv_sharp` | ledger='simulated_bets' | 'shadow_bets' | 'picks_forward_test' | keep it as a side table keyed on `picks.id`. Its `ledger` column becomes redundant (derive it from `source_ledger`). |
| ✗ `bankroll_after` | ✓ | — | — | a running per-bot figure computed at settle time; perf docs call it a "high-water" artefact (PERF-NO-HIGH-WATER-BANKROLL-COLUMN). Derive it with a window, don't store it. |

### 3.3 Dropped, or moved to `extra jsonb` (sim analytics with no counterpart)

| column | why |
|---|---|
| `news_triggered`, `news_impact_score`, `reasoning`, `ai_explanation(_at)`, `dimension_scores`, `alignment_count/total/class`, `model_disagreement`, `lineup_confirmed`, `odds_at_open`, `odds_drift`, `xg_source`, `pin_cross_drift_shadow_flag` | sim-pipeline-only analytics. Some have readers (`write_dashboard_cache` alignment_class; post_mortem; `aln1_tune_analysis`). **Move to `extra jsonb`** and keep the readers working through a view. |
| `af_home/draw/away_prob`, `af_agrees` | never written (`store_bet` drops them). ✗ drop. |
| `combo_legs`, `combo_size`, `system_type` | 0 rows in sim (combo bots retired); `real_bets` keeps its own. ✗ drop from `picks`. |
| `strategy_profile` | dropped by both insert paths except in-play (914 rows). Keep it in `extra`. |
| `shadow_run_id` | no table behind it; groups one run. Keep in `extra` or drop. |
| `signaled_at`, `user_placed_at`, `user_skipped_at`, `admin_offered_at` | operator workflow state, not pick facts. ✚ Move to a `pick_operator_state` table (or `extra`). They are **mutable**, and the Telegram webhook writes them on sibling rows. |
| forward test `anchor_*`, `grade_reasons`, `kickoff_at` | keep (§3.1). Also add ✚ `decision_snapshot jsonb` holding the verbatim original forward-test row, so the pre-registered record is reproducible byte for byte. |

**Timing-cohort duplicates, measured**
- Shadow HHMM copies: 156,956 rows, 14,941 keys.
- Keys that exist only as copies: 13,365. For these keys the copy **is** the pick.
- Keys with both a base row and copies: 1,576.
- Sim bots' shadow rows that duplicate a sim pick: e.g. 343 of `bot_v10_1x2`'s 403.

**Recommendation:** backfill **all** rows. Mark `is_primary` by the migration 410 rule: sim wins; otherwise the earliest row per key, whatever its cohort. Keep the rest with `is_primary=false` and a `timing_slot`. Nothing is lost, and readers filter `WHERE is_primary`.

The 157k copy rows cost about 100 MB. They could instead be moved to a cold `pick_reevaluations` table. **Owner decision:** keep the copies in `picks`, split them out, or stop writing them. Stopping writes for retired bots is already on #139's clean-up list.

### 3.4 Forward-test immutability: what the trigger must allow

| column group | today changed by | proposed rule |
|---|---|---|
| decision: match, market, selection, odds, bookmaker, edge, probability, anchor_*, odds_quoted_at, arm, rule_version, pick_time | migration 343 (rule_version relabel, day-1 junk) | **frozen**: the trigger rejects the UPDATE when `immutable` |
| grade, grade_reasons | migration 381 re-tier; `backfill_consensus_grades` | frozen once non-NULL (allow NULL → value exactly once) |
| telegram_message_id | `attach_message_id` after the claim | allow NULL → value once |
| outcome/result, pnl, closing_*, clv*, settled_at | settlement; **`results_check._regrade` clears and re-settles** | allowed, with an audit row written to a ✚ `pick_settlement_history` table on every change, so a regrade is visible |

The rule must also cover the **existing 760 rows**. Copy them verbatim into `decision_snapshot`, and have reconciliation compare `picks` against `picks_forward_test` field by field before `picks_forward_test_summary*` and `picks_public_all` are re-pointed.

### 3.5 `bots` and `coolbet_placer_bots` → extended `bots`

| target | source | notes |
|---|---|---|
| bots.* (15 columns) | bots | keep. `current_bankroll` / `starting_bankroll` are only meaningful for sim (Kelly). |
| ✚ `family` | `bot_config.family` (410, exported daily) | move it from the export into the row once writers stop being code constants |
| ✚ `can_publish` | `bots.show_on_picks` (5 rows true, 2 of them retired) plus the forward-test arms' implicit publish plus the Telegram sender | ⚠ today `show_on_picks` gates only the sim half of `picks_public_all`. The forward-test half is gated by arm. |
| ✚ `can_place_real` | `coolbet_placer_bots.ui_place_enabled` (2 rows, both false) ∩ the `PLACEABLE_BOTS` constant (`placement_gate.py`) | ⚠ `coolbet_placer_bots` is keyed by **name** and read by `placement_gate.py:89` (PAUSED callers) and `coolbet_control.py:216` (LIVE 08:00). The web toggle writes it. |
| ✚ `lifecycle` | `is_active`, `retired_at`, `maturity_label` | keep the `bots_set_retired_label` trigger |
| ✚ `config jsonb` + `bot_config_versions` | code constants (`BOTS_CONFIG`, `BotConfig` lists, `TRIGGER_CONFIGS`, `bot_registry.py`) plus `bot_config` (410) | reuse `bot_config_history` (16 rows) as the versions table |
| ✚ bot rows for `control_junk_anchor` and `consensus_ungraded` | — | so every pick has a `bot_id` |

`ensure_bots` (supabase_client.py:43) **re-creates any missing `BOTS_CONFIG` bot on every run**. Make it config-aware before `bots` becomes the source of truth.

### 3.6 `real_bets` and `user_picks`

- **`real_bets`:** add ✚ `pick_id → picks.id`, backfilled as `COALESCE(simulated_bet_id, shadow_bet_id)`; the UUIDs are kept, so this is a plain copy. The 35 unlinked rows stay NULL. Keep the two old columns as deprecated until every writer is switched: `store_real_bet`, `record_manual_real_bet`, `/api/admin/record-combo`, `place_coolbet_ui`. Change the unique index `real_bets_one_per_shadow_pick` to `(pick_id) WHERE pick_id IS NOT NULL`.
- **`user_picks`:** 6 rows from May, no writer anywhere, one settler. Its sibling concept `user_pick_marks` is live. **Recommendation:** retire `user_picks`, which means dropping the two settle functions. Out of scope for `picks`.

---

## 4. Code paths that must switch: a safe sequence

Guiding rules:
- **Writers dual-write before any reader moves.**
- **Admin readers move before public readers.**
- **Pre-registered and public-signed records move last**, and only after equality is proven.
- Each step ships with a smoke test and a reconciliation check (design § Review rule).

### Step 5b: create and backfill (no behaviour change)

1. Migration: the `picks` table, the result enum, indexes (primary partial unique; per-cohort unique; pending partial; (bot_id, pick_time); match_id), the `settled_at` trigger, the immutability trigger, `pick_settlement_history`, `pick_operator_state` (or `extra`), bot rows for the control and ungraded consensus. **No anon grant.**
2. Backfill from the three ledgers, keeping the UUIDs. Compute `is_primary`, `timing_slot`, `stake_scheme`, `prob_source`, `decision_snapshot`.
3. `bot_ledger_v2` view on `picks WHERE is_primary`, plus a **reconciliation query** against migration 410's `bot_ledger`: per bot, the counts, won/lost/void/pending, SUM(pnl_unit), and mean clv_raw, clv_mc and clv_pinnacle must match exactly. Also per-row equality on the forward test.

**Risks**
- R1: the pnl void normalisation (182 sim rows, 410 shadow rows) makes native P&L sums differ from the old tables unless it is handled explicitly.
- R2: the `clv_pinnacle` vs `_devig` choice for sim.
- R3: the `odds` vs `odds_exec` basis for `pnl_unit`. 410 uses the recorded decision price; the public figures use `odds_at_pick_live`.
- R4: sim's naive `pick_time` time zone.

**Estimate:** 1.5–2 days, including two review agents.

### Step 5c: dual-write, one writer per sub-step, daily reconciliation job

Order runs from lowest to highest blast radius. Each writer first writes the old table, then `picks` with the same UUID (`INSERT … ON CONFLICT (id) DO UPDATE`). A daily reconciliation job alerts on any drift.

| # | writer | why this position | risk |
|---|---|---|---|
| c1 | shadow **paper** writers: corners, team_total, fh, inplay_collector (systemd) | retired or experimental, no readers of consequence | inplay_collector deploys through a **separate systemd unit**; restart it too |
| c2 | shadow **upsert** writers: pick_generator, pick_trigger_matcher, ou35 | DO UPDATE semantics must be mirrored; the upsert's key must match `picks`' per-cohort unique | R8: the upsert overwrites odds and edge on picks that `real_bets` points at. Decide now whether `picks` freezes the first price (recommended; write the latest to `odds_latest`). |
| c3 | `bulk_store_shadow_bets` (pipeline shadow, HHMM) | highest volume, about 48 writes per pick per day | throughput; also the moment to stop writing copies for retired bots if the owner agrees |
| c4 | `store_bet` (sim) plus `news_checker`, the signaler, and the web Telegram/admin columns (`user_placed_at`, `admin_offered_at`) | public-facing ledger | the web writes to sim, so this needs a **web deploy** in the same step; the `pg_notify` trigger |
| c5 | settlement: `_settle_pending_bets`, `_settle_pending_shadow_bets`, the paper bots' `settle_picks`, `fix_stale_live_matches`, `void_ungradeable_1h_bets`, `resettle_wrongly_voided_bets`, `_apply_clv_autovoid`, `results_check._regrade`, **`match_status_sweeper.py` (GitHub workflow)**, `backfill_odds_at_pick_live` | settle both, or switch settlement to `picks` and sync back with a trigger | R5: the sweeper runs outside the VPS, so it is easy to miss. R6: the team_total and 1H double-settler race; fix it before dual-writing, not after. |
| c6 | `publish_picks_forward_test.claim` / `attach_message_id` plus `settle_picks_forward_test` / `_void_forward_test_on_dead_matches` | the pre-registered test | dual-write with the immutability trigger live on `picks`; reconcile field by field daily |
| c7 | `clv_sharp.py`: read `picks` and write `leg_clv_sharp` keyed on `picks.id` | the id is unchanged, so this is a low-risk read switch | — |

**Estimate:** about 4–5 days (c1 ½ d, c2 ¾ d, c3 ½ d, c4 1 d including web, c5 1–1½ d, c6 ¾ d, c7 ¼ d), plus two review agents on each writer step.

### Step 5d: readers switch to `picks`

| order | readers | risk |
|---|---|---|
| d1 | **admin web**: `/admin/bots` (already on 410 views; re-point to `bot_ledger_v2`), `/admin/shadow-bots` + `[bot]` (`shadow_bets_unique`, `shadow_bot_scoreboard`), `/admin/real-bets`, `/admin/ops`, `/admin/place` | admin only. Keep `user_pick_marks` ids valid (UUIDs kept). |
| d2 | engine monitoring: `health_alerts`, ops snapshot, `coolbet_daily_summary`, `results_check.run`, `settle_reconcile`, `live_poller`, `book_price_fidelity`, `trigger_calibrator_check`, `weekly_bot_review`, `threshold_check`, `observatory_metrics` | noisy alerts if the counts change (HHMM copies). Always filter `is_primary`. |
| d3 | **placement and signalling**: `coolbet_placer.load_qualified_*` / `place_bet_by_id`, `coolbet_signaler`, `coolbet_prekickoff_alert`, `pick_generator._candidates_from_pipeline`, `store_real_bet`, `place_coolbet_ui.load_picks` (PAUSED; switch it anyway), `placement_gate` → `bots.can_place_real`, `coolbet_control` | 🤖 OWN. The chance to fix "operator paths read `simulated_bets` only". Only one control may decide `can_place_real`; retire `coolbet_placer_bots` and `PLACEABLE_BOTS` in the same step or they will diverge. |
| d4 | training: `fit_platt`, `fit_platt_live`, `train_b_ml3`, `validate_meta_b_ml3`, `aln1_tune_analysis`, `compute_model_evaluations`, `run_post_mortem` | label drift if the cohort filter changes (sim → `is_primary AND source_ledger='sim'` to keep them identical first). Freeze `probability_raw` before this step, so `news_checker` no longer rewrites labels. |
| d5 | **public**: `write_dashboard_cache`, `getCalibratedHeadlineStats`, `/api/v1/track-record`, `getAllBets`, `getPublicPerformanceExtras`, `getModelV2Stats`, `getRecentSettledBets`, `getAllBotsFromDB`, `_our_stats` (landing competitor table), `export_track_record_snapshot` (**signed public ledger**), `dump_oddsintel_picks_csv` | 👥 PICKS. **Unify the five cohort definitions into one view (`picks_public_cohort`) first**, and prove the figures are unchanged, or record the change on /methodology. The signed daily ledger must keep its row ids and field names; add a `schema_version` rather than changing fields in place. |
| d6 | **pre-registered surfaces**: `picks_public_all`, `picks_forward_test_public`, `picks_forward_test_summary`, `_by_market`, `_arm_summary`, `clv_sharp_legs` → re-point to `picks` **only after c6 has reconciled identically for ≥ 7 days** (and after the view's arm → bot CASE becomes `picks.bot_id`) | 👥 PICKS, highest trust cost. Keep the view **names** and column lists identical (anon grants, and smokes PICKS-FORWARD-TEST-SURFACE, PUBLISHED-ARM-HAS-A-RECORD and ANON-LEAST-PRIVILEGE pin them). |
| d7 | smoke tests: rewrite the ~130 executable references (§1D); add `PICKS-RECONCILES-LEGACY` and `PICKS-IMMUTABLE-FORWARD-TEST` | CI stays red until done. Do it **per reader step**, not at the end. |

**Estimate:** about 5–6 days (d1 1 d, d2 ½ d, d3 1 d, d4 ½ d, d5 1½ d, d6 ¾ d); d7 is spread across these, about 1 d in total.

### Step 5e: old tables become read-only compatibility views, then drop

1. Rename `simulated_bets` → `simulated_bets_legacy`, `shadow_bets` → `shadow_bets_legacy`, `picks_forward_test` → `picks_forward_test_legacy`. Create views with the old names over `picks`, so the ~110 one-off scripts keep working read-only. Revoke INSERT/UPDATE/DELETE and add a BEFORE trigger that raises. The one-off **writers** listed in §1B/§1C will then fail loudly, which is intended.
2. `shadow_bets_unique` becomes a view on `picks` with the same columns (smoke SHADOW-VIEW-COLUMN-DRIFT).
3. Drop after two weeks with zero readers, measured with `pg_stat_user_tables` / `pg_stat_statements` on the legacy tables. Also drop `user_picks`, `coolbet_placer_bots` (folded into `bots`), the `inplay_bet_fired` trigger, and the backup tables (§2.1) with owner sign-off.
4. **Ripple-check the docs:** SYSTEM_MAP, COOLBET_OWN_BETTING, ANALYSIS_GOTCHAS (§18 and others on ledger vocabularies), WORKFLOWS, `bot_registry.py` descriptions, and the methodology page text.

**Estimate:** about 1.5 days, plus the two-week soak.

### Risk register

| # | risk | step | mitigation |
|---|---|---|---|
| R1 | void pnl normalisation changes native P&L sums | 5b | explicit rule, per-bot reconciliation tolerance 0 on pnl_unit |
| R2 | sim CLV-vs-Pinnacle column ambiguity (`clv_pinnacle` vs `_devig`) | 5b | owner or analyst decision; recompute in the backfill |
| R3 | ROI basis (`odds` vs `odds_exec`) differs between admin and public | 5b/5d | two named columns; `pnl_unit_exec` for public |
| R4 | naive sim `pick_time` | 5b | verify against `created_at` before the backfill |
| R5 | `match_status_sweeper` writes from GitHub Actions, not the VPS | 5c | include it in c5; smoke test that no writer targets the legacy tables |
| R6 | double settler (team_total, 1H) | 5c | fix before dual-write |
| R7 | `matches` delete cascades to shadow and sim; `cleanup_match_dupes` doesn't re-point | 5b | `picks.match_id` ON DELETE RESTRICT; update `cleanup_match_dupes` |
| R8 | upsert writers change the price on placed picks | 5c | freeze the first decision price on `picks` |
| R9 | anon grant on `simulated_bets` / `bots` | 5b | `picks` gets no anon grant; public reads go through `*_public` views only |
| R10 | three different arm → bot mappings | 5b/5d | `bot_id` stored on the row; views read it |
| R11 | forward-test regrade (clear, then re-settle) looks like tampering once `immutable` exists | 5b/5c | `pick_settlement_history` audit rows |
| R12 | about 130 smoke tests pin table names | 5d | per-step rewrite; never one big-bang rename |
| R13 | `ensure_bots` resurrects bots from code constants | 5c/5d | make it config-aware before `bots` is authoritative |
| R14 | the Mac or PAUSED placer is un-paused mid-migration against the old tables | 5c/5d | switch `place_coolbet_ui.load_picks` in d3 even though it is paused; placement_gate reads `bots.can_place_real` |

---

## 5. Size estimate

| step | content | estimate |
|---|---|---|
| 5a | this inventory, plus review | done (½ d) |
| 5b | `picks` schema, triggers, backfill, reconciliation vs `bot_ledger` | 1.5–2 d |
| 5c | dual-write, 7 writer groups, daily reconciliation job | 4–5 d |
| 5d | reader switch (admin → engine → placement → training → public → pre-registered), smoke rewrite | 5–6 d |
| 5e | compat views, lock legacy, drop after a 2-week soak, doc ripple | 1.5 d + 2 wk soak |
| **5 total (bets and bots)** | | **≈ 12–15 working days** + soak |
| 5P (separate) | append-only prediction history, calibrator registry, per-market version registry, `picks.prediction_id`; fix the version-blind readers (§2.1) | 4–6 d; independent of 5b–5e except for the nullable FK |

The whole epic was estimated at "~1 wk in phases". **Phase 5 alone is about 2½–3 weeks of work**, plus the soak. The owner should know this before 5b starts.

**Decisions the owner must make before 5b (from §3):**
1. HHMM copies: keep them in `picks`, split them to a cold table, or stop writing them.
2. The canonical Pinnacle CLV column for sim.
3. The ROI basis for `pnl_unit`.
4. `published_picks`: fold into `picks` or leave it.
5. Retire `user_picks`.
6. One public cohort definition to replace the five.
