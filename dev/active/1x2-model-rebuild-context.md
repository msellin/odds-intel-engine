Parent: [[#141]] 1X2-MODEL-REBUILD-2026-09-24

# 1X2 model rebuild — context

## Key files
- `workers/model/train.py` — current XGB trainer (MFV-fed, 67 cols)
- `workers/model/xgboost_ensemble.py` — serving; fill values at :401-405
- `workers/model/improvements.py:128-236` — shrinkage toward Pinnacle (α≈0 for 1X2)
- `scripts/weekly_eval_and_compare.py` — weekly eval; honest-holdout guard makes it exit 2
- `scripts/residual_test.py` — old binary 1X2 α harness (zero-fill)
- `scripts/ab_ou_feature_arms.py` — O/U arms harness (prior art for this one)
- `scripts/build_half_time_ratings.py` — HT/2H rating (#084)
- `dev/active/per-market-feature-sets-design.md` — literature + 1X2 difference set

## Production state (2026-09-24)
VPS `.env`: MODEL_VERSION=v20260712, MODEL_VERSION_1X2=v20260830,
MODEL_VERSION_OU(_T1)=v20260903_cut0820, DRAW_CAL_FACTOR=0.75.

## Decisions
- New harness computes ratings from `matches` scores (100% coverage), not from MFV.
- Research cache under `data/research/1x2/` (gitignored? check) or scratchpad — never write
  research data into `matches`.

## State at end of session 2026-09-24
- Better model BUILT and in SHADOW: `workers/model/ratings_1x2.py` + `workers/jobs/rating_1x2_shadow.py`
  (subprocess, 05:30/17:30 UTC) → `rating_1x2_predictions` (model_version `r1x2_d8plus_v1`).
  History: `rating_history_results` (258,222 rows, migration 412).
- Holdout 08-31..09-24: log-loss 1.0078 vs prod 1.0711; α vs Pinnacle = 0.
- pandas 3.0.4 on the VPS/CI segfaults on tz-aware datetime takes → module runs on integer day
  numbers, job keeps epoch seconds (RELIABILITY_LEDGER #26). Pinning pandas NOT done.
- Research cache (gitignored): `data/models/_research/1x2/` (matches/stats/pinnacle parquet,
  af_history.parquet, tuned_params.json).

## Update 2026-09-24 (later) — combined model live in shadow
- Round 3b COMBINED model (`workers/model/combined_1x2.py`, `market_consensus_1x2.py`) adopted:
  0.9763 vs 1.0711. VPS verified: full job 202 s / 866 MB (combiner fit on 54k matches), refresh
  1.2 s; P&C rows sit 0.020 from Pinnacle on average (rating-only: 0.101). Versions in
  `rating_1x2_predictions`: `r1x2_d8plus_v1` (rating) and `r1x2_comb_v1` (combined, `sources`).
- `bot_rating_1x2_v1` ("1x2 market NEW", migration 414) reads the RATING version.
- Round 3c (lineups) waits on the AF lineup/player backfill — owner arranging it.

## Next step
1. ~2026-10-01: `python3 scripts/ab_1x2_rating_arms.py --forward` (needs ≥2,000 settled rows).
2. Owner decision: replace the Poisson/XGB legs of the served 1X2 blend with the rating model. If yes:
   the stored 1X2 Platt params (`model_calibration` 1x2_*) were fitted on the old blend and must be
   refit or bypassed (the rating model is already calibrated); touch `ensemble_prediction` /
   `daily_pipeline_v2.py` ~3140 for 1X2 only; keep O/U untouched.
3. Separately worth a row if promotion is declined: the stored 1X2 blend is worse than uniform
   (Poisson leg, MODEL_WHITEPAPER §4.2 note).
