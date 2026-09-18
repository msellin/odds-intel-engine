# Product fix plan — merged, 2026-09-14

> # ⛔ RETIRED AS A TASK LIST — 2026-09-18
>
> **This file is no longer a backlog. It is a dated snapshot, kept for its evidence.**
>
> It was a SECOND task list running alongside `PRIORITY_QUEUE.md`, which never referenced it — so
> its items were invisible to every triage of the master queue. The 2026-09-18 consolidation
> re-verified every still-open item against code and the database and promoted the survivors into
> the master as **`MODEL-TRAINING-DEBT-2026-09-18`** (and `DIRECT-BOOK-CLV-SHADOW-BACKFILL-2026-09-18`).
>
> **Do not add work here. Do not work from the status cells below — several are out of date**
> (e.g. the ELO/form leakage this file lists as open was fixed; `corr(elo_diff, home_win)` fell
> 0.4317 → 0.2100). `PRIORITY_QUEUE.md` is the single master task list.



Merges three independent workstreams: the O/U-calibrator investigation, the
training-path audit, and the parallel sharp-anchor audit
(`docs/SHARP_ANCHOR_AUDIT_2026_09_14.md`). Ordered by **impact on what we
believe or what we stake**, not by effort.

**The organising fact:** in 48 hours, four separate instruments were found to
have been measuring real defects for months without anyone reading them — the
shrinkage alpha decaying to 0, the blend fitter walking the XGB leg down, the
O/U calibrator's ECE check validating a function nobody ran, and every offline
evaluator scoring a correctly-indexed model that production served inverted. The
common shape is **a number that was computed, not surfaced**. Several tasks below
are therefore about surfacing, not fixing.

---

## Priority table

| # | Task | Impact | Dir | Est | Status |
|---|---|---|---|---|---|
| **P0-1** | **Port `DIRECT-BOOK-CLV` to `shadow_bets`** (audit §4) | **66.9% of all CLV (106,726 of 159,614 rows) was computed against an arbitrary book** — `get_closing_odds()` is called with no bookmaker and its own docstring says which book wins "can change between two runs". Every bot decision in this project rests on this column. Also delivers the non-circular metric on ~112k rows instead of 89, so real time folds exist immediately. | 🤖👥 BOTH — fixes what we stake on AND every published bot figure | 1d | ⬜ |
| **P0-2** | **Fix ELO/form leakage in the training table** | `update_elo_ratings()` writes post-match ELO stamped with the match date, and runs BEFORE the ETL reads `date <= date_str`. **79.5% of training rows carry an ELO that absorbed their own match.** Stored `elo_diff` AUC 0.745 vs 0.617 genuinely pre-match — above the de-vigged market's 0.727, which an ELO cannot be. elo+form = **39% of 1X2 model importance**. Until this is fixed no model result means anything. | 🤖👥 BOTH | 2-3d | ⬜ |
| **P0-3** | **Backfill `recommended_bookmaker` on the 8 per-book trigger bots** (audit §3) | 488 of 2,445 trigger rows (20%; ~33% on the sharp 1x2 bots) have no venue, so they cannot be priced executably even in principle. This is the n=140→89 gap. Recoverable **with certainty** from the bot name via `BOOK_MARKET_BOTS`. **Blocks P0-1** (NULL-blocked without it). ⚠️ Do NOT backfill by price-matching: only 36% of 400 sampled rows resolve to one book. | 🤖 OWN | 2h | ⬜ |
| **P1-4** | **Re-check the three 2026-09-14 1x2 retirements** after P0-1 | `bot_coolbet_trigger_1x2_v1`, `bot_unibet_trigger_1x2_v1`, `bot_trigger_1x2_model_v1` were retired on CLV measured against an arbitrary book. The decision may or may not survive a correct metric. | 🤖 OWN | 2h | ⬜ |
| **P1-5** | **Retrain + re-measure alpha once P0-2 lands** | The alpha is the honest verdict on whether a model business exists. Currently it reads 0.0085 / 0.0000 (1x2) on inverted + leaked inputs, so it is not yet a verdict on anything. ⚠️ `fit_blend_weights` fits over ALL history with no date bound, so inverted rows will depress alpha for months — needs an era marker (`+selcal1` precedent). | 🤖👥 BOTH | 1d | ⬜ |
| **P1-6** | **Phantom features: `pinnacle_implied_home/draw/away`** | In `feature_cols.pkl` but **not columns of `match_feature_vectors`** — `raw.get(col)` → None → 0.0 with the missing-flag pinned to 1 for **100% of production rows, forever**. 3.2% of importance dead plus ~3% on permanently-pinned indicators. The weekly retrain passes `--include-pinnacle`; the comment directly above it rejects `--include-drift` for this exact reason. ⚠️ Must NOT be "fixed" by adding the column — training takes the LATEST pre-KO Pinnacle price, i.e. effectively the close, which inference never has. | 🤖👥 BOTH | 4h | ⬜ |
| **P1-7** | **Post-hoc feature backfill (train/serve skew)** | Features written by 23:05–23:45 UTC crons, i.e. after the match: `season_progress` 2.0% NULL in training vs **96.5% live**; `league_draw_rate_ytd` 37.4% vs 96.5%; `league_clv_efficiency` 62.1% vs **100%**; `line_velocity` 74.1% vs 100%; `goals_for/against_avg_*` 23.8% vs 62.5%. A feature dense in training and absent live is the exact "good offline, useless live" signature. | 🤖👥 BOTH | 1d | ⬜ |
| **P1-8** | **Imputation mismatch train vs serve** | `train.py:83-97` imputes NULLs with the **per-league mean**; `xgboost_ensemble.py:255-260` fills **0.0**. A missing `opening_implied_home` is ~0.45 in training and 0.0 at serve — a different leaf. The per-league means are also computed over the whole frame before `TimeSeriesSplit`, so CV folds see future-fold means. | 🤖👥 BOTH | 4h | ⬜ |
| **P1-9** | **Replace `n ≥ 334` with a power function + regime conditions** (audit §2) | Observed CLV sd = 0.147, so required n for t=2 is **10 at a +9.2% effect**, not 334. The 334 is calibrated to a ~2% effect and is ritual here. Power was never the binding constraint — **bias was**. Accruing to 334 inside one regime tightens a biased estimate. Replace with `required_n(effect, sd)` plus a real regime condition (N distinct weeks, folds spanning a model-version and book-set change). | 🤖👥 BOTH | 4h | ⬜ |
| **P1-10** | **Pinnacle-movement cut as a standing evaluation column** (audit §1) | Where Pinnacle is static, "our book beats Pinnacle now" and "at close" are the same sentence. Split by movement: static (n=88) **+14.95%**, but genuinely moved >5% (n=61) **+2.42%, t=+1.1 — not significant**. A harder haircut than my +13.18%→+9.21%. Make it impossible to report a Pinnacle-anchored CLV without showing this split. Home: `scripts/bot_segment_table.py --pinnacle-moved`. | 👥 PICKS mainly | 4h | ⬜ |
| **P2-11** | **Draw `cal_prob` is a constant** (new, 2026-09-14) | Live draw picks span **[0.3050, 0.3072], sd 0.0004** on n=96. The `1x2_draw` Platt is a=0.3907 (range [0.216, 0.290]) — the flattest curve in the table. We are emitting a fixed number and calling it a probability. Either the draw head is dead or the calibrator has flattened it to nothing; both are worth knowing before any draw market is published. | 🤖👥 BOTH | 4h | ⬜ |
| **P2-12** | **`OU-CALIBRATOR-REFIT-ON-SHRUNK`** | Fit on `shrunk` (what inference receives), validate on the `edge ≥ floor` SELECTED subpopulation not universe-wide ECE. Backtest says a correct curve beats none: overconfidence gap +5.1pp → **+2.5pp** at the same volume. Not urgent — "no curve" is already safe. ⚠️ The 2-feature variant collapsed the gate to 1 pick; do not ship it without re-tuning the floor. | 🤖👥 BOTH | 1d | ⬜ |
| **P2-13** | **O/U blend weight is the 1x2 blend weight** | `load_blend_weight()` only ever reads `blend_weight_1x2*`; no `blend_weight_ou*` exists. O/U is blended 83/17 Poisson/XGB on a weight nobody fit for it. Low leverage now (it scales a leg that gets 23% weight on O/U, 0.85% on 1x2) — fold into P2-12. | 🤖👥 BOTH | 4h | ⬜ |
| **P2-14** | **`/picks` publishes a frozen `odds_at_pick`** | Up to 6h behind the market / 48h old absolute, frozen at first insert and never refreshed, while `odds_at_pick_live` is computed every 30min and never displayed. Product decision, not a defect. | 👥 PICKS | 4h | ⬜ |
| **P3-15** | **`odds_drift_home` / `steam_move` built from an unbounded query** | `supabase_client.py:1508-1520` has no `is_live = false` filter and no pre-kickoff bound, so "latest snapshot" can be an in-play price on a finished match. **Currently inert** — neither column is in `FEATURE_COLS` and the meta-model uses the bounded `*_at_t6h` variants — but it is a loaded gun in a shared builder. | 🤖👥 BOTH | 2h | ⬜ |
| **P3-16** | **`train_ah_xgboost.py:200` uses a random split** | `StratifiedKFold(shuffle=True)` on football data leaks through team-strength features. AH head only; `train.py` and `train_b_ml3.py` both use `TimeSeriesSplit` correctly. | 🤖 OWN | 1h | ⬜ |
| **P3-17** | **Ablate the `*_missing` indicators** | ~20% of model importance sits on missingness flags (`opening_implied_*_missing`, `market_implied_btts_yes_missing`, `bookmaker_disagreement_missing` are top-10). These plausibly encode data-collection era or league rather than football, and the coverage regime differs at serve time. Suspicion, needs a test. | 🤖👥 BOTH | 4h | ⬜ |

---

## Done in this pass (2026-09-13/14)

| Task | Impact |
|---|---|
| `OU-CALIBRATOR-DOMAIN-MISMATCH` (mig 335) | Curve fitted on raw probs, applied to Pinnacle-shrunk ones. Of 142 picks published since it shipped, **only 2 (1%)** cleared their floor without the sigmoid lift. Drove `bot_v10_all` 14.6%→69.9% O/U share and +38.5%→−15.0% ROI. |
| `1X2-CLASS-ORDER-INVERTED` | Home/away swapped on every served prediction since 2026-05-10. AUC(home) **0.4151 served vs 0.5892 un-inverted** on 4,658 matches. |
| `MODEL-OUTPUT-CALIBRATION` test | The check that would have caught the above in days. Mutation-verified. |
| 4 latent O/U bugs | Settlement grading lost-on-both-sides; `store_odds` missing `handicap_line`; 1xBet 0.25 totals poisoning a training feature; isotonic unreachable for O/U. |
| `ALPHA-IS-AN-UNREAD-INSTRUMENT` (mig 338) | `ll_model`/`ll_market` were computed every run and discarded. Now recorded. |
| `PERF-CHART-EVENT-MARKERS` | Bug window marked on /performance; found the existing markers had silently not rendered in months. |

## Retracted / corrected

- **Mine:** "1x2 has the same compressed shape as O/U, held safe only by the Pinnacle veto." **Not supported.** Live 1x2 `cal_prob` spans [0.249, 0.876], outside the sigmoid's [0.297, 0.679] — something runs after Platt on that path. Re-derive before relying on it.
- **Mine:** the `n ≥ 334` gate in the sharp pre-registration — superseded by P1-9.
- **Audit's:** "the model has usable ranking information" — withdrawn; `corr(cal_prob, odds) = −0.504`, so it is substantially just "short odds", and it orders CLV in only 1 of 3 odds bands.

## Not recommended

Staking or publishing the sharp anchor; re-deriving the 2.2 odds floor from the
window that produced it; running the exploratory grid over five days; adding
`pinnacle_implied_*` to MFV to "fix" P1-6.

---

# Defect register — everything confirmed wrong, 2026-09-13/14

Separate from the task list above: this is *what is broken*, not *what to do*.
Every row is confirmed by reading code, querying the live DB, or both. Suspicions
are marked as such and kept at the bottom. Nothing here is inferred from a doc.

**Impact scale:** 🔴 changes what we believe or stake · 🟠 wrong numbers reach a
decision · 🟡 real but bounded · ⚪ latent (correct today, wrong on the next change)

## A. Model & algorithms

| # | Defect | Impact | Status | Comment |
|---|---|---|---|---|
| A1 | **1X2 home/away swapped on every served prediction** since 2026-05-10. `train.py` maps home→0; inference read `probs[2]` under a comment assuming the opposite. | 🔴 AUC(home) **0.4151 served vs 0.5892 correct** on 4,658 matches. corr with home-win **−0.165**. A predictive model served backwards for 4 months. | ✅ Fixed | Survived because **every offline evaluator indexed `classes_` correctly** — it scored fine everywhere and was wrong only in production. Third instance of this exact class. Production impact today was small (29 of 1,947 candidates change gate) *only because* alpha had already been driven to ~0 — the system defended against it without anyone noticing. |
| A2 | **ELO and form in the training table contain the match's own result.** `update_elo_ratings()` writes post-match ELO stamped with the match date, and runs *before* the ETL reads `date <= date_str`. | 🔴 **79.5% of training rows** affected. Stored `elo_diff` AUC **0.745** vs **0.617** genuinely pre-match. elo+form = **39% of 1X2 importance**. | ⬜ Open | The tell is that 0.745 is **above the de-vigged market's 0.727** — an ELO cannot out-predict the market. This is why the model is still worse than the base rate even after A1 is fixed (0.7283 vs 0.6817). |
| A3 | **O/U Platt curve fitted on raw probs, applied to Pinnacle-shrunk probs.** Range collapsed to [0.3028, 0.6663], fixed point 0.4713. | 🔴 Of 142 picks published after it shipped, **only 2 (1%)** cleared their floor without the sigmoid lift. Drove `bot_v10_all` 14.6%→**69.9%** O/U share and +38.5%→**−15.0%** ROI; **−€467** drawdown. | ✅ Fixed (mig 335) | The fitter's own out-of-sample ECE check *passed* — it validated a function production never executes. Average ECE is the wrong loss for a curve whose only job is to feed a tail gate. |
| A4 | **`pinnacle_implied_home/draw/away` are phantom features** — in `feature_cols.pkl`, not columns of `match_feature_vectors`. | 🟠 `None → 0.0` with the missing-flag pinned to 1 on **100% of production rows, forever.** ~6% of importance dead. | ⬜ Open | The weekly retrain passes `--include-pinnacle`; the comment immediately above it rejects `--include-drift` for *precisely this reason*. ⚠️ Cannot be fixed by adding the column — training takes the latest pre-KO Pinnacle price, i.e. effectively the close. |
| A5 | **Post-hoc backfilled features (train/serve skew).** Written by 23:05–23:45 UTC crons, after the match. | 🟠 `season_progress` 2.0% NULL in training vs **96.5% live**; `league_clv_efficiency` 62.1% vs **100%**; `line_velocity` 74.1% vs 100%. | ⬜ Open | Dense in training, absent at serve — the exact signature of "scores well offline, adds nothing live". |
| A6 | **Imputation differs between training and serving.** Training fills the per-league mean; serving fills `0.0`. | 🟠 A missing `opening_implied_home` is ~0.45 in training and 0.0 at serve — a different tree leaf. | ⬜ Open | The serving code has a comment acknowledging the mismatch and calling it acceptable. It isn't. Separately, the per-league means are computed over the whole frame *before* `TimeSeriesSplit`, so CV folds see future-fold means. |
| A7 | **O/U blend weight is the 1x2 blend weight.** `load_blend_weight()` only ever reads `blend_weight_1x2*`. | 🟡 O/U blended 83/17 Poisson/XGB on a weight nobody fit for it. | ⬜ Open | No `blend_weight_ou*` row has ever existed. Low leverage now — it scales a leg that gets 23% weight on O/U, 0.85% on 1x2. |
| A8 | **Draw `cal_prob` is effectively a constant.** | 🟡 Live draw picks span **[0.3050, 0.3072], sd 0.0004** (n=96). | ⬜ Open | `1x2_draw` Platt is a=0.3907 — the flattest curve in the table. We emit a fixed number and call it a probability. Found while verifying an unrelated claim. |
| A9 | **`train_ah_xgboost.py:200` uses `StratifiedKFold(shuffle=True)`** — a random split on football data. | 🟡 Leaks through team-strength features. AH head only. | ⬜ Open | `train.py` and `train_b_ml3.py` both use `TimeSeriesSplit` correctly, so this is an isolated regression, not a house style. |
| A10 | **Isotonic bundles unreachable for O/U** — named `isotonic_over_25.pkl`, looked up as `over_under_25_over`. | ⚪ Latent | ✅ Fixed | Inert because `STAGE2_CALIBRATOR` defaults to platt — which is *why* it could sit unnoticed: flipping that env var would have read as "isotonic changes nothing" rather than "isotonic never loaded". |

## B. Data & pipeline

| # | Defect | Impact | Status | Comment |
|---|---|---|---|---|
| B1 | **Shadow CLV computed against an arbitrary book.** `get_closing_odds()` called with no bookmaker. | 🔴 **66.9% of all CLV (106,726 of 159,614 rows)** has no known closing book. Every bot decision rests on this column. | ⬜ Open | The function's own docstring says the unfiltered form "is not well defined… which book wins can change between two runs of the same query". `DIRECT-BOOK-CLV` solved this for `real_bets` in migration 332 and was never ported. |
| B2 | **`recommended_bookmaker` NULL on 20% of trigger-bot rows** (~33% on the sharp 1x2 bots). | 🟠 488 of 2,445 rows cannot be priced executably even in principle. This is the n=140→89 gap. | ⬜ Open | Recoverable **with certainty** from the bot name via `BOOK_MARKET_BOTS`. ⚠️ Do *not* backfill by price-matching — only 36% of 400 sampled rows resolve to exactly one book. |
| B3 | **1xBet files 0.25-goal Asian totals under `market='over_under_25'`.** | 🟠 `ou25_bookmaker_disagreement` is max−min across books, so **one** mislabelled row sets the max. Live MFV max **0.9401** against a mean of 0.0534; 138 rows >0.30. It is a *training* feature. | ✅ Fixed | Fixed by keying on `handicap_line` in all three query paths, not by blacklisting the book — a blacklist only chases whoever does it today. |
| B4 | **`store_odds()` never wrote `handicap_line`** — one of three writers missed by OU-LINE-BACKFILL. | 🟡 Its rows landed NULL; any cross-book join keyed on the line dropped them silently. | ✅ Fixed | The line was never ambiguous there — it's in `line_label`. |
| B5 | **`odds_drift_home`/`steam_move` built from an unbounded query** — no `is_live=false`, no pre-kickoff bound. | ⚪ Latent | ⬜ Open | "Latest snapshot" can be an **in-play** price on a finished match. Currently inert (not in `FEATURE_COLS`; the meta-model uses bounded `*_at_t6h` variants) but it is a loaded gun in a shared builder. |
| B6 | **`simulated_bets.pnl` is priced at `odds_at_pick`** — the highest price ever *seen*, not one on offer. | 🟠 Raw column is **€447 optimistic** across all bots (−€39.79 stored vs −€487.15 executable). `bot_v10_all` +6.65% vs +3.10% ROI. | 📋 Documented | Not a bug — the UI was already fixed to the executable price in 2026-09-05. It is a **trap**: I summed the raw column mid-investigation and quoted an inflated drawdown. Now in `ANALYSIS_GOTCHAS`. When the page and your query disagree, the *query* is wrong. |

## C. Settlement & grading

| # | Defect | Impact | Status | Comment |
|---|---|---|---|---|
| C1 | **`_r_ou_goals` graded an unreadable O/U line as LOST — on both sides.** | ⚪ Latent | ✅ Fixed | `over_under_275` parses as 275.0, fails the 0<v<10 check, returns None → `return False`. Exactly the "unrecognised market becomes a silent loss" failure the resolver registry's own header says it was built to remove, still alive inside one resolver. Not reachable today (pipeline offers only 05/15/25/35/45) but the error is asymmetric: refusing alerts, guessing settles wrong silently. |
| C2 | **Integer O/U lines graded both sides as lost instead of a push.** | ⚪ Latent | ✅ Fixed | `over_under_30` with exactly 3 goals satisfies neither `>` nor `<`. The book returns the stake; we recorded two losses. |

## D. Measurement & process — the category that actually cost the most

| # | Defect | Impact | Status | Comment |
|---|---|---|---|---|
| D1 | **No test ever scored production's own output against reality.** | 🔴 This is *why* A1 survived four months. Every offline evaluator re-implements the read from `classes_`, and all of them were right. | ✅ Fixed | `MODEL-OUTPUT-CALIBRATION` now does it. Two lessons in its docstring: the first version checked only `source='ensemble'` and **passed on the very bug it was written for** (the blend is 86% Poisson, reading +0.090 while the raw head underneath was −0.168 — a blend can mask an inverted leg indefinitely); and it is scoped to post-fix rows because a red-on-arrival test gets ignored. |
| D2 | **`ll_model` / `ll_market` computed every run and discarded.** | 🔴 The cleanest model-vs-market skill comparison in the system, printed to stdout since May. | ✅ Fixed (mig 338) | Alongside it, the shrinkage alpha — a continuously-refitted, 60k-sample measurement of "does our model add anything" — read **0.0085 (1x2 t1)** and **exactly 0.0000** on two tiers, and nobody read it either. |
| D3 | **`clv_pinnacle` is partly circular** with the sharp selection rule. | 🟠 R²=0.386. Split by whether Pinnacle moved: static lines **+14.95%**, genuinely moved >5% **+2.42%, t=+1.1 — not significant**. | 📋 Recorded | My own +13.18%→+9.21% haircut understated it. Fix is to make the movement split a standing evaluation column so no sweep can report a Pinnacle-anchored CLV without it. |
| D4 | **`n ≥ 334` promotion gate is calibrated to the wrong effect size.** | 🟠 Observed CLV sd 0.147 ⇒ n=**10** suffices at a +9.2% effect. 334 assumes ~2%. | 📋 Recorded | Mine. Power was never the binding constraint — **bias** was. Accruing to 334 inside one regime tightens a biased estimate. |
| D5 | **Two tests pinned a reality that had changed**, one pre-existing and one self-inflicted. | 🟡 CI was red before this session started, masking every other regression those tests covered. | ✅ Fixed | `REAL-BETS-CLV-EDGE-SCHEMA` asserted a literal SQL string that a correct fix had rewritten; `OU-PLATT-UNFITTABLE` asserted rows my own migration deleted. My ripple-check grepped `docs/` and `*.md` but not `scripts/` — **the test suite is part of the doc surface**. |
| D6 | **`/performance` event markers had not rendered in months.** | 🟡 Hardcoded `x="May 6"` inside a `period==="90d"` guard; Recharts matches a categorical x against values *present in the data*. | ✅ Fixed | A marker that renders nothing is indistinguishable from one nobody added. The guard checked the selected period, not whether the day was on the chart. |

## E. Suspicions — not confirmed, do not act on yet

| # | Suspicion | Why it matters | Next step |
|---|---|---|---|
| E1 | **~20% of model importance sits on `*_missing` indicators.** | They plausibly encode data-collection era or league rather than football, and the coverage regime differs at serve time. | Ablation test. |
| E2 | **The Pinnacle veto (`gap > 0.12`) may be load-bearing in a way nothing documents.** | It is what currently stops 1x2 admitting the picks the sigmoid inflates — 11 of 87 candidates on a sample day. | Measure what 1x2 selection looks like with it off, before anyone "simplifies" it. |
| E3 | **The O/U head may be substantially re-learning the market.** | Its top features are `pinnacle_implied_under25`/`over25`; market values are 23.7% of O/U importance. | Not a bug if true — a design limit, and a reason alpha decays rather than collapsing. |
