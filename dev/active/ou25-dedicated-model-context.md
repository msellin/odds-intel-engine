# OU25-DEDICATED-MODEL-INVESTIGATE — Context

**For: continuing this work in a fresh session.**

## State as of 2026-06-08 session start

Task locked 🔄 In Progress in PRIORITY_QUEUE.md line 60. Plan written to `ou25-dedicated-model-plan.md`. Tasks tracked in conversation TaskList (#1-#6).

## Key files

| File | Purpose |
|---|---|
| `workers/model/train.py` | Production training script. Reference for FEATURE_COLS (line 130) — the dedicated model's feature subset must use names from this list. |
| `workers/model/xgboost_ensemble.py` | Production inference. `_load_bundle()` (line 83), `get_xgboost_prediction()` (line 293). Bundle structure: `over_under.pkl`, `home_goals.pkl`, `away_goals.pkl`, `feature_cols.pkl`. Must mirror this for ou25_dedicated_v1 to be a drop-in. |
| `scripts/backfill_ah_predictions.py` | Reference Poisson + DC implementation. `_ah_model_prob` (line 98), `solve_lambdas` (line 151), `_dc_tau` (Dixon-Coles low-score correction). Reuse the math, not the AH-specific selection logic. |
| `workers/jobs/daily_pipeline_v2.py` | Live inference path. `_solve_lambdas_calibrated` + `_ah_model_prob` defined here. Search smoke_test.py for line numbers. |
| `scripts/ingest_football_data_csvs.py` | CSV ingest pipeline. Line 499/506: `add(name, "over_under_25", "over"/"under", o/u, is_closing, is_opening)` — confirms exact `market='over_under_25'` and `selection='over'/'under'` in odds_snapshots. |
| `supabase/migrations/179_mfv_pinnacle_open_to_close_drift.sql` | DRIFT-FEATURE columns: `pinnacle_drift_home/draw/away` on match_feature_vectors. Backfilled 2026-06-04 from CSV-FULL-EXTRACT. |
| `supabase/migrations/183_mfv_pinnacle_drift_fix.sql` | Drift backfill correction. |
| `dev/active/csv-full-extract-backtest-results.md` | Confirms the CSV-FULL-EXTRACT data unlock: 8,868 paired AH closing matches, 8,850 paired open+close 1X2 matches. OU-specific counts TBD by audit. |
| `dev/active/ah-bot-model-results.md` | Negative AH result that motivates this task — AH model failed because Pinnacle AH closing was too sharp. OU 2.5 has wider margin so may behave differently. |

## Key data points

- **Production OU head**: v14 was the baseline. v20260524_market is current prod but 1.4% worse on OU log_loss. v20260531 (cron) is 4.1% worse.
- **Env override path**: `MODEL_VERSION_OU=v14_recreate_2026_05_11` is the target baseline because OU-CLV-OPTION-B-FLIP shipped that as the override on 2026-06-07.
- **CSV data**: 118K Betfair Exchange rows, 80K Bet365 OU 2.5 closing, ~8.5K paired Pinnacle OU 2.5 close+open (per priority queue task body, exact OU-specific number TBD by audit).

## Schema essentials

- `matches.score_home + matches.score_away` — actual goal totals (smallint, NULL until finished).
- `odds_snapshots(match_id, bookmaker, market='over_under_25', selection IN ('over','under'), implied_prob, odds, is_closing, is_opening, timestamp)`.
- `match_feature_vectors` — see `workers/model/train.py:130-171` for the full feature_cols list. Required join key: `match_id`.

## Decisions made

1. **Architecture: Poisson goals model** (not direct binary classifier). Per priority queue task body. Reuses existing _ah_model_prob math, easier to interpret feature importance, generates secondary signals (xg_home, xg_away) that could feed other markets.
2. **Bundle output as drop-in over_under.pkl + home_goals.pkl + away_goals.pkl** — so `MODEL_VERSION_OU=ou25_dedicated_v1` works through existing inference path with zero code change.
3. **Holdout window: time-based.** Train pre-2025-07-01, test 2025-07-01..2026-04-30. Exact dates TBD after Phase 1 data audit.
4. **Ship gate**: ≥5% log_loss improvement OR ≥2pp ROI@+5% edge improvement vs `v14_recreate_2026_05_11`.

## Decisions pending

- [x] Training universe — 10,466 Pinnacle paired+finished+MFV matches (2023-2026). Bet365 breadth would only add ~2.6K marginal (13,085 total). Stay with Pinnacle-primary.
- [x] Feature subset — see Phase 1 findings below
- [ ] PoissonRegressor (sklearn) vs `objective="count:poisson"` XGBoost (Phase 2 output)
- [ ] Train/test cutoff date (Phase 2 output)

## Phase 1 audit findings (2026-06-08)

### Universe sizes

| Bookmaker | Paired+Finished+MFV |
|---|---|
| Pinnacle | **10,466** |
| Bet365 | 13,085 |
| Pinnacle ∩ Bet365 | 10,293 |

Per-year (Pinnacle): 2023=1,411 · 2024=3,979 · 2025=4,697 · 2026=379

### Feature coverage problem (the big finding)

**MFV is essentially empty for CSV-era matches (2023-2025).** CSV-FULL-EXTRACT backfilled odds, not MFV features. Only AF-era matches (2026+, post April migration) have MFV populated with the full feature set.

Coverage on CSV-era (2023-2025) matches:

**Usable features (<10% NULL)**:
- `elo_home`, `elo_away`, `elo_diff` (0% NULL)
- `league_tier` (0% NULL)
- `pinnacle_drift_home/draw/away` (0% NULL — DRIFT-FEATURE backfill 2026-06-04)
- `form_ppg_home/away` (0-17% NULL depending on year)

**Effectively unusable for CSV-era training (95-100% NULL)**:
- All goals_for_avg, goals_against_avg, xg_overperf
- All referee_*, league_draw_rate_ytd, season_progress
- All injury, weather, player_rating
- opening_implied_*, bookmaker_disagreement
- form_momentum, rest_days, h2h_win_pct, fixture_importance

This dramatically narrows the model. Training set has 9 MFV features + we can derive Pinnacle close 1X2 implied probabilities directly from `odds_snapshots` for ~3 more.

### Architecture pivot (recorded here, will update plan doc)

Original plan proposed Poisson + DC with rich features (xG, referee, injury, weather). **None of those features exist for CSV-era matches.**

Two viable paths forward:

**Path A (lean Poisson — faithful to task spec)**: train Poisson goal regressors with the sparse feature set (~12 features: ELO, league_tier, drift, form_ppg, Pinnacle close 1X2 implied). Label = `matches.score_home` and `matches.score_away` separately (count regression). At inference build joint matrix → over25.

**Path B (direct binary classifier — pragmatic)**: train an XGBoost binary classifier directly on `(score_home + score_away > 2.5)` with the same sparse feature set. Simpler, fewer assumptions, but no secondary signal (no xG estimate).

**Decision: Path A (Poisson).** Reasons:
1. The task body explicitly says Poisson + DC.
2. Goal-count regression extracts more signal per row than binary (the model learns the full goal distribution, not just one cross-section).
3. Comparable inference contract — `over_under.pkl` can wrap the joint-matrix computation behind a sklearn-classifier interface, same as Path B.
4. Generates xG estimates as a side-effect, which is potentially useful for future markets.

## Phase 2 architecture (locked 2026-06-08)

**Model class**: XGBoost regressors with `objective='count:poisson'`.
- `home_goals_xgb`: predicts `score_home`
- `away_goals_xgb`: predicts `score_away`
- Inference: build joint goal matrix (Dixon-Coles τ correction), sum `P(h+a > 2.5)`

**Final feature set (9 MFV-only columns — derived from-odds_snapshots features dropped because `_build_row_from_mfv` only reads MFV)**:
1. `elo_home`
2. `elo_away`
3. `elo_diff`
4. `league_tier`
5. `pinnacle_drift_home`
6. `pinnacle_drift_draw`
7. `pinnacle_drift_away`
8. `form_ppg_home`
9. `form_ppg_away`

**Train/test split**:
- Train: 2023-01-01 to 2025-09-30 (n ≈ 8,500)
- Holdout: 2025-10-01 to 2026-05-31 (n ≈ 1,500)
- Time-ordered, no shuffle.

**Base rate on universe**: 51.7% Over 2.5 — balanced binary.

**Bundle layout** (drop-in compatible with existing `_load_bundle`):
```
data/models/soccer/ou25_dedicated_v1/
  feature_cols.pkl      # the 9 cols above
  over_under.pkl         # Ou25PoissonWrapper (sklearn-classifier interface)
  home_goals.pkl         # xgb home goals regressor
  away_goals.pkl         # xgb away goals regressor
  result_1x2.pkl         # COPY of v14_recreate_2026_05_11/result_1x2.pkl
                         # (stub — required by _load_bundle, never read at OU inference)
```

**Why stub `result_1x2.pkl`**: `xgboost_ensemble._load_bundle` (line 114-125) requires all 5 pickles; if any are missing, returns empty dict → OU inference falls back to Poisson-only. Copying v14_recreate's stub satisfies the loader contract without touching core inference code. Future cleanup: make `_load_bundle` lenient with missing heads when feature_cols indicate single-head bundle. Filed as candidate follow-up.

**Inference wrapper** — `Ou25PoissonWrapper`:
```python
class Ou25PoissonWrapper:
    """sklearn-classifier-shaped wrapper around two Poisson regressors."""
    classes_ = [False, True]  # matches v14's over_under classifier shape

    def __init__(self, home_goals_model, away_goals_model, feature_cols, dc_rho=-0.18):
        self.home_goals_model = home_goals_model
        self.away_goals_model = away_goals_model
        self.feature_cols = feature_cols
        self.dc_rho = dc_rho

    def predict_proba(self, X) -> np.ndarray:
        # → [(under25, over25), ...]
        ...
```

Joint matrix uses `_dc_tau` from `scripts/backfill_ah_predictions.py` (or equivalent inline) — Dixon-Coles low-score correction for sample sizes 0-7 each side.

**Baseline bundles for comparison** (all rescored on same holdout matches):
- `v14`
- `v14_recreate_2026_05_11` ← the env-var override target (primary baseline)
- `v20260524_market`
- (skip `v20260607` — only 5K predictions, partial; not a fair comparison)

**Metrics on holdout**:
- `log_loss` (binary cross-entropy vs over25 outcome)
- `brier` (mean squared error vs binary outcome)
- `ECE` (10-bin calibration error)
- `ROI@+5%` edge — bet over (or under) whichever side has model_prob > pinnacle_implied + 0.05
- `ROI@+10%` edge

**Ship gate**: ≥5% log_loss improvement OR ≥2pp ROI@+5% lift vs `v14_recreate_2026_05_11`.

## Next steps (post-docs-written)

1. Phase 1 data audit. Query DB for paired OU 2.5 closing × MFV × finished-match counts. Write findings to `ou25-dedicated-model-context.md` Decisions Made section.
2. Phase 2 architecture lockdown.
3. Phase 3+ implementation.

## Gotchas

- **Inference compatibility**: the v14 over_under head returns `predict_proba` with `classes_ = [False, True]`. Our Poisson-derived bundle needs to wrap the joint-matrix computation in a class with the same interface. Inspect `workers/model/xgboost_ensemble.py:382-388` for exact contract.
- **Feature schema lock**: bundle must save `feature_cols.pkl` matching whatever subset we trained on. Inference loads with `pd.DataFrame([row])[ou_feature_cols].fillna(0)` — column order matters.
- **MFV row availability**: CSV-era matches (pre-AF migration ~2026-04-30) may not have full MFV feature population. Phase 1 audit must check this — if MFV NULL rate is high, fall back to a thinner feature set.
- **Dixon-Coles tau correction**: needs paired (h, a, exp_h, exp_a) — same as `_ah_model_prob`. Reuse, don't re-implement.
- **Pinnacle drift sign convention**: `(close_implied − open_implied)`. Positive = market moved toward that selection. Verified in migration 179 comment.
