# P3.4 In-Play Phase 2 ML — implementation spec

**Filed:** 2026-06-06
**Status:** Spec only. Implementation deferred — this doc unblocks the multi-day build by resolving the open architectural questions.
**Parent task:** `PRIORITY_QUEUE.md` § INPLAY Plan + Tier 4 row `P3.4`
**Cross-references:** `PRIORITY_QUEUE.md` § INPLAY Plan §1-3 (core hypothesis, model architecture, feature tiers — written 2026-04-30, still authoritative). Don't re-derive what's there.

---

## Why this spec exists

The § INPLAY Plan section in `PRIORITY_QUEUE.md` was written 2026-04-30 before we had any data. Now (2026-06-06) the gates have crossed:

| Gate | Required | Actual | Status |
|---|---|---|---|
| xG snapshots | 500+ | **1,050 matches** | ✅ 2× |
| In-play settled bets | 200+ | **651 bets** | ✅ 3× |

The gates passed silently weeks ago and weren't noticed because the threshold check script was bugged (fixed 2026-06-06 — `THRESHOLD-CHECK-AUDIT-FIX`). **Phase 2 ML build has been ready to start for ~2 weeks.**

This spec resolves the gaps in the original plan: what training data we actually have, what's missing, what to label on, holdout strategy, success criteria, and per-strategy roll-out order.

---

## Part 1 — Training data inventory (actual state as of 2026-06-06)

### `live_match_snapshots` — 13,633 matches / 630,868 rows

| Property | Value |
|---|---|
| Distinct matches | 13,633 |
| Total snapshot rows | 630,868 |
| Median snapshots/match | 36 |
| P90 snapshots/match | 93 |
| Max (pathological) | 904 |

**Minute coverage** (% of snapshots):

| Window | % |
|---|---|
| <30 min | 17.9% |
| 30-60 min | 48.9% |
| 60-75 min | 11.3% |
| ≥75 min | 21.9% |

Mid-game (30-60) is over-sampled vs late game — bias to account for in train/val splits.

### Feature coverage (% of snapshots where the column is non-NULL)

| Feature | Coverage | Notes |
|---|---|---|
| `xg_home`, `xg_away` | ~96% | Workhorse — Tier 1 feature in §3 |
| `shots_home`, `shots_on_target_home` | ~90% | Good coverage |
| `possession_home` | ~85% | Reasonable |
| `score_home`, `score_away` | ~100% | Always present |
| `corners_home` | **9.6%** | Sparse — Tier 2 feature in §3 (`corner_momentum`) may be data-limited |
| `live_1x2_home` (live odds) | **14.8%** | **Critical gap** — limits backtest universe |
| `live_ou_25_over` | **14.8%** | Same |
| `model_xg_home` | **0%** | Empty column — Phase 2 OUTPUT, not input |
| `model_ou25_prob` | **0.02%** | Same |

### Key gaps to flag in implementation

1. **Live odds only cover ~15% of snapshots.** Phase 2 backtest universe is limited to the ~93,000 snapshots with `live_1x2_home` and/or `live_ou_25_over` present. Verify this in pre-training EDA — the gate-already-met "1,050 matches" almost certainly cover most of the live-odds subset, but worth confirming.

2. **`model_xg_home` and `model_ou25_prob` are empty.** These are the outputs the new model produces. Phase 2 will populate them; current empty state is normal.

3. **CLV is NULL for every in-play bet.** Verified via `SELECT COUNT(clv) FROM simulated_bets sb JOIN bots b ON b.id=sb.bot_id WHERE b.name LIKE 'inplay_%' AND sb.result IN ('won','lost')` → 0 of 651. Reason: in-play markets don't have a "closing line" the way pre-match does — the line moves continuously up to settlement. **Implication:** Phase 2 cannot use CLV as a label. Must train on **bet outcome (won/lost)**, validating via ROI on holdout.

### Settled in-play bets per strategy

| Strategy | Settled | All-time ROI | Notes |
|---|---|---|---|
| `inplay_e` (Game-state value) | 216 | **+7.64%** | Largest sample; ECE 21.93% (miscalibrated, see INPLAY-E-ECE-RECHECK). Profitable despite bad calibration. |
| `inplay_p` | 193 | **−14.52%** | Persistent loser. Reviewed for retirement? |
| `inplay_c` | 53 | +1.13% | Marginal |
| `inplay_p_v2` | 46 | **+32.08%** | Promising — `inplay_p` v2 |
| `inplay_l` (Goal Contagion) | 32 | **+26.74%** | Close to INPLAY-HT-REPRICING calibration-review gate (50+) |
| `inplay_n` | 24 | **−50.63%** | INPLAY-N-MODEL-VS-MARKET-GATE filed 2026-06-06 |
| `inplay_o` (Underdog Hold) | 20 | +214% (pre-quarantine), now retired-effective | INPLAY-O-QUARANTINE 2026-06-06 voided 62/70 bets as stale-odds. Treat as untrustworthy. |
| `inplay_i` (Favourite Stall) | 17 | **−36.47%** | Filed INPLAY-I-INVESTIGATE. Don't train on it yet. |
| `inplay_b` | 13 | +47.62% | Small sample |
| `inplay_m` | 12 | +18.53% | Small sample |
| Others (a, d, f, g, h, j) | 2-11 | Mixed | Too small for individual training |
| **Total** | **651** | — | — |

---

## Part 2 — Architectural decisions (resolutions to § INPLAY Plan §2)

### Decision 1: **Target**

§ INPLAY Plan says: predict `lambda_home_remaining` + `lambda_away_remaining` via LightGBM Poisson regression. **Keep this.** All market probabilities derive from the per-team lambda pair.

**Open caveat:** the original plan assumed we had pre-computed prematch lambdas via Poisson fits. Verify the prematch lambdas are accessible in MFV (`match_feature_vectors`) before training begins — if not, derive from MFV `predictions` table rows where `source='poisson'`.

### Decision 2: **Label**

The original plan didn't specify the training label cleanly. **Resolved:** the label is **actual remaining goals from the snapshot's minute to full-time**, joined to `matches.final_score_home` / `matches.final_score_away`. Compute as:

```sql
label_remaining_home = (matches.score_home - snapshot.score_home)
label_remaining_away = (matches.score_away - snapshot.score_away)
```

Filter out matches with red cards (per § INPLAY Plan §2 — hard-skip in V1).

### Decision 3: **Holdout strategy**

**Temporal split:**

| Split | Window | Use |
|---|---|---|
| Train | 2026-04-01 → 2026-05-15 | Model fitting |
| Validation | 2026-05-16 → 2026-05-31 | Hyperparameter tuning + early stopping |
| Test | 2026-06-01 → 2026-06-07 (advancing weekly) | Honest OOS evaluation; rolling forward each week as new data arrives |

**Critical:** never tune on the test window. **Especially critical** because we have shadow-trail tasks scheduled for 2026-06-10 (PIN-CROSS-DRIFT-ACTIVATE, INPLAY-E-PLATT-ACTIVATE) — those expect post-2026-06-03 cleanliness; do not muddy that cohort with model retraining experiments.

### Decision 4: **Per-snapshot vs per-match unit**

The natural training unit is **one row per (match × minute-window)** — e.g., 36 rows per match for a typical match. § INPLAY Plan implies this.

**But this creates leakage:** rows from the same match are not independent. Use **group-aware cross-validation** during training (sklearn `GroupKFold` with `match_id` as groups) and never split within a match.

### Decision 5: **Features — start narrow, expand**

§ INPLAY Plan §3 lists ~20 features across 3 tiers. **For v0**, build only Tier 1 features (7 of them) plus 3 baseline features (minute, score_home, score_away). Add Tier 2 only if v0 doesn't beat baseline.

**Tier 1 features for v0 (10 total):**

1. `bayesian_xg_rate = (prematch_xg + live_xg) / (1.0 + minute/90)`
2. `xg_delta_vs_expectation = live_xg - (prematch_xg * minute/90)`
3. `xg_to_score_divergence = live_xg_total - actual_goals`
4. `implied_prob_gap = model_prob - (1 / live_odds)` (computed at inference)
5. `team_xg_per_shot = team_xg / max(team_shots, 1)`
6. `odds_velocity = (odds_now - odds_5min_ago) / odds_5min_ago`
7. `odds_staleness_flag = age_seconds > 60`
8. `minute` (continuous)
9. `score_home` (continuous)
10. `score_away` (continuous)

### Decision 6: **Algorithm**

§ INPLAY Plan says: LightGBM with `objective='poisson'`. **Keep.** Single algorithm for v0; XGBoost ensemble partner deferred to v1 if v0 underwhelms.

LightGBM hyperparameters (start with these, tune via grid on validation split):
- `n_estimators = 300`
- `max_depth = 6`
- `learning_rate = 0.05`
- `num_leaves = 31`
- `min_child_samples = 50` (deliberately conservative — small training set)
- `subsample = 0.8`
- `colsample_bytree = 0.8`
- `reg_alpha = 0.1`
- `reg_lambda = 1.0`

---

## Part 3 — Success criteria

The Phase 2 model **must** clear all four bars before being deployed in any production betting path:

### Bar 1: Calibration

**ECE (Expected Calibration Error) ≤ 5% on validation + test splits.** This is the same gate used for `inplay_e` (INPLAY-E-ECE-RECHECK 2026-06-03 found 21.93% — failed). If Phase 2 model can't clear 5%, it's not deployable.

### Bar 2: Beats `inplay_e` baseline

`inplay_e` is the current best in-play strategy by sample size (216 bets, +7.64% ROI). The Phase 2 model **must produce a strategy** (bot wrapping the model's output) that exceeds inplay_e's risk-adjusted return on the test window.

Specifically:
- ROI ≥ +5% on ≥100 test-window bets, OR
- ROI ≥ +10% on ≥50 test-window bets with no single bet > 5% of staked capital

If Phase 2 model improves probability estimates but the strategy wrapping it doesn't outperform inplay_e in live ROI terms, the model isn't ready.

### Bar 3: Robustness — no inplay_o-style data-leakage failures

The new strategy MUST be backtested with the existing `_score_odds_consistent()` veto (commit `6cf9a6a` 2026-05-30) explicitly applied. The inplay_o quarantine showed 88.6% of pre-fix bets were on stale-odds snapshots; any new strategy must demonstrate it doesn't fire on the same data pathology.

### Bar 4: No silent gating regression

The activation step must include re-running `scripts/threshold_check.py` and `scripts/smoke_test.py` to confirm:
- New strategy bot exists in `bots` table with `is_active=true`, `maturity_label='beta'` (NOT `calibrated` until 50+ live bets validated)
- No existing in-play bot has `maturity_label` regressed
- Pipeline runs (`pipeline_runs` table) show the new bot firing within 24h

---

## Part 4 — Phase 2 implementation order (5-7 days)

### Day 1 — Data extraction (2-3h)

`scripts/build_inplay_training_data.py`:
- Joins `live_match_snapshots` × `matches` × `predictions` (prematch poisson lambdas) × `match_feature_vectors` (prematch xG / ELO).
- Filters to matches with red cards = 0 (verify via match events table).
- Filters to snapshots with `xg_home IS NOT NULL` AND `score_home IS NOT NULL`.
- Writes `dev/active/inplay-training-v0.parquet` with one row per (match × minute-window), with features + 2 labels (`label_remaining_home`, `label_remaining_away`).
- Expected output: ~80k-100k training rows (650K total snapshots × ~15% w/ live odds × xG completeness filter).

### Day 2 — Model training (3-4h)

`scripts/train_inplay_phase2.py`:
- Loads training parquet.
- Temporal split per Decision 3.
- LightGBM Poisson regression × 2 (one per team).
- Group-aware CV per Decision 4.
- Outputs: model bundle (`models/inplay_v1_yyyymmdd.pkl`), ECE plot, validation/test ROI projection.
- Bar 1 (ECE ≤ 5%) checked here — abort if not cleared.

### Day 3 — Strategy wrapping (3-4h)

`workers/jobs/inplay_bot.py`:
- New strategy `inplay_phase2_v0` reads from the model bundle.
- Computes implied_prob_gap at every snapshot tick.
- Trigger: gap ≥ +5% AND odds_staleness_flag = False AND no red cards.
- Markets: start with OU 2.5 only (highest signal-to-noise per literature; opens up BTTS / 1X2 in v1).
- Stake: flat €5 (matches inplay_e for direct comparison).

### Day 4 — Backtest against historical snapshots (2-3h)

`scripts/backtest_inplay_phase2_v0.py`:
- Replays the new strategy against the test-window snapshot stream.
- Verifies Bar 2 (beats inplay_e baseline).
- Verifies Bar 3 (no stale-odds firings).

### Day 5 — Shadow deploy (live, no real money) (1-2h + 7d observation)

- Deploy `inplay_phase2_v0` bot to Railway as paper-trading (`is_real_money=false`).
- Maturity label `beta` (NOT calibrated).
- 7-day shadow observation. Check daily that the bot is firing at expected rate (~2-10 bets/day based on backtest).

### Day 6-7 — Honest verdict (2-3h)

- After 7d live shadow: compute live ROI, ECE, and pipeline health.
- If clears all 4 bars → promote to `maturity_label='active'`, ramp paper-trading capital, file `INPLAY-PHASE2-PROMOTE-CALIBRATED` task for the 50-bet → calibrated transition.
- If fails any bar → document the failure mode in `dev/active/inplay-phase2-v0-postmortem.md`, retire the bot, plan v1 (Tier 2 features OR XGBoost ensemble partner OR re-spec the target).

---

## Part 5 — Risk register

Known model issues from sibling in-play work that **must not repeat** in Phase 2:

| Risk | Source | Mitigation |
|---|---|---|
| Stale-odds data leakage | INPLAY-O-QUARANTINE (62/70 bets on inverted snapshots) | Apply `_score_odds_consistent()` veto pre-feature-extraction + reject any backtest result that suggests the bot is over-performing on high-odds underdog "leading" states |
| Catastrophic miscalibration despite +ROI | INPLAY-E-ECE-RECHECK (ECE 21.93%, ROI +7.64%) | ECE gate at training time (Bar 1). Don't let a +ROI bot through Bar 2 if Bar 1 fails. |
| Model over-confident in low-data leagues | INPLAY-N-MODEL-VS-MARKET-GATE (model 70%, implied 47%, 23pp gap) | Add a `prematch_implied_vs_model_gap` veto: skip any bet where `model_prob - prematch_implied > 0.15`. Same threshold the new INPLAY-N gate uses. |
| Sample-starvation masking failures | InplayBot UUID bug (11 days of "0 bets" looking normal) | Daily fire-rate monitor — alert if `inplay_phase2_v0` fires < 1 bet/day on a day with ≥5 qualifying snapshot-windows |

---

## Part 6 — Open decisions deferred to implementation start

These don't block the spec but must be resolved before Day 1 of implementation:

1. **Which prematch lambda source?** MFV `predictions.source='poisson'` (live pipeline) vs MFV `match_feature_vectors.predicted_*` (offline training feed)? Pick one and document.
2. **How to handle matches with red cards?** Hard-skip is the V1 default per § INPLAY Plan §2. Confirm we have a clean red-card flag column or derive from `match_events`.
3. **Train period start date.** 2026-04-01 was suggested above based on "live snapshot capture coverage starts there." Verify exact start date — if April data is sparse, push start to first month with ≥3K matches.
4. **Where does the model artifact live?** Suggest `models/inplay_v1_yyyymmdd.pkl` in Supabase Storage to match the existing XGBoost ensemble pattern. Confirm with operator before training run.
5. **OU 2.5 only or also BTTS/1X2?** v0 = OU 2.5 only per Decision 5. If v0 succeeds, expand to BTTS/1X2 in v1.

---

## Cross-references

- `PRIORITY_QUEUE.md` § INPLAY Plan §1-5 (full hypothesis + feature tiers + strategy roster)
- `PRIORITY_QUEUE.md` Tier 4 row `P3.4` (parent task)
- `PRIORITY_QUEUE.md` `INPLAY-E-ECE-RECHECK` (2026-06-03 — calibration risk reference)
- `PRIORITY_QUEUE.md` `INPLAY-O-QUARANTINE` (2026-06-06 — data-leakage failure mode)
- `PRIORITY_QUEUE.md` `INPLAY-N-MODEL-VS-MARKET-GATE` (2026-06-06 — model-vs-implied veto pattern)
- `scripts/threshold_check.py` (gate-monitoring script; run weekly)
