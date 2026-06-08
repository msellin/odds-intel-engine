# OU25-DEDICATED-MODEL-INVESTIGATE — Plan

**Started:** 2026-06-08
**Target verdict:** 2026-06-15
**Effort estimate:** 6-10h
**Status:** 🔄 In Progress

## Why this task

OU 2.5 log_loss has regressed across every cron retrain since TIER-C-EXPAND (2026-05-19):

| Bundle | OU log_loss (same eval window) | vs v14 |
|---|---|---|
| v14 (baseline) | 0.8145 | — |
| v14_recreate_2026_05_11 | 0.8141 | -0.05% |
| v20260524_market (current prod) | 0.8258 | **+1.4%** |
| v20260531 (latest cron) | 0.8482 | **+4.1%** |

Conclusion from `OU-CLV-OPTION-C-DIAG` (2026-06-06): TIER-C-EXPAND added low-tier matches that systematically distort the OU head. The Sunday 06-07 retrain is expected to be even worse.

**Two paths exist:**

1. **OU-CLV-OPTION-B-FLIP-MODEL-VERSION-OU** (already executed 2026-06-07) — pin OU to `v14_recreate_2026_05_11` via `MODEL_VERSION_OU` env var. Recovers OU CLV to pre-regression baseline. **This is the hot fix.**

2. **This task (OU25-DEDICATED-MODEL-INVESTIGATE)** — train a standalone OU 2.5 model on CSV-FULL-EXTRACT historical data. If meaningfully better than `v14_recreate_2026_05_11`, deploy as `MODEL_VERSION_OU=ou25_dedicated_v1`. If not, document why and keep the env-var override.

## Hypothesis

A goals model trained exclusively on CSV-FULL-EXTRACT football-data.co.uk data (cleaner labels, 2024+ → present, ~8.5K paired-Pinnacle matches + ~80K Bet365 breadth) can beat the joint-trained `v14` OU head because:

- **Data quality**: CSV labels are league-curated and consistent. AF data has gaps and inconsistencies across leagues.
- **No TIER-C contamination**: the dedicated training universe is the same set of mature leagues across all years — no late-added low-tier mass.
- **Margin headroom**: OU 2.5 bookmaker margin is ~5-6% (vs AH at ~2-3%), so soft books leave more inefficiency. Even if Pinnacle closing is sharp, the structural margin means a slightly-better model has more room to convert into ROI.

## Architecture

**Approach: Poisson + Dixon-Coles goals model.** Reuse `_ah_model_prob` and `_solve_lambdas_calibrated` infrastructure from `workers/jobs/daily_pipeline_v2.py` and `scripts/backfill_ah_predictions.py`.

- **Label**: actual goal totals (`matches.score_home + matches.score_away`)
- **Targets**: two Poisson regressors — `expected_home_goals` and `expected_away_goals`
- **Inference**: build joint goal-matrix (Dixon-Coles low-score correction) → sum `P(h+a > 2.5)` for over25_prob
- **Features** (subset of `match_feature_vectors`, restricted to what CSV-era matches actually have):
  - ELO: `elo_home`, `elo_away`, `elo_diff`
  - Goals form: `goals_for_avg_home/away`, `goals_against_avg_home/away`, `goals_for_avg_home/away`
  - xG: `xg_overperf_home/away`
  - Standings: `league_position_home/away`
  - Referee: `referee_over25_pct`, `referee_cards_avg`
  - League: `league_tier`, `league_draw_rate_ytd`, `season_progress`
  - Market: `opening_implied_*`, `bookmaker_disagreement`, `pinnacle_drift_home/draw/away` (DRIFT-FEATURE)
  - Form: `form_ppg_home/away`, `form_momentum_home/away`
  - Rest: `rest_days_home/away`
  - **Excluded** (sparse in CSV era): weather, injury severity, player ratings

**Bundle format**: matches the existing `_load_bundle` contract — `over_under.pkl` (Poisson-wrapped classifier), `home_goals.pkl`, `away_goals.pkl`, `feature_cols.pkl`. So that `MODEL_VERSION_OU=ou25_dedicated_v1` works as a drop-in.

## Phases

### Phase 1 — Data audit (1h) — Task #1

Quantify:
- Pinnacle OU 2.5 closing rows with `handicap_line` (expect ~8.5K paired matches)
- Bet365 OU 2.5 closing rows (expect ~80K)
- Subset that has finished `matches.score_home + score_away`
- Subset that has populated `match_feature_vectors` rows for the chosen feature subset

Output: dataset size table + chosen train/test split.

### Phase 2 — Architecture spec (30m) — Task #2

Lock in:
- Train/test split: time-based holdout (e.g. train pre-2025-07-01, test 2025-07-01..2026-04-30)
- Feature set + missing-value handling (league mean fallback)
- Poisson regressor class (sklearn `PoissonRegressor` or XGBoost `count:poisson` — pick one)
- Joint-matrix inference reuse of `_ah_model_prob` math

### Phase 3 — Build dataset loader (1.5h) — Task #4

Script: `scripts/train_ou25_dedicated.py`

```
loader → pd.DataFrame[features + actual_home_goals + actual_away_goals + actual_total + pinnacle_over25_closing + pinnacle_under25_closing]
```

Joins:
- `odds_snapshots WHERE market='over_under_25' AND is_closing AND bookmaker='Pinnacle'` (closing prices for label benchmark)
- `matches` for actual goal totals
- `match_feature_vectors` for features

### Phase 4 — Train + holdout evaluation (2-3h) — Task #5

Train two `PoissonRegressor` models (home_goals, away_goals) on training set. Compute over25_prob on holdout via joint matrix.

Evaluate four bundles on the same holdout matches:
1. `ou25_dedicated_v1` (this work)
2. `v14`
3. `v20260524_market` (current production)
4. `v14_recreate_2026_05_11` (env-var override target)

Metrics:
- **log_loss** (vs binary `total > 2.5` outcome)
- **brier**
- **calibration ECE** (10 bins)
- **ROI @ +5% edge** — bet whichever side has model_prob > pinnacle_implied + 0.05
- **ROI @ +10% edge** — same with +0.10 threshold

### Phase 5 — Verdict + decision (1h) — Task #6

**Ship gate**: ou25_dedicated_v1 must improve log_loss by ≥5% **OR** lift ROI@+5% by ≥2pp vs `v14_recreate_2026_05_11` (the env-var override baseline, since that's what production actually uses).

**If ship**:
- Save bundle to `data/models/soccer/ou25_dedicated_v1/`
- Upload to Supabase Storage via `workers/model/storage.ensure_local_bundle` machinery
- Update ROADMAP.md + MODEL_WHITEPAPER.md (per OU25-MODEL-WHITEPAPER-UPDATE task)
- Flip `MODEL_VERSION_OU=ou25_dedicated_v1` on Railway (12-24h shadow first)

**If shelve**:
- Document negative finding in `dev/active/ou25-dedicated-model-results.md`
- Keep env-var override `MODEL_VERSION_OU=v14_recreate_2026_05_11`
- File `OU-LONGTERM-EXCLUDE-TIERC-FROM-OU-TRAINING` as the next path

## Risks

| Risk | Mitigation |
|---|---|
| CSV training corpus tilts toward European top-5 — may not generalise to MFV's wider league set | Evaluate holdout on the same league-mix as live betting; if narrow, document and ship narrow-league-only |
| Pinnacle OU 2.5 closing is itself sharp — same trap as AH-BOT-MODEL | Compare to soft-book consensus on holdout; if model beats both, more confident it's a real edge |
| Holdout window overlaps with prod model training data (v20260524_market trained through ~2026-05-17) — biased comparison | Carefully document training cutoffs per bundle; if overlap is unavoidable, also report Q1 2025 + 2024 H2 sub-holdouts where no bundle has overlap |
| Feature schema mismatch: CSV-era matches may have NULL in newer MFV columns (player ratings, etc.) | Restrict feature set to columns with ≥80% coverage in CSV-era; document chosen subset explicitly |
| 6-10h underestimate | If Phase 4 evaluation runs long, ship Phase 4 verdict with single-corpus (Pinnacle-only) and defer Bet365-breadth comparison to a follow-up |

## Decision gates

- **After Phase 1**: if dataset < 5K paired matches, abort and document data gap.
- **After Phase 4**: if log_loss improvement < 2% AND ROI@+5% lift < 1pp, abort — too marginal to ship.
- **After Phase 5**: ship/shelve per gate above.

## Deliverables

- `scripts/train_ou25_dedicated.py` (data loader + training + eval)
- `data/models/soccer/ou25_dedicated_v1/` bundle (if shipped)
- `dev/active/ou25-dedicated-model-results.md` (verdict report — required regardless of outcome)
- Updates to: PRIORITY_QUEUE.md, ROADMAP.md (if shipped), MODEL_WHITEPAPER.md (if shipped), SIGNALS.md (if shipped)
- Smoke: `scripts/smoke_test.py` — `OU25-DEDICATED-MODEL` (source-inspection at minimum)
