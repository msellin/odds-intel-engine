Parent row: [[#162]] BOT-REFACTOR-CLEANUP, phase 1 (read-only audit). Area A: pick producers and pricing.

# A. Pick producers and pricing: current truth (2026-09-25, ~12:00 UTC)

This builds on `docs/BOTS_AUDIT_2026_09_24.md` §B3/B5/C, `docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md` §1A, and
`dev/active/unified-bot-model-genesis/predictions.md`. Where those still hold, this report cites them and
does not repeat them. It adds what changed after 2026-09-24 (NEW / NEW+ / EV bots, VIP, O/U sharp outlier,
`ou_comb_v1`, the `#152` plumbing, and the uncommitted `#159` pick-price writer) and reports what was
measured today.

Method: I read the code at HEAD plus the uncommitted working tree, and ran SELECT-only queries against the
VPS DB. I made no changes anywhere except this file.

---

## 0. Headline findings

1. **VIP #1 already breaks the owner's "a public bot never takes a VIP-held pick" rule.** `vip_exclude`
   re-checks the VIP rule against the price at that moment. It never reads the VIP bot's existing picks.
   Of 11 picks by `bot_v10_1x2_newplus_v1` (TESTING, `show_on_picks=TRUE`), **4 are the same selection as a
   VIP EV5 pick**. In 2 of those 4 the VIP pick came first: it was taken at 02:35 and 05:35, then the public
   twin took it at 09:06 and 11:35 after EV drifted below 5%. The other 2 went the other way round.
   (Code: `daily_pipeline_v2.py:3997-4013`.)
2. **`one_per_match` only holds within one run, not across runs.** VIP #1 holds **home @2.37 (07:35) and
   away @3.38 (09:35) on the same match** (`89256583…`). Both went out as VIP DMs. The cause is that the
   flag is only set when `store_bet` returns a new id (`:4379`), and nothing checks the bot's earlier
   picks (`:4291-4404`). `ou_sharp_outlier` does check earlier picks (`ou_sharp_outlier.py:180`), so the
   two VIP bots enforce "one per match" in two different ways.
3. **The live VIP #1 rule is not the backtested rule.** `bot_combined_1x2_ev5_v1` runs inside the legacy
   pipeline loop, so it inherits about 8 gates that B2/B4 never tested: odds-movement veto, ALN-1 +1%
   bump, Pinnacle mid-band +2pp, Pinnacle 0.12 veto, sharp-consensus home gate, Kelly>0, Kelly stake ≥ €1,
   and the meta-model when its env flag is on. In `candidate_funnel` over 2 days, **24 candidates that
   cleared the EV/odds/Pinnacle rule were dropped by these gates, against 26 accepted.** Stakes also vary
   with Kelly (€1.12 to €10.01) where the backtest was flat. `ou_sharp_outlier` is flat €10.
4. **The two VIP bots are `maturity_label='experimental'` but send to Pro/Elite and the VIP channel.**
   Policy §3.1 says EXPERIMENTAL means nothing is sent. VIP is meant to be a channel on top of TESTING or
   higher ("VIP · TESTING"). Nothing records which picks were actually sent: dedup is an in-memory 600 s
   dict (`notify/telegram.py:415`), and the VIP message_id is thrown away.
5. **Each "fair probability" is de-vigged four different ways, with four different Pinnacle freshness
   rules.** See §2.1. NEW+ (VIP #1) de-vigs Pinnacle **proportionally** (`market_consensus_1x2.py:74`),
   uses the latest leg at any age, and gets its "Pinnacle present" check from a separate proportional,
   any-age signal (`supabase_client.py:4798-4870`). VIP #2 uses **power** de-vig with a 3 h age cap. The
   sharp triggers, forward test and CLV use **Shin**.
6. **Paper writers that upsert overwrite the recorded price.** `pick_generator`, `pick_trigger_matcher`
   and `ou35_model_shadow` `DO UPDATE` `odds_at_pick` **and** `odds_at_pick_live` on every re-run, but keep
   the first `pick_time`. Over the last 5 days, **22–52% of their rows differ from the book's quote at
   pick_time, on average 1.0–3.8% higher** (§2.3). They also write `odds_at_pick_live` as a single-book
   price. The uncommitted `#159` `pick_price` defines it as the best of 4 own books, and it only fills
   NULLs, so these writers' values stick.
7. **`pick_generator`'s `predictions` source reads every model source.** It picks
   `DISTINCT ON (match, market) ORDER BY model_version DESC` with no `source` filter
   (`pick_generator.py:539-551, :617-628`). On today's 780 upcoming 1X2 match-markets it would price
   **276 off API-Football's own prediction (`source='af'`)**, 21 off raw `xgboost`, 81 off
   `national_team_v1`, and 399 off `ensemble`. It feeds the active `bot_unified_gate_1x2_paper_v1`. The
   isotonic calibrators in `pick_triggers.py:184-240` and `ou35_model_shadow.py:50-58` are fitted on the
   same source-blind pool.
8. **Real-money supply depends on the status label.** `bot_coolbet_1x2_model_v1`, the only real-money
   1X2 path, takes its candidates from `simulated_bets` of **any bot whose `maturity_label='calibrated'`**
   (`pick_generator.py:391-418`, `BotConfig.maturity` default `:101`). Today that means `bot_v10_1x2`. The
   #155 status rollout will change this bot's feed without anyone touching it, which conflicts with policy
   §3.1: real money is not a status.
9. **Dead weight is still running.** It includes 33 of 40 `BOTS_CONFIG` bots (retired, but still evaluated
   in 48 shadow cohorts a day), 5 no-op or uncalled shadow passes (about 1,300 lines,
   `daily_pipeline_v2.py:4619-5925`), InplayBot (3,269 lines, env-gated off, last write 2026-08-21),
   `pick_triggers` `model_*` windows (1,369 rows, no consumer), and `ou_prob_source` / O/U `vip_exclude`
   (no bot uses them).

---

## 1. Inventory: every live pick/candidate producer

Scheduler registrations are in `workers/scheduler.py`. "Tbl" is the table the producer writes.

| # | Producer (file:line) | Schedule (scheduler.py:line) | Probability source | Price basis written as `odds_at_pick` | Tbl | Bots (live status) | Sends? |
|---|---|---|---|---|---|---|---|
| P1 | `daily_pipeline_v2.run_morning` live mode; bot loop `:3700-4404` | `job_morning` 04:00 (:3365); `job_betting_refresh` :05/:35 via `betting_pipeline.run_betting` (:3470) | ensemble `pred` (inline `compute_prediction`) + `calibrate_prob` (Platt + Pinnacle shrink, `model/improvements.py:128`); or `rating_1x2_predictions` (`r1x2_d8plus_v1` / `r1x2_comb_v1`, `:3052-3066`); or `ou_model_predictions` (`:3068-3078`, unused) | MAX over **publishable** books, latest quote per book, lag ≤ 6 h and age ≤ 48 h (`:2280-2333`), after the 1X2/BTTS/DC ×1.25 outlier cap (`:2402-2525`) and the O/U "Pinnacle required + ×2.0" cap (`:2498-2504`) | `simulated_bets` via `store_bet` (`supabase_client.py:2256`) | 7 active: `bot_v10_1x2` (calibrated), `bot_high_roi_global_v2` (beta), `bot_v10_1x2_newplus_v1` (testing), `bot_combined_1x2_ev5_v1` (VIP, experimental), `_ev8_v1`, `bot_rating_1x2_v1`, `bot_combined_1x2_v1` (experimental). 33 retired, skipped at `:3706` | VIP DM + VIP channel (`:4537-4556`); operator alert (env, off); signaler after the run (P9) |
| P2 | `run_morning(shadow_mode=True)` timing cohorts | `job_shadow_run_interval` :10/:40, 24/7 (:3599). The docstring at `:551` wrongly says ":05/:35, 07-22" | same as P1 | same as P1 | `shadow_bets` via `bulk_store_shadow_bets` (`supabase_client.py:2389`, `ON CONFLICT DO NOTHING`) | **all 40, retired included** (`SHADOW-RETIRED-OK`, `:3701-3707`) | no |
| P3 | `_run_no_pin_shadow_pass` `:4619`, `_run_sweep_shadow_pass` `:4970` | called from P1 morning (`:4600-4604`) and every P2 cohort (`:4459-4463`) | ensemble / Pinnacle Shin | — | `shadow_bets` | every bot retired → returns 0 at `:4652` / `:4989` | no |
| P4 | `_run_coolbet_value_pass :5330`, `_run_pin_ou_shadow_pass :5479`, `_run_pin_1x2_shadow_pass :5740` | **never called** (smokes pin "must NOT be scheduled") | Shin Pinnacle | — | `shadow_bets` | retired | no |
| P5 | `ou_sharp_outlier.run` `:155` | `job_ou_sharp_outlier` :14/:44 (:3684) | Pinnacle **power** de-vig (`:46`), each book ≤ 3 h (`:73`), EV 5–15%, odds 1.30–6.00; EARLY = **now** ≥ 12 h before KO (`:115`); 2ANCHOR = leave-one-out power consensus ≥ 3 books, EV ≥ 2% | single book's latest quote (≤ 3 h), best EV | `simulated_bets` via `store_bet`, flat €10 | `bot_ou_sharp_early_v1` (VIP, experimental), `bot_ou_sharp_2anchor_v1` (experimental, hide_pending) | VIP DM + channel for EARLY (`:208`) |
| P6 | `pick_generator.generate` `:171`, via `bot_configs.CONFIGS` + `TRIGGER_CONFIGS` | `job_coolbet_model_1x2_shadow` / `_ou_shadow` :10/:40 (:4083/:4076) plus `on_odds_written()` after every Coolbet and Unibet-Site sweep (`coolbet_explorer.py:2327`, `unibet_odds_feed.py:852`) | `pipeline` = `simulated_bets.calibrated_prob` of `maturity='calibrated'` bots (`:391`); `sharp_devig` = Pinnacle Shin, any age (`:421`); `predictions` = source-blind newest `model_version` (`:497`) | winning **PLACEABLE_BOOKS** (Coolbet, Unibet-Site) price via `best_price_router.decide_book`; router freshness 180 min | `shadow_bets` **upsert** (`:321-341`) | `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1` (real-money capable, toggles off), `bot_trigger_1x2_sharp_v1`, `bot_trigger_ou_sharp_v1`, `bot_unified_gate_1x2_paper_v1`; 2 retired configs exit at `_bot_id` (`:132`) | real-money placer reads it (off) |
| P7 | `pick_triggers` Stage A (`pick_triggers.py`) | `job_pick_triggers` :05 hourly (:4115) | `model_*` = isotonic over source-blind `predictions` (`:184-240`, `:353`); `sharp_*` = Pinnacle Shin (`:262-291`) | windows only (`min_odds`, `max_odds = ×1.6`) | `pick_triggers` | — | no |
| P8 | `pick_trigger_matcher.run_all` | :15/:45 (:4119) | `pick_triggers.cal_prob` (sharp strategies only) | that book's latest quote (≤ 60 min) inside the window, `anchor_sanity` ratio guard | `shadow_bets` **upsert** (`:201-214`) | `bot_{coolbet,unibet}_trigger_sharp_{1x2,ou}_v1`, `bot_trigger_1x2_sharp_tight_v1` (4 books) | no |
| P9 | `coolbet_signaler.signal_all_bets` (after each P1 run, `betting_pipeline.py`) | — | reads `simulated_bets` | `odds_at_pick` | sets `simulated_bets.signaled_at` | public channel only if the (match, market, selection) group has a **calibrated** bot (`coolbet_signaler.py:212`) and the market is in `_PUBLIC_MARKETS` (`:295`); VIP bots excluded (`:155`) | public channel |
| P10 | `ou35_model_shadow.generate_picks` | :10/:40 (:4088) | isotonic over source-blind `predictions` 'over35' (`:50-58`, `:110-113`) | Coolbet only | `shadow_bets` **upsert** (`:131-146`) | `bot_ou35_model_v1` (experimental) | no |
| P11 | `corners_paper_bot` / `team_total_paper_bot` / `first_half_1x2_paper_bot` | 08/12/16/20 :20/:25/:27 (:4056-4064) | 2-way own de-vig (`_devig_two_way`) | 4 own books | `shadow_bets` | all 3 **retired**; kept writing on the owner's 09-18 decision (~1,150 rows/wk) | no |
| P12 | `inplay_collector.py` (VPS systemd `oddsintel-inplay-collector`) | not the scheduler | locked slow-state triggers | Epicbet on-screen / AF live | `shadow_bets` (`DO NOTHING`) | `bot_inplay_slowstate_v1` + `_afctl_v1` (experimental) | no |
| P13 | `inplay_bot.run_inplay_strategies` (3,269 lines) | `live_poller.py:565` only if `INPLAY_STRATEGIES_ENABLED` (default false) | — | — | `simulated_bets` | all `inplay_*` retired; last write 2026-08-21 | no |
| P14 | `scripts/publish_picks_forward_test.py` (**#161 editing**) | :05/:35 (:4101) | Pinnacle Shin, `MAX_ANCHOR_OVERROUND 0.04`, `ALIGN_MIN 60`; consensus arm = own `_consensus_anchor` (`:389`), ≥ 5 books; twin arm uses `anchor.py` | best publishable (`EXCLUDED_BOOKS :360`), `MAX_RATIO 1.20`, `MAX_ODDS 4.0`, lead ≥ 45 min | `picks_forward_test` | `bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_b/c/d_v1`, junk control, 2 twin arms (uncommitted) | public channel |
| P15 | `publish_daily_picks` | 06:45 (:3616) | `predictions` `source='ensemble'`, **every** model_version | — | `published_picks` (5 versions in 3 days) | — | no web reader (only settlement / backfill script) |
| M1 | `rating_1x2_shadow` fit (05:30/17:30, :3673) and `--refresh` (:10/:40, :3679) | | ratings + `market_consensus_1x2` (**proportional**, any-age close legs) → `rating_1x2_predictions`; also fits `combined_ou` → `ou_model_predictions` (power, any-age Pinnacle) | | model tables | feeds P1 | docstrings `scheduler.py:1137-1141, 1159-1165` still say "nothing that stakes or publishes reads that table", which is no longer true since VIP #1 |

Price writers after the insert: `store_bet` → `pick_price.price_legs` (**uncommitted, #159**,
`supabase_client.py:2376-2386`); `job_backfill_live_prices` every 30 min (:3344) fills NULL
`odds_at_pick_live` / `odds_at_pick_available` on both tables.

---

## 2. Duplication: the same thing computed in more than one place

### 2.1 De-vig / fair probability (the core duplication)

| Copy (file:line) | Method | Pinnacle / leg freshness | Consumer | Agrees with? |
|---|---|---|---|---|
| `model/devig.py:194 devig()` | **Shin** (canonical) | caller's choice | pick_triggers `:291`, pick_generator sharp `:474`, settlement Pinnacle close `:904`, forward test, observatory, promo_ev, anchor.py | reference |
| `utils/anchor.py compute_anchor` | Shin, one-fetch sets, Pinnacle ≤ 60 min, consensus ≥ 5 | explicit | `clv_sharp.py:157`, forward-test twin arm (`:840`) | its header says it is "ONE answer", but no producer in P1/P5/P6/P8 uses it |
| `supabase_client.py:4798-4870` signals `pinnacle_implied_{home,draw,away,over25,under25}` | **proportional**. Legs are chosen independently as the latest per selection, with **no age limit** (`timestamp <= m.date` only). A partial 1X2 set is normalised over only the legs present. **A single-sided O/U writes the raw vigged implied** (`:4867-4870`) | any age | P1: `calibrate_prob` anchor, Pinnacle 0.12 veto, mid-band, **`require_pinnacle` presence for VIP #1** (`:3973`) | disagrees with Shin (a draw bias of ~1–2 pp on longshots) |
| `model/market_consensus_1x2.py:65-78 _triples` | **proportional** for consensus **and** Pinnacle (`pin_h/d/a`) | latest leg, any age (`which="close"`) | NEW+ `r1x2_comb_v1` → VIP #1, EV8, newplus twin | known [[#154]]; disagrees with every sharp producer |
| `model/combined_ou.py:34 power_devig` | power, overround guard 0.98–1.30 | Pinnacle latest, **any age** | `ou_model_predictions.p_over` / `p_pin` → P1 O/U `vip_exclude` (inert) | same formula as the next row, different freshness |
| `jobs/ou_sharp_outlier.py:46 power_devig` | power, same guard | ≤ 3 h | VIP #2 | same formula, second copy |
| `publish_picks_forward_test.py:389 _consensus_anchor` | Shin per book (or `devig_fn`), mean, ≥ 5 books | 6 h window (`:452`) | consensus arms | 5th consensus implementation (anchor.py §"four implementations") |
| `automation/anchor_sanity.consensus_median_quotes` | median raw price of ≥ 4 books (no de-vig) | — | matcher and router guard | different kind of number (price, not probability) |
| `utils/mirror_guard.consensus`, `automation/promo_ev.consensus_fair_prob` | own consensus | — | fetch_odds / board_audit, promo | 6th and 7th |
| `corners_paper_bot.py:71`, `team_total_paper_bot.py:73 _devig_two_way` | own 2-way | — | retired paper bots | copies |

So, today: **VIP #1 = proportional/any-age. VIP #2 = power/3 h. Sharp triggers = Shin/any-age
(generator) or Shin with a 60-min book quote but no Pinnacle age cap (matcher). Forward test = Shin/60 min
aligned. CLV = Shin (anchor.py or settlement).** The CLV used to judge the VIP bots therefore uses a
different de-vig from the one that picked them.

### 2.2 "Sharp overlay" rules: five producers of the same idea

| Producer | Books bet | Fair p | Edge unit | Floor / ceiling | Anchor age | Book-quote age | Odds cap |
|---|---|---|---|---|---|---|---|
| P1 VIP #1 EV5 (NEW+ ≈ Pinnacle + consensus) | publishable | combined logit | EV | ≥ 5% / none | any | ≤ 6 h lag, ≤ 48 h | 1.30–6.00; ×1.25 outlier |
| P5 VIP #2 O/U EARLY | publishable | Pinnacle power | EV | 5% / 15% | ≤ 3 h | ≤ 3 h | 1.30–6.00 |
| P6 `bot_trigger_*_sharp_v1` | Coolbet, Unibet-Site | Pinnacle Shin | pp | 3% / 8% | any | router 180 min | ×1.6 |
| P8 per-book sharp + tight | 1 book / 4 books | Pinnacle Shin | pp | window | any | ≤ 60 min | ×1.6 + anchor_sanity 1.5625 |
| P14 forward test sharp arm | publishable | Pinnacle Shin, overround ≤ 4% | pp | 3% / — | aligned ≤ 60 min | 6 h | 4.0, ratio 1.20 |

Five book sets, three edge units, three de-vigs, and five freshness rules, all for one concept. The
"sent" pair (P1 VIP, P14) do not share a single constant.

### 2.3 Price recorded at pick time (`odds_at_pick` / `odds_at_pick_live`)

| Writer | `odds_at_pick` | `odds_at_pick_live` | Overwritten later? |
|---|---|---|---|
| P1 `store_bet` | best publishable (P1 rule) | NULL → filled by `pick_price` (uncommitted) / backfill = best of 4 own books ≤ pick_time | no (unique `uq_bet_per_bot_match_market_selection`, insert-or-skip) |
| P2 `bulk_store_shadow_bets` | same | NULL → backfill | no (`DO NOTHING`) |
| P5 `store_bet` | the book's quote (≤ 3 h) | `pick_price` | no |
| P6 / P8 / P10 upserts | winning book | **written = same single-book price** | **yes, both, every re-run; `pick_time` kept** |

Measured on shadow_bets over the last 5 days, comparing stored `odds_at_pick` with the recommended book's
latest quote at or before `pick_time`:

| bot | rows | differ | mean stored/quote − 1 |
|---|---|---|---|
| bot_unified_gate_1x2_paper_v1 | 284 | 148 (52%) | +3.81% |
| bot_ou35_model_v1 | 67 | 26 (39%) | +1.49% |
| bot_trigger_1x2_sharp_tight_v1 | 118 | 48 (41%) | +1.03% |
| bot_trigger_1x2_sharp_v1 | 61 | 19 (31%) | +1.86% |
| bot_unibet_trigger_sharp_1x2_v1 | 64 | 16 (25%) | +3.06% |
| bot_coolbet_trigger_sharp_1x2_v1 | 46 | 10 (22%) | +1.46% |

The bias is upward because a later re-run only rewrites a row when that price also clears. This is the
decision-price-vs-last-clearing-price defect from BOTS_AUDIT B3(b), now measured, and it carries into
`#159`'s `odds_at_pick_available` (floored at `odds_at_pick_live`).

### 2.4 Book sets (update to BOTS_AUDIT B5)

- Deny lists: `daily_pipeline_v2._NON_OFFERS :1269` (with `api-football*`) vs
  `publish_picks_forward_test.EXCLUDED_BOOKS :360` (without) vs `anchor.NEVER_IN_ANCHOR` (adds Coolbet,
  Coolbet-OddsAPI) vs `book_price_fidelity._NON_BOOKS :50`. Four copies.
- Own 4 books: `ACCESSIBLE_BOOKMAKERS :1287`, `board_guard.DIRECT_BOOKS :94`,
  `health_alerts.DIRECT_FEED_BOOKS :801`, `near_kickoff_capture.BOOKS :60`, three `PLACEMENT_BOOKS` in the
  paper bots, `feed_registry.COVERAGE_BOOKS :154`, forward-test `OWN_DIRECT_BOOKS :260` (deliberately
  frozen). Nine copies. They agree today.
- `market_consensus_1x2.CONSENSUS_BOOKS :29` still includes AF `Unibet` (33% phantom-high, the reason it
  is excluded everywhere else) plus `BetWin` / `Betfred` (CSV imports). **This disagrees with the deny
  lists.** It is historical, but it feeds NEW+'s fit.
- `PLACEABLE_BOOKS = (Coolbet, Unibet-Site)` (`best_price_router.py:47`) for P6. P8 tight uses 4 books.
  `ACCESSIBLE` has 4.

### 2.5 Outlier caps (same guard, different numbers)

Pipeline 1X2/BTTS/DC ×1.25 (`:2402`); pipeline O/U ×2.0 vs Pinnacle (`:2504`); `_own_outlier_ok` ×1.25
own-set (`pick_generator.py:361`); `anchor_sanity.OUTLIER_MULT` 1.6 plus ratio 1.5625 (P6/P8/P7); forward
test `MAX_RATIO` 1.20; ou_sharp EV cap 15%; dead passes `_PIN_1X2_OUTLIER_MULT 1.35` (`:5737`) and
`_PIN_OU_OUTLIER_MULT 1.30` (`:5244`). Both dead-pass comments claim to "match ODDS-OUTLIER-FILTER", which
is 1.25 and 2.0. They are wrong but unreachable.

### 2.6 VIP rule: three copies

1. The bots themselves: `BOTS_CONFIG["bot_combined_1x2_ev5_v1"]` (`:182`) and `ou_sharp_outlier` constants
   (`:31-38`).
2. `vip_exclude` predicates hard-code EV5 = 0.05 and O/U EARLY 5–15% / 12 h (`:3997-4013`). They omit the
   VIP bot's `require_pinnacle`, odds range and `one_per_match`, and use `ou_model_predictions.p_pin`
   (any-age Pinnacle) where VIP #2 uses ≤ 3 h quotes. That makes them over-exclude in some cases, and they
   also miss picks the VIP bot already holds (finding 1).
3. Membership: `bot_registry.VIP_BOTS :326` (engine sends and the signaler exclusion) vs `bots.vip`
   (RLS, migrations 420/421, web `engine-data.ts:412`). They agree today; nothing checks the two against
   each other.

### 2.7 "Edge" column meaning

`simulated_bets.edge_percent` = pp (`store_bet` re-derives it, `supabase_client.py:2277`). P1 EV bots gate
and sort on EV but store pp (`:4265, :4302`). P5 stores pp. The dead pipeline shadow passes stored EV in
the same column (`coolbet_signaler.py:247` comment). `ALN-1` (+1%) and mid-band (+2pp) bumps are added to
the EV threshold unchanged (`:4056, :4165`), which mixes units.

### 2.8 Isotonic calibrators

`pick_triggers.py:184` (1X2) and `:229` (O/U), `ou35_model_shadow.py:45`, and the generator's reuse of
them: three separate fits over the same source-blind pool (finding 7). The pipeline uses a different
calibrator again (`calibrate_prob`, Platt + Pinnacle shrink).

---

## 3. Drift from owner policy §3

| Rule | Path | Breach (evidence) |
|---|---|---|
| §3.6 public never takes a VIP-held pick | `vip_exclude` `:3997` | 2 real leaks, 2 reversed-order overlaps, out of 11 newplus picks (finding 1). `bot_v10_1x2` and `bot_high_roi_global_v2` (both public) have **no** `vip_exclude` at all. Adding it is a live-bot rule change: needs a twin + owner OK |
| §3.1 EXPERIMENTAL = nothing sent | VIP #1 / VIP #2 | `maturity_label='experimental'` while sending DMs (`:4537-4556`, `ou_sharp_outlier.py:208`) |
| §3.2 anything sent is counted | VIP sends | the pick row is counted, but **which picks were sent is not recorded** (in-memory dedup, message_id discarded). The one_per_match leak sent both sides of one match |
| §3.1 statuses = distribution | `coolbet_signaler.is_public_eligible :212` | public channel = "group has a **calibrated** bot". TESTING / BETA bots (newplus, `high_roi_global_v2`) are never posted; policy says TESTING+ are sent. Distribution is decided by a second rule that can drift from status (area B/C may own the fix) |
| §3.1 real money is not a status | `pick_generator._candidates_from_pipeline :391` | real-money candidate supply = `maturity_label='calibrated'` (finding 8) |
| §3.4 change a live bot only via a twin | `vip_exclude` plumbing | fine as built (twin only). Any change to shared P1 gates (pin veto, ALN, odds_mv) changes `bot_v10_1x2` and `high_roi_global_v2` too; they are in the same loop |
| §3.5 one shared computation per number | §2.1–§2.8 | de-vig ×4 methods, sharp rule ×5, price basis ×3 writers, VIP rule ×3, book sets ×9 |
| Pre-registration integrity | VIP #1 | the live rule ≠ the backtested B2 rule (finding 3). The record being built does not measure the rule that was approved |
| Real-money-capable path missing a gate | P6 `sharp_devig` | no Pinnacle age cap (C10 still true). P6 `predictions` source-blind prices (finding 7). The placer re-prices before staking; money is currently off |

---

## 4. Dead / retired / unused (with evidence)

| Item | Evidence | Size |
|---|---|---|
| 33 retired `BOTS_CONFIG` entries | DB: 7 active of 40. Still evaluated by 48 cohorts a day, ~52k shadow rows / 30 d (BOTS_AUDIT B4); `high_alignment` alone 849 in 7 d | `:208-1146` |
| `BOT_TIMING_COHORTS` entries | every active bot is `"all"` and cohorts are not used by any live bot; `BOT_COHORT_OVERRIDES` env (`:3720`) | `:1149-1190` |
| `_run_no_pin_shadow_pass`, `_run_sweep_shadow_pass` | all bots retired (08-21..08-26), early return, still called 49×/day | `:4619-5186` |
| `_run_coolbet_value_pass`, `_run_pin_ou_shadow_pass`, `_run_pin_1x2_shadow_pass` + `_LINESHOP_*` / `_PIN_*` constants | never called; smokes assert it | `:5188-5925` |
| `ou_prob_source="combined_ou"` + O/U `vip_exclude` | no BOTS_CONFIG entry sets it (grep: `:3782, :3961` only) | inert until #152 |
| InplayBot `workers/jobs/inplay_bot.py` | env default off (`live_poller.py:565`); all `inplay_*` retired; last write 08-21 | 3,269 lines |
| `pick_triggers` `model_1x2` / `model_ou25` strategies | matcher routes sharp only (`BOOK_MARKET_BOTS :31`); 1,053 + 316 live rows | Stage A half |
| `TRIGGER_CONFIGS` `bot_trigger_{1x2,ou}_model_v1` | retired 09-13/14, exit at `_bot_id` each sweep | 2 configs |
| P6 double invocation | model bots run from `job_coolbet_model_*` :10/:40 **and** every sweep `on_odds_written` | redundant, harmless |
| `published_picks` (P15) | no web reader. Writes all 5 ensemble versions (v20260712 240, v20260830 247, v20260903_cut0820 165, v20260705 43, v20260823 2 in 3 d) | job + table |
| `store_prediction_snapshot` stage `stats_only` (`:4382`) | table `prediction_snapshots` **does not exist** (`to_regclass` = NULL); every call fails silently inside try/pass | dead call |
| Retired paper bots P11 | owner kept them (09-18); nothing reads them | ~1,150 rows/wk |
| Stale docs in code | `bot_wide_1x2_model_v1` "exists" (`coolbet_model_1x2_shadow.py:28`, not in configs); `job_shadow_run_interval` docstring timing; rating job "nothing reads it"; `betting_pipeline.py` header (06:00 cron, GitHub workflow) | comments |
| `bot_combined_1x2_v1` | 0 simulated and 0 shadow rows in 7 d (starved, as the handover predicted) | owner's call (§3.3 flag) |

---

## 5. Refactor candidates

Risk: L = low, M = medium, H = high. "Twin+OK" = touches a live bot's rules, so it needs a twin and the
owner's OK. "Blocked" = depends on a file another session holds (§6).

| # | Candidate | Serves | Risk / who depends | Gate |
|---|---|---|---|---|
| R1 | **Make `vip_exclude` read the VIP ledger**: drop the candidate if any pending `simulated_bets` row of a `VIP_BOTS` bot has the same (match, market, selection), keeping the predicate for same-run order. Also block the reverse (a VIP bot must not take a selection a public bot already published), or accept it and say so | §3.6 | L code; changes newplus (TESTING) picks | Twin+OK only in the sense that newplus is live; `daily_pipeline_v2.py` is **blocked** (#152/#161 editing BOTS_CONFIG) |
| R2 | **`one_per_match` across runs**: before storing, skip if the bot has any pending row on the match (same as `ou_sharp_outlier.py:180`), and set `_one_done` when `store_bet` returns None on dedup | §3.2, pre-reg | L; changes VIP #1 | owner OK (VIP live); blocked file |
| R3 | **Run VIP #1 as its own module** (like P5), with exactly the B2 rule and flat stake, and no inherited legacy gates. Or record the inherited gates as part of the pre-registered rule. Keep the current bot as-is and add a twin | pre-reg, §3.4, §3.5 | M; the VIP record restarts | **Twin+OK** |
| R4 | **One `fair_prob(match, market, at, method, max_age)` service** in `workers/model/devig.py` + `utils/anchor.py`, with an explicit per-market method (Shin for 1X2, power for O/U per GOTCHAS #78) and explicit Pinnacle max-age. Replace in order: the `pinnacle_implied_*` signal writer (fixes proportional, partial-set and single-side-raw), `market_consensus_1x2._triples` ([[#154]]), both `power_devig` copies, forward-test `_consensus_anchor` (**frozen pre-reg**: leave it, parity-test only) | §3.5 | **H**: changes the `calibrate_prob` anchor and Pinnacle veto for `bot_v10_1x2` (live), and the NEW+ fit | Twin+OK for every live consumer; parity test first |
| R5 | **Stop the upsert overwrite** in P6/P8/P10: `DO UPDATE` only sets `last_seen_odds` (new column) or nothing. Never write `odds_at_pick_live` from these writers; leave it to `pick_price` | §3.5, #159 | L–M; shadow scoreboards shift by −1…−4% ROI on those bots | **depends on #159** (`pick_price.py` uncommitted) |
| R6 | **Source-filter every `predictions` reader**: `source='ensemble'` plus the per-market production version (`_active_model_version`). Covers the generator `:539, :617`, `pick_triggers :184, :229, :353`, `ou35_model_shadow :50, :110`, `publish_daily_picks` | §3.5 | M; changes `unified_gate` (experimental) and `ou35` (experimental), and the P6 real-money bot's `predictions` source (unused) | owner OK (experimental bots only) |
| R7 | **Decouple real-money supply from status**: `BotConfig.source_bots=("bot_v10_1x2",)` instead of `maturity=("calibrated",)` | §3.1 | L; today it is the same set; guards against #155 | do before #155 flips labels |
| R8 | **Delete dead passes** P3/P4 (`:4619-5925`, about 1,300 lines) and their smoke pins. Keep `_get_bot_id_by_name` (imported by `ou_sharp_outlier.py:158`). Also delete the retired `TRIGGER_CONFIGS`, the `model_*` strategies in `pick_triggers` + calibrator, InplayBot + the `live_poller` hook | cleanup | L; about 12 smoke tests reference these functions (`smoke_test.py:1998-2139, 4158, 26337-26465, 29015, 30497-30510, 30791, 50109, 50722, 53580`) | blocked file for P3/P4 |
| R9 | **Stop evaluating retired BOTS_CONFIG bots in shadow cohorts** (remove `SHADOW-RETIRED-OK`), then move the 33 dicts into an archive module that `export_bot_config` / registry can still read | cleanup, §3.3 (retired picks keep counting; new ones are not needed) | L; ~52k rows / 30 d stop; the "recovery criterion" has no reader (B4) | owner OK (reverses a 05-20 decision); blocked file |
| R10 | **Record every send**: a `pick_sends(pick_id, channel, sent_at, message_id)` row (or `simulated_bets.vip_sent_at`) written by the one VIP send helper, used by both P1 and P5 | §3.2 | L | new migration; web headline (#159) may want it |
| R11 | **Status ↔ VIP consistency**: set VIP bots to TESTING (owner) and add a check `VIP_BOTS == {bots.vip}` (smoke or startup) | §3.1 | L | owner OK (label change = distribution) |
| R12 | **One deny-list constant** (`is_publishable_book`) imported by the forward test *for parity only* (pre-reg frozen), `anchor`, `book_price_fidelity`, `market_consensus_1x2`; one own-books constant | §3.5 | L; `CONSENSUS_BOOKS` change alters the NEW+ fit | NEW+ refit = Twin+OK |
| R13 | **One sharp-producer engine**: fold P6 `sharp_devig` into P8 (or the reverse) with one freshness (60 min book, Pinnacle ≤ 60 min aligned), one ceiling and one book set. P5 becomes a config of it | §3.5 | M; the 5 per-book sharp bots are experimental | owner OK |
| R14 | Fix stale comments/docstrings (§4 last rows) | ripple | L | any time |

---

## 6. Files other sessions are editing: dependencies

Uncommitted in the shared checkout: `daily_pipeline_v2.py` (clean at HEAD but BOTS_CONFIG is claimed by
#152/#161), `workers/registry/bot_registry.py`, `docs/SYSTEM_MAP.md`, `scripts/publish_picks_forward_test.py`
(+`record_twin_arms`), `workers/jobs/settlement.py` (dashboard_cache → `bot_performance`),
`workers/scheduler.py` (backfill + twin arms), `workers/api_clients/supabase_client.py` (`store_bet` →
`pick_price`), new `workers/utils/pick_price.py`, migrations 433/434, `scripts/backfill_odds_at_pick_live.py`.

| Candidate | Blocked by |
|---|---|
| R1, R2, R3, R8 (P3/P4 part), R9 | `daily_pipeline_v2.py` BOTS_CONFIG owners (#152, #161) |
| R5 | #159 (`pick_price.py`, `supabase_client.py`, backfill) |
| R10 | touches `daily_pipeline_v2.py` + `ou_sharp_outlier.py`; the headline design is #159 |
| R11 | #155 (statuses) and `bot_registry.py` |
| R4 | `settlement.py` (Pinnacle close CLV) is #159's. Do the signal writer + `market_consensus_1x2` first (#154) |
| R12 | forward test is #161's and pre-registered: parity test only |
| R6, R7, R13, R14 (non-pipeline files) | free now: `pick_generator.py`, `bot_configs.py`, `pick_triggers.py`, `ou35_model_shadow.py`, `publish_daily_picks.py` |

---

## 7. Reproduce (SELECT-only)

```sql
-- VIP overlap with public twins (finding 1)
WITH vip AS (SELECT s.* FROM simulated_bets s JOIN bots b ON b.id=s.bot_id
             WHERE b.name='bot_combined_1x2_ev5_v1')
SELECT s.match_id, s.selection, s.pick_time, v.pick_time
  FROM simulated_bets s JOIN bots b ON b.id=s.bot_id
  JOIN vip v USING (match_id, market, selection) WHERE b.name='bot_v10_1x2_newplus_v1';
-- one_per_match leak (finding 2)
SELECT match_id, count(*) FROM simulated_bets s JOIN bots b ON b.id=s.bot_id
 WHERE b.name IN ('bot_combined_1x2_ev5_v1','bot_combined_1x2_ev8_v1') GROUP BY 1 HAVING count(*)>1;
-- inherited gates on EV5 (finding 3)
SELECT step, count(*) FROM candidate_funnel WHERE bot='bot_combined_1x2_ev5_v1'
 AND day>=current_date-1 GROUP BY 1 ORDER BY 2 DESC;
-- source-blind predictions (finding 7)
SELECT source, model_version, count(*) FROM (SELECT DISTINCT ON (p.match_id,p.market) p.source,p.model_version
  FROM predictions p JOIN matches m ON m.id=p.match_id WHERE p.market LIKE '1x2_%'
  AND m.date>now() AND m.status='scheduled' AND p.model_probability IS NOT NULL
  ORDER BY p.match_id,p.market,p.model_version DESC) x GROUP BY 1,2;
-- upsert price drift (§2.3): stored odds_at_pick vs the book's latest quote <= pick_time, last 5 d
```
