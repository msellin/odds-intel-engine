# Master task list — 2026-09-14

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



> ## ⚠️ AUDITED END-OF-DAY 2026-09-14 — read `docs/TASK_LIST_AUDIT_2026_09_14.md` first
>
> This list was written **this morning**, before a day of findings that overturned
> several of its premises. Every one of the 17 then-open items (#4–#20) has since
> been re-verified against the code, the DB and git history — never against a doc
> asserting a state. The audit carries the evidence, the WHY-not-done category for
> each, and the ranked shortlist. Status cells below are updated in place; the
> **What the audit found** column is the short version.
>
> **Headline:** 2 already done (#7, #17) · 1 obsolete as filed (#20) · 5 rescoped
> (#4, #6, #9, #13, #15) · 9 still valid. The larger correction is to **scale**,
> not status — three items quote numbers today's work moved by 8–16×.
>
> **Three live defects found while auditing outrank most of this list** and are
> on none of it: an un-gated `resettle_wrongly_voided_bets` write path that can
> re-fabricate arbitrary-book CLV, a smoke guard defeated by removing the symptom
> it checks for, and `PRODUCT_FIX_PLAN` P1-7 (train/serve feature availability)
> which was lost between the two lists. See the audit's §Findings.
>
> **Do not restart #8, #15, #16 or #19** — each is paused for a reason that was
> verified to still hold today. The reasons are in the audit so nobody
> re-litigates them.

Every task from all three workstreams, priority-ordered, each with a
**pre-registered success measure**. The measure column is the point: this
project's dominant failure mode was not bugs, it was numbers that were computed
and never surfaced. A task without a falsifiable "did it help" is how that
happens.

**Two independent tracks.** Track M (model) and Track B (betting evidence) do not
block each other and should run in parallel.

| # | Task | Track | Dir | Est | Status | What it does | How we will know it helped |
|---|---|---|---|---|---|---|---|
| **1** | **Fix ELO/form leakage** | M | 🤖👥 | — | ✅ **DONE 2026-09-14** (`61cd38b`) | Read path changed to `date < match_date` in `_build_mfv_rows_for_matches` (the training-data path) and `write_morning_signals`. `batch_write_morning_signals` deliberately left at `<=` and documented — it is keyed on TODAY for UPCOMING fixtures, where "latest available now" is what inference legitimately has. | ✅ **MEASURED on 22,290 matches (2026-07-01..09-10): `elo_diff` AUC 0.7396 → 0.6171**, i.e. from *above* the de-vigged market (0.7270, logically impossible) to plausibly below it. 80.2% of stored rows differ from strictly-pre-match — independently confirming the audit's 79.5%. Smoke `ELO-FORM-LEAK` mutation-verified (reverting to `<=` fails it). CI 962 passed. ⚠️ **Stored MFV rows are still leaked** — this fixed the read; #2 rebuilds history and #3 retrains. Model quality is NOT yet improved and cannot be until #3. |
| **1b** | **`LEAKAGE-CANARY` — generic guard for the whole class** | M | 🤖👥 | — | ✅ **DONE 2026-09-14** | `scripts/leakage_canary.py` ranks every numeric MFV feature by \|corr\| with a settled outcome and flags any **non-market** feature reaching the strongest market-derived one. Encodes a hard rule: no strictly pre-match feature can out-discriminate the de-vigged market. Excludes market-derived columns (they *are* the market) and label columns (`total_goals` reads 0.761 vs over-2.5 because it IS the total — verified not a feature against the live bundle). | ✅ **Ran across all three outcomes: `over` CLEAN, `btts` CLEAN, `home` flags only `elo_diff` (0.4116 vs market ceiling 0.3658)** — so there is no second expensive leak. Smoke `LEAKAGE-CANARY` pins that known set; a NEW leak fails CI. The exception list should shrink to empty after #2. |
| **2** | **Rebuild historical MFV on clean features** | M | 🤖👥 | — | ✅ **DONE 2026-09-14** (mig 339/340) | Backed up 49,395 rows, rebuilt 49,509 across 134 dates via `backfill_mfv_historical.py --from 2026-05-01`, then surgically restored the columns the rebuild had no business touching. | ✅ **stored `elo_diff` AUC 0.7536 → 0.6134** (market ceiling 0.7270) — the stored feature is now honest, not just the read. 81.8% of rows changed. **`LEAKAGE-CANARY` is now completely clean — the `elo_diff` exception is gone.** ⚠️ The rebuild was initially DESTRUCTIVE: it NULLed `goals_for_avg_*` on 22,182 of 37,152 rows (−59.7%) because those are read through from a since-pruned `match_signals`, while reporting success with an unchanged row count. Caught by a column-level coverage diff against the backup, restored by mig 340, and prevented structurally by `coalesce_columns` on the upsert (#2b). ❌ The NULL-convergence measure did **not** move (`season_progress` still 4.7% train / 94.0% live) — that is **`PRODUCT_FIX_PLAN` P1-7's** job, not this one; I was wrong to attach it here. ⚠️ **AUDIT 2026-09-14:** this cross-reference read "task #7", which in THIS list is the bot retirement re-check — and P1-7 has no row here at all. Re-confirmed live: `season_progress` 5.0% NULL on past matches vs **88.9%** on upcoming; `league_clv_efficiency` 63.4% vs **100%**. See audit §F3. |
| **2b** | **`MFV-UPSERT-NON-DESTRUCTIVE` — a rebuild may never NULL read-through data** | M | 🤖👥 | — | ✅ **DONE 2026-09-14** | `bulk_upsert` gains `coalesce_columns`; both MFV builders pass the 25 columns read through from `match_signals`. Computed columns (`elo_*`, `form_*`) deliberately excluded — their NULL is a finding, and COALESCEing them would have undone #1 in the same commit that made it. | ✅ Pinned by `MFV-SIGNAL-COLUMNS-NOT-DRIFTED`, which asserts the list is wired into **both** the bulk and the one-by-one fallback path (the fallback runs exactly when the bulk path failed, i.e. under stress) and still matches what the reader assigns. |
| **3** | **Retrain — the first clean model this project has had** | M | 🤖👥 | — | ✅ **DONE 2026-09-14** | Rebuilt the FULL corpus clean (pre-May too — training on all history would have been 45% leaked), then trained `v20260914_clean` and `v20260914_clean_cut0820` (same 2026-08-20 cutoff as the live bundle, for a controlled comparison). | ✅ **GATE MET — after a same-day correction.** I first reported it failed; I had scored the RAW XGBoost output, which production never serves (it applies shrinkage + Platt first). The raw model IS worse than a constant, but its ranking is monotone across all 6 calibration bins and it is biased low by 10–17pp everywhere — a LEVEL error, not missing signal. Time-ordered recalibration (Platt on the first half of the holdout, scored on the second): **log-loss 0.7274 → 0.6705 vs base rate 0.6873, i.e. +2.44%** — the first model in this project's history to beat a constant. ⚠️ Still far below the market (AUC 0.6010 vs 0.7270), and the LIVE calibrator is wrong for it (`a=1.609` vs the `a=3.587` it needs), so it cannot be dropped in without refitting. Original raw-output finding: On n=14,084 held out after both cutoffs, both models scored on identical clean features: clean **AUC 0.6055 / LL 0.7173** vs leak-trained **0.6152 / 0.7183** vs **base rate 0.6856**. The clean model is indistinguishable from the leaked one (+0.13%) and **both are ~4.7% worse than a constant**. ➡️ **The leak inflated our EVALUATION, not our predictions.** Serving was always mediocre; we could not see it because every evaluator scored against the same contaminated table. The inversion fix was the real gain (AUC 0.4151 served → ~0.61 read correctly). Reproduce: `scripts/compare_bundles_holdout.py`. |
| **4** | **THE DECISIVE EXPERIMENT — does the clean model add anything the market lacks?** | M | 🤖👥 | 1d | 🔁 **RESCOPED** — 1X2 leg DONE today (α=0.0000, CI [0,0.0325], `c8b729a`). Residue = O/U + BTTS, **never started** | Predict the **residual** `y − p_mkt` on a time-ordered holdout, the method the OU signal-search pre-registration used. Answers "do we have a model business" with a number instead of an argument. | Three outcomes, all pre-declared: (a) no residual signal → **model-anchored betting is dead**, go all-in on sharp; (b) residual signal < vig (2.5–6.6%) → real but not monetisable at our books → buy more books, not more modelling; (c) residual signal > vig → **you have a model business**, re-derive every floor. |
| **5** | **Backfill `recommended_bookmaker` on 8 per-book trigger bots** | B | 🤖 | 2h | ⬜ **STILL VALID** — never started, 496/2,587 NULL (19.2%). ⚠️ **"Blocks #6" is FALSE** (it is 4.1% of #6). Real payoff: doubles n on the two sharp 1x2 bots | 488 of 2,445 trigger rows have no venue and cannot be priced executably. Recoverable with certainty from the bot name via `BOOK_MARKET_BOTS`. **Blocks #6.** ⚠️ Never by price-matching — only 36% of 400 sampled rows resolve to one book. | NULL rate on trigger bots **20% → 0%**; sharp 1x2 bots **33% → 0%**; the H1 evaluable population rises from **n=89 toward 140**. |
| **6** | **Port `DIRECT-BOOK-CLV` to `shadow_bets`** | B | 🤖👥 | 1d | 🔁 **RESCOPED** — code landed (`de7858c`), **backfill never ran** (106,726 bookless, unmoved). Payoff is **~11.8k unique rows, not 112k**. Feasible: 10,276 recoverable → 8.1% | `get_closing_odds()` is called with no bookmaker, so CLV is measured against whichever of ~13 books sorted last. Port `_direct_book_close` from the `real_bets` path (migration 332). Gives a structurally non-circular metric on ~112k rows instead of 89 — real time folds with no waiting. | **Baseline: 66.9% of CLV rows (106,726 of 159,614) have no known closing book.** Success = <10%, AND the circularity regression falls from **R²=0.386** toward ~0. ⚠️ Historical bot CLV will MOVE — that is the point, not a bug. |
| **7** | **Re-check the three 2026-09-14 1x2 retirements** | B | 🤖 | 2h | ✅ **DONE 2026-09-14** — overtaken by `OWN_BOOK_UNIVERSE` per-book-margin table (−6.10/−5.41/−6.97%). Carve-out: the reason on file is stale | `bot_coolbet_trigger_1x2_v1`, `bot_unibet_trigger_1x2_v1`, `bot_trigger_1x2_model_v1` were retired on arbitrary-book CLV. Depends on #6. | Binary: do they still fail on direct-book CLV? If any flips sign, the retirement is reversed and the method that produced it is re-examined. |
| **8** | **Re-measure shrinkage alpha after #3** | M | 🤖👥 | 4h | ⛔ **BLOCKED ×2, do not start** — no era marker in `fit_blend_weights`, and `v20260914_clean` has `promoted_at IS NULL` (not serving) | Alpha is the honest verdict on whether the model adds anything. Today's 0.0085 / 0.0000 was measured on inverted + leaked inputs, so it is a verdict on nothing. ⚠️ `fit_blend_weights` reads ALL history with no date bound, so inverted rows depress it for months — needs an era marker (`+selcal1` precedent). | **Baseline: `shrinkage_alpha_t1_1x2` = 0.0085, t2 and t4 exactly 0.0000.** Any sustained rise above 0.10 on clean data = the model is contributing. Flat at ~0 on clean data = the strongest possible evidence to stop model work. |
| **9** | **Arm `MODEL-OUTPUT-CALIBRATION`** | M | 🤖👥 | — | 🔁 **RESCOPED** — data gate at n=44/500, arms in ~2 days, BUT `created_at >= 2026-09-14` (midnight) admits 1,179 pre-fix rows; fix the boundary first | Currently SKIPS until 500 settled post-fix predictions exist. It is the test that would have caught the inversion in days. | It stops skipping and passes. If it ever fails, a market is being served backwards — that is its only job. |
| **10** | **Phantom `pinnacle_implied_*` features** | M | 🤖👥 | 4h | ⬜ **STILL VALID** — narrowed: 3 phantom features not 5; ~6% gain confirmed; today's retrain inherited them; needs a 2-file change (`smoke_test.py:12083` pins them) | In `feature_cols.pkl` but not columns of MFV → `0.0` with the missing-flag pinned on 100% of production rows forever. ⚠️ Do NOT fix by adding the column: training takes the latest pre-KO Pinnacle price, effectively the close, which inference never has. Remove them instead. | Pinned missing-flag rate **100% → 0%**; ~6% of gain importance redistributes to features that exist. |
| **11** | **Imputation mismatch train vs serve** | M | 🤖👥 | 4h | ⬜ **STILL VALID** — both halves true; the "indicators carry the signal" defence is *weaker* now (`_missing` gain rose to 20.8–28.9%) | Training fills the per-league mean, serving fills `0.0`. Also: means computed over the whole frame before `TimeSeriesSplit`, so CV folds see future means. | Same MFV row scored through both paths returns the **same probability** (currently unmeasured, and that is the problem). |
| **12** | **Swap `n ≥ 334` for power + regime conditions** | B | 🤖👥 | 4h | ⬜ **STILL VALID — promote.** Today corroborated it twice (mig 348's asymmetric-n doctrine; the +16% era selection) and encoded it nowhere | sd = 0.147 ⇒ n=10 suffices at a +9.2% effect; 334 assumes ~2%. Power was never the constraint — **bias** was. | Promotion decisions cite an effect size and a count of distinct weeks/regimes, never a bare n. |
| **13** | **Pinnacle-movement cut as a standing evaluation column** | B | 👥 | 4h | 🔁 **RESCOPED** — bots moved off `clv_pinnacle` today; what remains is `trigger_calibrator_check.py`, the watcher that auto-pages a verdict. Do with #12 | Where Pinnacle is static, "beats Pinnacle now" and "beat Pinnacle at close" are the same sentence. | **Baseline: static lines +14.95%, genuinely moved >5% +2.42% (t=+1.1, not significant).** Success = no sweep can report a Pinnacle-anchored CLV without printing this split. |
| **14** | **Draw `cal_prob` is a constant** | M | 🤖👥 | 4h | ⬜ **STILL VALID — baseline WITHDRAWN.** Cause found: `1x2_draw platt_a=0.39067`. The `[0.3050,0.3072]`/n=96 figure does not reproduce anywhere | Live draw picks span **[0.3050, 0.3072], sd 0.0004** on n=96. Either the draw head is dead or the calibrator flattened it to nothing. | sd rises materially above 0.0004, or the draw market is explicitly retired. Either is an answer; the current state is neither. |
| **15** | **`OU-CALIBRATOR-REFIT-ON-SHRUNK`** | M | 🤖👥 | 1d | 🔁 **RESCOPED** — harness already built (`ou_calibrator_backtest.py`); the question is "does ANY curve beat none" (no curve +24.7% t=+5.5 vs curve −1.57%). Deferral holds | Fit on `shrunk` (what inference receives), validate on the `edge ≥ floor` SELECTED subpopulation, not universe-wide ECE. ⚠️ The 2-feature variant collapsed the gate to 1 pick. | Overconfidence gap **+5.1pp → +2.5pp** at the same pick volume (measured in `ou_calibrator_backtest.py`). |
| **16** | **O/U blend weight is the 1x2 blend weight** | M | 🤖👥 | 4h | ⬜ **STILL VALID** — low leverage, fold into #15. No `blend_weight_ou*` key has ever existed | `load_blend_weight()` only reads `blend_weight_1x2*`. Fold into #15. | A `blend_weight_ou*` row exists and differs materially from 0.83. |
| **17** | **`/picks` publishes a frozen `odds_at_pick`** | B | 👥 | 4h | ✅ **DONE 2026-09-14** — surface replaced (`b894cb2`/`80aedd1`); staleness now disclosed per-row (`alignment_gap_minutes`, break-even `min` price) | Up to 6h behind the market / 48h old, while `odds_at_pick_live` is computed every 30min and never shown. Product decision, not a defect. | Published price age p95 falls below 1h, or the staleness is disclosed on the page. |
| **18** | **`odds_drift`/`steam_move` unbounded query** | M | 🤖👥 | 2h | ⬜ **STILL VALID — and worse than filed.** The `AF-ISLIVE` guard skips it *because* it has no `is_live` clause. Inertness re-verified | No `is_live=false`, no pre-kickoff bound — "latest snapshot" can be an in-play price. Inert today; a loaded gun in a shared builder. | Source guard + a test; the columns cannot read a post-kickoff price. |
| **19** | **`train_ah_xgboost.py` random split** | M | 🤖 | 1h | ⬜ **STILL VALID — keep last.** Dormant by design (`5141ce4`'s 4-step activation never ran); no `ah_xgb` reference anywhere in `workers/` | `StratifiedKFold(shuffle=True)` leaks through team-strength features. AH only. | `TimeSeriesSplit` in source; AH holdout metric will DROP — that is the honest number. |
| **20** | **Ablate the `*_missing` indicators** | M | 🤖👥 | 4h | 🔁 **OBSOLETE as filed** — answered 2026-09-04 (`b0510d5`, gotcha §36, dependence 0.23×). **Rescoped:** re-run the ablation on `v20260914_clean` (gain 13–18% → 20.8–28.9%) | ~20% of importance sits on missingness flags, which may encode collection era or league rather than football. Suspicion, not a defect. | Holdout log-loss with vs without. If removing them does not hurt, they were encoding regime and should go. |

## Done 2026-09-13/14

| Task | Verified effect |
|---|---|
| `OU-CALIBRATOR-DOMAIN-MISMATCH` (mig 335) | Only 2 of 142 published picks cleared their floor without the sigmoid lift. O/U pick volume correctly fell to ~0 (best available edge today +5.3% vs an 8% floor). |
| `1X2-CLASS-ORDER-INVERTED` | AUC(home) 0.4151 → 0.5892 on 4,658 matches. |
| `MODEL-OUTPUT-CALIBRATION` test | Mutation-verified: fails on historical data, passes clean. |
| 4 latent O/U bugs | Settlement lost-on-both-sides; `store_odds` handicap_line; 1xBet poisoning; isotonic key. |
| `ALPHA-IS-AN-UNREAD-INSTRUMENT` (mig 338) | `ll_model`/`ll_market` now recorded instead of printed and discarded. |
| `PERF-CHART-EVENT-MARKERS` | Bug window visible; found the old markers had silently not rendered in months. |
| 2 stale CI tests | CI was red before this session and is now green at 960. |

## Sequencing

> **⚠️ SUPERSEDED 2026-09-14 by `docs/TASK_LIST_AUDIT_2026_09_14.md`.** The plan
> below is kept for the record. Two of its edges were wrong: 1→2→3→4 completed
> today for 1X2 only, and `5 → 6` is not a dependency (#5 supplies 4.1% of #6's
> population). The ranked replacement is in the audit.

```
Track M:  1 → 2 → 3 → 4        (the leak gates the retrain gates the answer)
                    ↘ 8
Track B:  5 → 6 → 7            (independent; start immediately)

Everything 10-20 is parallelisable once its track's head is clear.
```

### What to work next (audited, 2026-09-14)

1. **Gate `resettle_wrongly_voided_bets`** — NOT ON THIS LIST. A running job can
   overwrite `clv` on 133 rows that already carry a valid closing book, with no
   guard able to see it, on the system's only promotion gate. ~1h.
2. **#6** — the backfill, measured feasible (10,276 of 11,822 recoverable → 8.1%).
   Add a `closing_minutes_before_ko` equivalent in the same pass. 1d.
3. **#5** — for the sharp-1x2 `n` argument, not the "blocks #6" one. 2h.
4. **#9** — fix the arming boundary; ~10 min of work with a ~2-day expiry.
5. **#18 + the guard that missed it** — the guard is the bigger defect.
6. **#12 + #13** together in `trigger_calibrator_check.py` — one file, both items.
7. **#20-rescoped** — permutation ablation on `v20260914_clean` before promoting it.
8. **P1-7** — train/serve feature availability; on no list (audit §F3).
9. **#4 residue** — the residual test on O/U and BTTS.
10. **#11, then #10.**

**Do not:** stake or publish the sharp anchor, re-derive the 2.2 odds floor from
the window that produced it, run the exploratory grid on five days of data, or
read a post-retrain drop in offline metrics as a regression.

**Also do not restart #8, #15, #16 or #19** — each is paused for a reason
verified to still hold on 2026-09-14. The reasons are quoted verbatim in the
audit so they do not get re-litigated.
