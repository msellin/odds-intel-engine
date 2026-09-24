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

## Next step
Build data cache + ratings in `scripts/ab_1x2_rating_arms.py`.
