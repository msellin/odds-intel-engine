# Audit of `MASTER_TASK_LIST_2026_09_14.md` — end of day, 2026-09-14

> # ⛔ NOT A TASK LIST — 2026-09-18
>
> This is the AUDIT of `MASTER_TASK_LIST_2026_09_14.md`, kept for its method and evidence.
> Both it and the list it audits were retired as backlogs on 2026-09-18; the surviving work
> lives in `PRIORITY_QUEUE.md` as `MODEL-TRAINING-DEBT-2026-09-18`. Read it for HOW things
> were verified, never for what is open.


The master list was written this morning. A day of findings then overturned
several of its premises. This audits all **17 open items (#4–#20)** against the
code, the DB and git history — never against a doc asserting a state.

**Method, and why it is stated first.** This repo's own rule is *"close with
evidence, never by assumption"*, and today produced three cases where a
commit's claim and the tree disagreed (the leakage-canary exception that was
never removed; `ACCESSIBLE_BOOKMAKERS` naming a book dead for two days; a
published CLV measured against an arbitrary book). So every verdict below cites
a commit hash, a `file:line`, or a SQL result. Four independent passes were run
(three verification agents plus a direct pass) and each load-bearing number was
reproduced from source rather than read from prose.

**Two things were audited per item, not one.** The owner's addition: *"not
finished" and "deliberately stopped" are different states and lead to opposite
actions.* Every row therefore carries a **WHY** category:

| code | meaning |
|---|---|
| **NEVER STARTED** | no commit, no branch, no partial code |
| **ABANDONED** | partial work sits in the tree — say whether it is safe |
| **DEFERRED** | someone decided not to, and wrote why (quoted verbatim) |
| **BLOCKED** | named blocker; say whether it has cleared |
| **OVERTAKEN** | done under another name, or the premise dissolved |
| **UNKNOWN** | no reason recorded anywhere — the next person will re-litigate it |

---

## Headline

| | count | items |
|---|---|---|
| **ALREADY DONE** — close today | 2 | #7, #17 |
| **OBSOLETE as filed** (answered elsewhere) | 1 | #20 → rescoped |
| **RESCOPED** — the work changed shape | 5 | #4, #6, #9, #13, #15 |
| **STILL VALID** | 9 | #5, #8, #10, #11, #12, #14, #16, #18, #19 |

Nothing on the list was *silently* closed by today's ~25 commits except #7 and
#17. The bigger correction is to **scale**, not to status: three items quote
numbers that today's work moved by 8–16×, and one item's entire justification
(`#20`) was answered ten days before the list was written.

**Three findings below are not status notes.** They are live defects found while
auditing and they outrank most of the list — see §Findings.

---

## The 17 items

| # | Item | Verdict | WHY not done | Evidence | What changed today |
|---|---|---|---|---|---|
| **4** | The decisive experiment — does the clean model add anything? | **RESCOPED** | **OVERTAKEN** (1X2 leg) + **NEVER STARTED** (O/U, BTTS) | `scripts/residual_test.py` shipped in `c8b729a`, corrected by `2c3f87b`. n=7,662, test half 3,831, **α = 0.0000 in all three arms**; profile-likelihood **95% CI [0, 0.0325]**. Scope proven in code, not prose: `residual_test.py:63` loads only `result_1x2.pkl`, `:136` `idx = classes.index(0)`, `:88` `y = score_home > score_away`. Zero over/under/BTTS references in the file — no disabled path, nothing half-wired. | Pre-declared outcome **(a)** landed for 1X2: model-anchored 1X2 staking is dead. **The residue is the whole item now:** O/U and BTTS were never tested this way. The goalline α that the plan doc calls "the one live instrument suggesting the model contributes something" is real (`shrinkage_alpha_t1..t4_goalline` = 0.2278 / 0.2878 / 0.2848 / 0.1463) **but is fitted over all history with no date bound** — the same contaminated inputs item #8 says make the 1X2 figure "a verdict on nothing". It is a hypothesis, not evidence. |
| **5** | Backfill `recommended_bookmaker` on 8 per-book trigger bots | **STILL VALID — but re-justified, and "Blocks #6" is FALSE** | **NEVER STARTED** | `git log --all --grep=RECBOOK` → exactly one commit (`f26a89c`), which backfilled **one unrelated bot**: migration 349 is `WHERE bot_id = (SELECT id FROM bots WHERE name = 'bot_ou35_model_v1')`. DB now: **496 NULL of 2,587 trigger-bot rows = 19.2%**; sharp 1x2 bots **69 of 219 = 31.5%**. The count has *grown* by 8 since the list was written. `BOOK_MARKET_BOTS` at `workers/jobs/pick_trigger_matcher.py:29`. | **Two corrections.** (1) **The apparent blocker does not apply.** `f26a89c` forbids generalising the backfill to *"multi-book bots, **where the book that set the best price varies per pick**"*. The 8 trigger bots are single-book by construction — `("Coolbet","1x2","model_1x2") → bot_coolbet_trigger_1x2_v1` can only ever have been priced at Coolbet, the identical certainty migration 349 used. The one genuine multi-book bot, `bot_trigger_1x2_sharp_tight_v1` (`pick_trigger_matcher.py:51`), already has 0 NULLs. (2) **"Blocks #6" (list line 20) is wrong.** Of the 1,248 venue-less bookless CLV rows, item 5's bots own exactly **488 = 4.1%** of item 6's 11,822-row population; 760 (61%) belong to bots item 5 does not cover (`bot_acca_leg_shadow` alone is 533). #6 reaches 8.1% bookless *without* #5. **The real argument is different and better:** 65 of those 488 sit on `bot_coolbet_trigger_sharp_1x2_v1` and `bot_unibet_trigger_sharp_1x2_v1` — the two bots whose promotion verdict is *the* open question at n=66 and n=67. Backfilling roughly **doubles the n on both sides of that comparison**. |
| **6** | Port `DIRECT-BOOK-CLV` to `shadow_bets` | **RESCOPED — payoff 8–16× smaller, and the port is partial** | **ABANDONED** (half-ported) + **DEFERRED** (backfill) | Code landed in `de7858c`, `workers/jobs/settlement.py:3732-3739` — own book or nothing, no substitution. But **the metric has not moved at all**: bookless CLV rows = **106,726**, bit-identical to the item's own baseline, because only 1 row has settled through the new path. `_direct_book_close` **does not exist** — the item's instruction is unfollowable as written; the real mechanism is the 4th arg of `get_closing_odds` (`settlement.py:791`). | **Three corrections.** (1) **Scale:** "~112k rows" counts each bet ~8×. `shadow_bets` 161,637 raw vs `shadow_bets_unique` 19,962 — the honest payoff is **~11.8k unique rows**, not 112k. (2) **Partial port:** the `real_bets` sibling (mig 332) has a freshness bound `DIRECT_CLOSE_MAX_MIN` and records `closing_minutes_before_ko`; the shadow path has neither, so a price 5h pre-kickoff counts as a "close". (3) **The deferral does not survive as a reason.** Verbatim: *"Historical rows are NOT rewritten. `closing_odds IS NOT NULL AND closing_bookmaker IS NULL` is the marker… exclude those from any gate or published figure."* That is a detection convention, not a judgement that recomputing is wrong — and migration 332 line 28 shipped exactly that backfill for `real_bets` (`scripts/backfill_real_bets_direct_clv.py`, pinned at `smoke_test.py:39725`). The shadow half shipped no equivalent. **Feasibility measured, and it passes:** of 11,822 bookless unique CLV rows, 10,574 (89.4%) have a venue, and **10,276 (97.2% of those) are recoverable** under `get_closing_odds`'s real semantics — `is_closing` alone recovers only 5,257, because the direct-scrape feeds are barely ever stamped closing (Coolbet 1.09%, Epicbet 2.08%, Unibet-Site 5.70% vs Bet365 53%), but `CLOSING-PRE-KO-FALLBACK` (`settlement.py:874-890`) takes the latest `timestamp <= m.date` row. Backfilling leaves **1,546 bookless = 8.1%**, i.e. the item's own `<10%` criterion is attainable without #5. |
| **7** | Re-check the three 2026-09-14 1x2 retirements | **ALREADY DONE — close** | **OVERTAKEN** | The re-check happened the same day under another name: `docs/OWN_BOOK_UNIVERSE_2026_09_14.md:105-130` re-derived all three on **per-book-margin own-book CLV** — `bot_trigger_1x2_model_v1` −6.10%, `bot_unibet_trigger_1x2_v1` −5.41%, `bot_coolbet_trigger_1x2_v1` −6.97% — reproduced independently from the DB to the rounding digit. All three are `is_active=f` in the DB and their specs are gone from `bot_registry.py:82-90`. | **The decision survives; its recorded reason does not.** On the own-book basis two of the three are CLV-**positive** (+1.70%, +2.73%) and are retired only via the margin correction. `bot_registry.py:84-85` and migration 336 still cite *"CLV −8.4% to −9.2% … t=−7.1..−14.5"*, which is the pre-fix arbitrary-book number and **could not be reproduced from `shadow_bets.clv` under any filter**. Close the item; carve out a 15-minute comment fix. |
| **8** | Re-measure shrinkage alpha after the retrain | **STILL VALID — but cannot be started** | **BLOCKED ×2, neither cleared** | `SELECT … WHERE fitted_at >= '2026-09-14'` → **0 rows**. Latest fit is 2026-09-13 23:54:50; baseline confirmed exactly (`shrinkage_alpha_t1_1x2` 0.0085, t2 and t4 exactly 0). | **Blocker 1 — the era marker does not exist.** `scripts/fit_blend_weights.py:120-137` still has no date, era or `model_version` predicate; a grep for `era_marker`/`ERA_START` finds one hit in an unrelated file. **Blocker 2 — the clean model is not serving.** `v20260914_clean` and `_cut0820` are trained and registered with `promoted_at IS NULL`; zero rows in `predictions` or `pick_triggers` carry them (served: `v20260903_cut0820`). So a refit today would read four months of inverted rows and produce another verdict on nothing. **Leave open, do not start.** |
| **9** | Arm `MODEL-OUTPUT-CALIBRATION` | **RESCOPED — it needs a fix BEFORE it arms** | **DEFERRED / data gate, correctly implemented** | The test body is complete (`smoke_test.py:40416-40490`); only the sample is missing. `raise SkipTest` at `:40480`. Reproducing its own SQL: best pair is `ensemble/1x2_home` at **n=44 of MIN_N=500**. Daily settled volume 115–765, so it arms in roughly **1–2 days**. | **A real defect found while auditing.** The test filters `p.created_at >= '2026-09-14'` — midnight — but `1X2-CLASS-ORDER-INVERTED` landed at **07:08 UTC**. The 00:00 UTC batch alone is 1,179 ensemble rows created *before* the fix. Restricted to the true fix time, only **5** settled post-fix rows exist. **It will arm on a contaminated window** — the exact "red on arrival" failure its own docstring was written to avoid. Also worth watching: `xgboost/1x2_home` currently reads corr **−0.3989** on a pre-fix n=20. Fix the boundary this week; it is ~10 minutes and it expires in ~2 days. |
| **10** | Phantom `pinnacle_implied_*` features | **STILL VALID — premise narrowed** | **DEFERRED** (reason recorded, still holds) | MFV has 2 of the 5: `pinnacle_implied_over25`/`under25` are real columns (19.1% coverage). **Exactly 3 features + 3 flags are phantom** (`_home/_draw/_away`). The 100% pinned-flag rate is *structural, not statistical*: `xgboost_ensemble.py:236` does `SELECT *`, so `raw.get("pinnacle_implied_home")` is `None` on every row that will ever exist (`:250-252`, `:258`). Gain cost on the served bundle: **result_1x2 6.06%**, over_under 5.23%, btts 5.76% — reproduces the item's "~6%". | **Today's retrain inherited them.** `v20260914_clean/feature_cols.pkl` (67 features) still contains all ten names, identical to the served bundle. The recorded reason to remove rather than add still holds (`PRODUCT_FIX_PLAN:83`: *"Cannot be fixed by adding the column — training takes the latest pre-KO Pinnacle price, i.e. effectively the close"*). **Scope correction:** it is a two-file change — `smoke_test.py:12083` actively *pins* `"pinnacle_implied_home"` into `INFORMATIVE_MISSING_COLS`, so removing it fails CI until the test moves too. |
| **11** | Imputation mismatch train vs serve | **STILL VALID — both halves** | **DEFERRED in a code comment that no longer holds** | Training fills per-league means (`workers/model/train.py:86-94`); serving fills `0.0` (`xgboost_ensemble.py:255-258`). Fold leak confirmed: `_impute_features` runs at `train.py:231` *before* `TimeSeriesSplit` is constructed at `:236`, so every fold sees means computed over future folds. | The serving comment justifies itself with *"the indicator column carries the real signal"*. That defence is **weaker after today**, not stronger: the clean retrain pushed `*_missing` nominal gain from 13–18% to **20.8–28.9%** (see #20), i.e. more of the model now rides on the indicators that this mismatch corrupts. `PRODUCT_FIX_PLAN:85` already retracts the comment — *"The serving code has a comment acknowledging the mismatch and calling it acceptable. It isn't."* |
| **12** | Swap `n ≥ 334` for power + regime conditions | **STILL VALID — promote** | **NEVER STARTED** | `CLV_USEFUL_N = 334` at `scripts/trigger_calibrator_check.py:63`, consumed by the auto-paging watcher `job_trigger_calibrator_watch` (`workers/scheduler.py:2010`), and **pinned** by `TRIGGER-CALIBRATOR-WATCH` asserting `CLV_USEFUL_N >= 300` (`smoke_test.py:38579`). No `required_n(effect, sd)` anywhere. | **Today corroborated it twice and encoded it nowhere.** (a) Migration 348 retired five bots using exactly the reasoning this item asks for — *"promotion needs power to detect a SMALL positive effect, so it needs n; retirement of something sitting 5-6pp BELOW break-even… needs far less"* — as **prose in a migration**, so the next agent re-litigates it. (b) The `+16.00%` retraction was an **era selection**, not a parameter: all 92 legs fell in one 11-day window, and the recorded lesson is *"Always print the date span of a cell alongside its n"* — which is precisely the regime condition this item specifies. Bias, not power, was the binding constraint, exactly as filed. |
| **13** | Pinnacle-movement cut as a standing evaluation column | **RESCOPED — surface shrank, urgency concentrated** | **NEVER STARTED** | The proposed home is `scripts/bot_segment_table.py --pinnacle-moved` (`SHARP_ANCHOR_AUDIT:38`). No such flag; the file is untouched since 2026-09-13 22:59. Baseline reproduced in the audit doc: static lines **+14.95%** (n=88) vs genuinely moved >5% **+2.42%, t=+1.1** (n=61); **45% of picks move less than 0.5%**. | **The bot fleet moved off Pinnacle CLV today.** `scripts/bot_status_board.py` and migration 348 judge on margin-corrected **own-book** `clv`, which is not circular with a Pinnacle-anchored selection. So the item no longer applies fleet-wide. **What is left is sharper:** `clv_pinnacle` still drives `scripts/trigger_calibrator_check.py:73,159` — the watcher that will auto-page a promotion verdict — plus `clv_gate_report.py`, `bot_inventory.py`, `odds_band_by_market.py`. Rescope to "the movement split is mandatory wherever `clv_pinnacle` is still a decision variable", and do it in the same pass as #12, which lives in the same file. |
| **14** | Draw `cal_prob` is a constant | **STILL VALID — finding confirmed, baseline withdrawn** | **UNKNOWN — no reason recorded anywhere** | Root cause identified and it is the **calibrator, not a dead head**: `model_calibration` `1x2_draw` holds `platt_a = 0.39067`, `platt_b = -1.28659`, unchanged since 2026-09-13 23:54 (and effectively unchanged since 09-09). With a ≈ 0.39 the logistic compresses everything into a narrow band: `v20260712+selcal1` draws sit within **0.000126** of each other on n=79 (sd 5.08e-05). The control is decisive — `sharp_1x2` (`pinnacle_shin_devig`, no model) gives **381 distinct values on 381 rows** over [0.0907, 0.4145] on the same day through the same pipeline. | **Withdraw the quoted baseline.** `[0.3050, 0.3072]`, sd 0.0004, n=96 does **not reproduce** from `pick_triggers`, `shadow_bets`, `simulated_bets` or `picks_forward_test_shadow`; `git log -S"0.3050"` finds only the doc commits that assert it. It is another prose-only number of the class `PLAN_AFTER_AUDITS` §6 identifies. The *finding* survives on better evidence than it was filed with. Nothing today changed it (zero `model_calibration` rows refitted on 2026-09-14). `git log --all --grep` over `cal_prob` / `draw head` / `DRAW-` returns no commit addressing it — **no reason was ever recorded for leaving it.** |
| **15** | `OU-CALIBRATOR-REFIT-ON-SHRUNK` | **RESCOPED — the question is "any curve vs no curve"** | **DEFERRED, reason recorded in three places, still holds** | Migration 335 **deleted** the rows rather than refitting, and says why at `335_ou_calibrator_domain_mismatch.sql:36-44`: *"WHY DELETE RATHER THAN REFIT HERE … A corrected refit is a separate, measured decision — see `scripts/ou_calibrator_backtest.py`"*. Verified live: no `over_under_*` row exists in `model_calibration`; O/U stage 2 is a silent no-op today. `fit_calibration_from_predictions.py:281-287` and `smoke_test.py:29644` both name the ticket as the intended unblock. | **Most of the item is already built.** `scripts/ou_calibrator_backtest.py` already fits arm C on `shrunk` (`:250-255`), scores a time-ordered TEST slice, applies the edge floor *before* scoring (`:206-212`) and prints the gap on the **selected** subpopulation — the exact validation the item specifies. What remains is run → decide → unlock the fitter's `--i_have_fixed_the_domain_mismatch` refusal (`:287`). **And the null currently wins:** no curve = CLV **+24.7% (t=+5.5)** vs live curve **−1.57% (t=−2.4)** (`smoke_test.py:39878`). The honest framing is not "fix the curve" but "prove any curve beats none". |
| **16** | O/U blend weight is the 1x2 blend weight | **STILL VALID** | **DEFERRED as low leverage; fold into #15** | `load_blend_weight` reads only `blend_weight_1x2_t{tier}` (`xgboost_ensemble.py:513`) and `blend_weight_1x2` (`:524`); the O/U leg consumes the same `pw` at `:574`. DB: **no `blend_weight_ou*` key has ever existed**. Latest global value 0.8553, so O/U is blended 86/14 on a weight fit against 1x2 outcomes. | Recorded reason (`PRODUCT_FIX_PLAN:86`): *"Low leverage now — it scales a leg that gets 23% weight on O/U, 0.85% on 1x2."* Still holds. Note it is **not** covered by the α = 0 finding — that was 1X2 only. |
| **17** | `/picks` publishes a frozen `odds_at_pick` | **ALREADY DONE — close** | **OVERTAKEN** | The surface the item describes no longer exists. `/picks` was rebuilt today (`b894cb2` engine, `80aedd1` web) to read `picks_forward_test` via `src/lib/forward-test-picks.ts`; `src/app/picks/page.tsx` imports nothing from `upcoming-picks.ts` and `odds_at_pick` appears nowhere in its read path. | The success criterion — *"price age p95 below 1h, **or** the staleness is disclosed on the page"* — is met by the replacement. `picks_forward_test` records `odds_quoted_at`, `anchor_quoted_at` and `alignment_gap_minutes` **per row at publish time** (mig 342), the page renders the gap ("`42m apart`") and a break-even `min` price whose tooltip says verbatim *"Odds move after a pick is posted, so check the price you are actually offered against this."* Close it. |
| **18** | `odds_drift`/`steam_move` unbounded query | **STILL VALID — and worse than filed** | **OVERTAKEN by a class fix that missed it / UNKNOWN** | `workers/api_clients/supabase_client.py:1514-1521` still selects `FROM odds_snapshots WHERE match_id = ANY(…) AND market='1x2' ORDER BY timestamp ASC LIMIT 10000` — no `is_live`, no `timestamp <= m.date`, no `minutes_to_kickoff`. `snaps[-1]` at `:1912` becomes `odds_drift_home`/`steam_move` at `:1914`. Inertness **verified, not assumed**: neither column is in `feature_cols.pkl` for the served `v20260903_cut0820` or for `v20260914_clean`; the only readers are research scripts. | **Why it survived the class sweep — and this is the part to escalate.** `AF-ISLIVE-UNRELIABLE` (`2f32a65`) bounded 13 statements in this file and shipped a guard, but the guard at `smoke_test.py:35441` reads `if "is_live = false" not in seg: continue`. This query contains no `is_live` clause **at all**, so it is skipped silently — **a guard defeated by being more wrong than it checks for.** A second unfiled hazard sits in the same statement: `LIMIT 10000` across a 200-match chunk can truncate mid-match, making `snaps[0]` not the opening. |
| **19** | `train_ah_xgboost.py` random split | **STILL VALID — lowest priority, correctly** | **DEFERRED: dormant by design, reason verbatim** | `scripts/train_ah_xgboost.py:200` is still `StratifiedKFold(n_splits=5, shuffle=True, random_state=42)`, unchanged since 2026-09-05. Dormancy verified by absence: `grep "ah_xgb"` returns **nothing in `workers/`** — no loader, no import, no env gate. Live AH pricing is the Poisson/Dixon-Coles `_ah_model_prob` (`daily_pipeline_v2.py:1325`). | Commit `5141ce4` recorded a 4-step activation plan at creation time (*"Not auto-activated… 4. Env-gate it behind `AH_XGB_ENABLED=true`"*); none of the four ever ran. The leak cannot reach production and fixing the split only invalidates the one number anyone quotes (CV AUC 0.7308 ± 0.0205). Keep open, keep last. |
| **20** | Ablate the `*_missing` indicators | **OBSOLETE as filed → RESCOPED** | **OVERTAKEN** (original) / **NEVER STARTED** (rescoped) | The question was answered **ten days before the list was written**. Commit `b0510d5` (2026-09-04) *"MODEL-LEANS-ON-MISSINGNESS: not a real fragility"*: permutation ablation on the OU head, n=6,847, 5 repeats — permuting all 15 `_missing` indicators costs **+0.0020** log-loss vs **+0.0086** for 3 real features of equal nominal importance, i.e. **0.23× the dependence**. Filed permanently as `ANALYSIS_GOTCHAS` §36 with the methodological rule: permute, do not read gain. | **But the premise moved, and that part is new.** §36's ablation was run on the *leak-trained* model at 13–18% nominal gain. Re-measured off the bundles: `result_1x2` 17.7% (served) → **28.9%** (`v20260914_clean`); over_under 15.1% → 23.7%; btts 13.2% → 20.8%. The clean retrain removed the ELO leak and the `_missing` flags absorbed the vacated weight. **Rescope to: re-run the 2026-09-04 permutation ablation against `v20260914_clean` before promoting it** — §36's method stands, its conclusion is about a superseded model. |

---

## Findings — things that are not status notes

These were found while auditing. They are live, none is on the task list, and
the first two outrank most of it.

### F1. `resettle_wrongly_voided_bets` can re-fabricate an arbitrary-book CLV — including one wearing a valid book label

`de7858c` closed the arbitrary-book fallback on *one* of the two paths that
write `shadow_bets`. The other is registered and running: `settlement.py:2070`
calls it unconditionally inside `settle_ready_matches()` every settlement cycle,
and the smoke suite pins that wiring (`smoke_test.py:25968`).

Its write, `settlement.py:2282-2286`, updates `closing_odds` and `clv` from the
**unfiltered** three-arg `get_closing_odds` at `:2272`, over
`table in ("shadow_bets", "simulated_bets")` — both surfaces `de7858c` and
`bb520a4` were meant to protect.

**`closing_bookmaker` is not in the SET list.** A row that already carries a
valid `closing_bookmaker` keeps it while its `clv` is overwritten with an
arbitrary-book value — a fabricated number wearing a valid label, invisible to
`de7858c`'s own detection marker and counted as own-book by every gate.

Measured now: **405 rows** match `_WRONGLY_VOIDED_SQL` in `shadow_bets`, of which
**133 carry a non-NULL `closing_bookmaker`** — the undetectable path. Post-fix
fabricated rows today: **0**. The hazard has not fired; nothing prevents it.

The existing guard cannot catch it: it is anchored on `created_at >= ` the fix
date, and every row this sweep touches predates the fix by construction.

This is the `RELIABILITY_LEDGER` pattern *"a second code path inheriting no
gates"*, on the single most consequential number in the system now that CLV is
the only promotion gate. **Caution for whoever fixes it:** the obvious one-line
patch does not compile — `_WRONGLY_VOIDED_SQL` (`:2084-2101`) selects neither
`recommended_bookmaker` nor `closing_bookmaker`, so the column is not available
in `bet` at that point.

### F2. `AF-ISLIVE-PREMATCH-GUARD` is defeated by removing the symptom

`smoke_test.py:35441` only inspects SELECT segments containing the literal
`is_live = false`. A query that omits `is_live` entirely passes the guard **by
being more wrong**. That is how item #18's query survived a sweep whose own
commit body claims to have bounded 13 statements in that file. The guard should
key on `odds_snapshots` + absence of a kickoff bound, not on the symptom text.

### F3. An open item was lost between the two lists

`PRODUCT_FIX_PLAN_2026_09_14.md` **P1-7 — post-hoc feature backfill (train/serve
skew)** has no row in the master list. It is not a duplicate of #11 (that is
*imputation strategy*; this is *feature availability*). It is the canonical
"good offline, useless live" signature, and the master list's own #2 note
gestures at it — *"that is task #7's job"* — pointing at a number that in the
master list means something else entirely. Confirmed live from the DB:

| column | NULL rate, past matches (training) | NULL rate, upcoming (live inference) |
|---|---|---|
| `season_progress` | 5.0% | **88.9%** |
| `league_clv_efficiency` | 63.4% | **100.0%** |
| `goals_for_avg_home` | 22.5% | **50.0%** |

(n = 40,431 past / 54 upcoming, 120-day window. The upcoming n is small; the
pattern matches the audit's independently measured 2.0% vs 96.5%.)

### F4. Two defects found by today's market-expansion sweep are in no list

`OWN_MARKET_EXPANSION_2026_09_14.md:149` recommends two rows and neither is in
`PRIORITY_QUEUE.md` (`grep` → 0 hits each). The sweep deferred filing them
because another agent held that file:

* **`HT-STATS-FULL-MATCH-FALLBACK`** — `api_football.py::parse_fixture_stats_halftime`
  falls back to the full-match `statistics` array when `statistics_1h` is
  absent, so **54.2% of `*_ht` rows hold full-match values**. Nothing bets on it
  today, but 303,120 `corners_1h_ou_45` snapshots are accumulating against a
  label that is wrong on half the rows.
* **`SETTLEMENT-1H-TOTALS-GRADED-ON-FULL-TIME`** — `_r_ou_goals` matches on the
  substring `"over_under"`, so `over_under_1h_15` would grade against the
  full-time score. Latent, same shape as the bug `_r_1x2_1h` exists to prevent.

### F5. Stale reasons that will cause someone to re-litigate a settled decision

* `bot_registry.py:84-85` and migration 336 justify the three 1x2 retirements
  with pre-fix arbitrary-book CLV (−8.4% to −9.2%) that **cannot be reproduced**
  from `shadow_bets.clv` under any filter. The retirements are right on today's
  numbers; the file says why for the wrong reason.
* `bot_registry.py:92-95` still says *"The four MODEL-anchored O/U trigger bots
  are deliberately NOT retired"* and points at
  `dev/active/HELD_retire_model_anchored_ou_losers.sql` — but **migration 348
  retired three of those four today**. The staged SQL file is still in the tree.
* Two dead duplicate constants named `_INFORMATIVE_MISSING_COLS`
  (`xgboost_ensemble.py:220`, `offline_eval.py:85`) are referenced **zero**
  times and already list 8 and 11 entries against `train.py`'s 15. Harmless only
  because the real logic uses `col.endswith("_missing")`.

### F5b. Ripple: `PRODUCT_FIX_PLAN_2026_09_14.md` is now the stalest doc in the set

It is the master list's parent and still carries **27 ⬜ against 11 ✅**, including
`P0-1` and `P0-2`, whose work landed today. Its item bodies are still the best
write-up of several defects (they are quoted throughout this audit), so it should
not be deleted — but its **status column is wrong** and someone will read it as a
to-do list. Not edited here: this audit's brief allows writing one report and
updating the master list only. **Recommended: carry the status cells across from
the master list, or banner it as superseded by that list.**

### F6. One "done" row is done but unexercised — verify tomorrow, do not re-open

The master list's Done table claims `ALPHA-IS-AN-UNREAD-INSTRUMENT` (mig 338)
means *"`ll_model`/`ll_market` now recorded instead of printed and discarded"*.
All 1,643 `model_calibration` rows have both columns **NULL**. This is **not** a
half-wired path: `fit_blend_weights.py:419,439-440` does write them; the
migration landed 06:34 UTC today and the fitter last ran 2026-09-13 23:54 UTC,
i.e. it has not run since. It should populate at tonight's settlement blend
refit. **Check the columns tomorrow.** If they are still NULL, it *is* broken.

---

## Reasons that changed state today

**No longer true — unblock:**

* **#5's apparent blocker.** `f26a89c`'s "never generalise the backfill" is
  scoped to multi-book bots and does not cover the 8 single-book trigger bots.
* **#6's deferral.** "Historical rows are not rewritten" is a detection
  convention, and the identical backfill shipped for `real_bets` (mig 332).
  Feasibility is now measured, not assumed: 10,276 of 11,822 bookless rows are
  recoverable, landing the bar at 8.1%.
* **A dependency that never existed.** The list says #5 "Blocks #6". It does
  not — #5 supplies 4.1% of #6's population. Both are worth doing; neither
  waits on the other, and sequencing them cost a day that was never needed.
* **#13's scope.** Bot verdicts moved off `clv_pinnacle` today, so the item
  shrinks to the surfaces that still use it — chiefly the auto-paging watcher.

**Still true — leave paused, and leave the reason visible:**

* **#8** — no era marker, and the clean model is not promoted. Starting it
  produces another verdict on nothing.
* **#15 / #16** — migration 335's *"a corrected refit is a separate, measured
  decision"* holds, and the measured comparison currently favours **no curve**.
* **#19** — dormant by design; the leak cannot reach production.
* **#10** — "remove, do not add the column" is still the right call; the
  training price is effectively the close.

**Dangerous partial work still in the tree:**

* **F1** — the un-gated `resettle` write path (live, running).
* **F2** — a guard that passes the thing it was written to catch.
* `dev/active/HELD_retire_model_anchored_ou_losers.sql` — staged SQL for bots
  three of which migration 348 already retired.
* `data/models/ah_xgb/v_20260525/` — a trained bundle with no loader and a
  4-step activation plan nobody executed (safe, but unaccounted for).

---

## What should actually be worked next

Ranked. Each line is the justification.

1. **Gate `resettle_wrongly_voided_bets` (F1).** A running job can overwrite
   `clv` on 133 rows that already carry a valid closing book, producing a
   fabricated number no guard can see — on the metric that is now the *only*
   promotion gate. ~1h; needs two columns added to `_WRONGLY_VOIDED_SQL`.
2. **#6 — the historical own-book CLV backfill.** The code shipped; the metric
   has not moved one row (106,726 bookless, unchanged). Measured as feasible:
   **10,276 of 11,822 bookless unique rows are recoverable**, taking the bar to
   **8.1%** — it clears its own `<10%` criterion on its own. Quote the honest
   payoff (~11.8k unique rows, not 112k). **Add a `closing_minutes_before_ko`
   equivalent in the same pass**: recovery for the placeable books leans on the
   pre-KO fallback, and Coolbet's sweep cadence means a backfilled "close" is
   often a ~3h-stale price. Without that column the item trades one silent bias
   for a quieter one. 1d.
3. **#5 — backfill `recommended_bookmaker` on the 8 trigger bots.** *Not* the
   gate for #6 (it contributes 4.1% of that population). Do it for the real
   reason: 65 of its rows sit on the two sharp 1x2 bots whose verdict is the one
   open promotion question, and it roughly **doubles the n on both sides** of a
   comparison currently decided at n=66 vs n=67. 2h.
4. **#9 — fix the arming boundary before it arms (~2 days).** Ten minutes of
   work with a hard expiry: `created_at >= '2026-09-14'` admits 1,179 pre-fix
   rows, so the calibration canary will arm on a contaminated window and either
   pass falsely or go red on arrival.
5. **#18 + F2 — bound the drift query, then fix the guard that missed it.** The
   query is inert today, but the *guard* is not: it silently skips any query
   that omits `is_live`, so the whole class fix is weaker than its commit claims.
6. **#12 + #13 together, in `trigger_calibrator_check.py`.** One file holds both
   the bare `n ≥ 334` and the last `clv_pinnacle` decision surface, and it is
   wired to auto-page a promotion verdict. Today produced the doctrine (mig 348's
   asymmetric n) and the counter-example (the +16% era selection) and encoded
   neither.
7. **#20-rescoped — permutation ablation on `v20260914_clean` before promoting
   it.** `*_missing` nominal gain rose to 20.8–28.9% on the clean bundle; the
   only ablation we have was run on the leaked one. Cheap, and it gates a
   promotion.
8. **F3 / P1-7 — train-serve feature availability.** `league_clv_efficiency` is
   100% NULL at inference and 63% NULL in training. This is the textbook
   "good offline, useless live" signature and it is on no list.
9. **#4 residue — the residual test on O/U and BTTS.** 1X2 is settled at α = 0.
   The goalline α of 0.15–0.29 is the only instrument anywhere suggesting the
   model contributes something, and it has never been tested the way 1X2 was.
   Blocked behind #8's era marker if you want a clean answer.
10. **#11, then #10.** Both are real, both are model-quality work, and neither
    pays until something clean is promoted.

**Explicitly not next:** #7 and #17 (close them), #8 (blocked — do not start),
#15/#16 (the recorded deferral holds and the null currently wins), #19 (dormant).

---

## Scope of this audit

Read-only on code and DB. No source file, migration, `PRIORITY_QUEUE.md`,
`scripts/own_*.py` or `workers/jobs/settlement.py` was modified — those were held
by other agents. **No smoke test was added**, which the task lifecycle normally
requires, because this pass writes no code: the deliverables are this document
and the status update to `MASTER_TASK_LIST_2026_09_14.md`. Each fix that comes
out of the shortlist carries its own test obligation.

Four independent passes ran over the 17 items (three verification agents plus a
direct pass); every number quoted above was recomputed from source, the DB or
git, not read from a doc.

## What could not be verified, and why

1. **VPS environment state.** `MODEL_VERSION`, `MODEL_VERSION_1X2` and
   `SHADOW_MODEL_VERSION` are unset in the local `.env`; promotion is a manual
   flip on the box. "The clean model is not serving" is inferred from served
   `model_version` values in `predictions` and `pick_triggers` plus
   `promoted_at IS NULL` — strong, but indirect.
2. **Whether `50ec734` (the 1X2 inversion fix) is deployed**, as distinct from
   committed. Post-fix `xgboost` sample is n=2 — too thin to tell. Per
   `ENGINE-DEPLOY-2026-08-24`, assume nothing about what is live on the box.
3. **Migration 336's `−8.44% / −8.68% / −9.16%`** could not be reproduced from
   `shadow_bets.clv` under own-book, arbitrary-book or pooled filters. Presumably
   a `scripts/` harness applied extra book exclusions. Not wrong — not
   reproducible from the columns.
4. **Item #14's baseline** `[0.3050, 0.3072]`, sd 0.0004, n=96 does not
   reproduce from any table. Withdrawn as a quotable number; the finding it
   described is confirmed by other means.
5. **`scripts/ou_calibrator_backtest.py` was not run.** The +24.7% vs −1.57%
   comparison is quoted from the committed smoke test and migration text — a
   record of a run, not a doc asserting a fix, but not re-executed here.
6. **The `*_missing` gain percentages are nominal, not dependence.** They are
   gain-based `feature_importances_` off the saved bundles. Per §36's own rule,
   treat them as weight until the permutation re-run (shortlist item 7).
7. **The Asian-handicap share of #6's recoverable count is a slight over-count.**
   `asian_handicap` embeds its line in the selection (`"home -0.5"`); the
   feasibility join took `split_part(sel,' ',1)` and did **not** match
   `handicap_line`, so those 1,111 rows are matched loosely. Everything else
   joined on an identity normalisation — `shadow_bets.market` already stores the
   `odds_snapshots` vocabulary — and a join ignoring market/selection entirely
   moved the strict count only 5,257 → 5,272, so mis-normalisation is not what
   limits recovery.
