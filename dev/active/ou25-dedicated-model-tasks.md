# OU25-DEDICATED-MODEL-INVESTIGATE — Tasks

**Mirrors conversation TaskList #1-#6.** Mark items as you go.

## Phase 1 — Data audit

- [ ] Query: count Pinnacle OU 2.5 closing rows (with paired over+under same match) since 2024-01-01
- [ ] Query: count Bet365 OU 2.5 closing rows since 2024-01-01
- [ ] Query: subset that joins to finished `matches` (score_home + score_away both NOT NULL)
- [ ] Query: subset with non-NULL MFV row for each feature col in the proposed subset
- [ ] Pick: training corpus (Pinnacle-only primary; Bet365 fallback if Pinnacle < 5K)
- [ ] Pick: train/test cutoff date based on row distribution
- [ ] Write findings to `ou25-dedicated-model-context.md` (Decisions Made section)

## Phase 2 — Architecture spec

- [ ] Decide PoissonRegressor library (sklearn.linear_model.PoissonRegressor vs xgboost `count:poisson`)
- [ ] Lock feature subset (start with proposed list in plan, narrow by coverage)
- [ ] Sketch joint-matrix inference wrapper class (sklearn-classifier interface for over_under.pkl)
- [ ] Write inference contract assertion to smoke_test.py stub

## Phase 3 — Dataset loader

- [ ] Create `scripts/train_ou25_dedicated.py` skeleton
- [ ] Implement loader → DataFrame[features, home_goals, away_goals, pinnacle_over_implied, pinnacle_under_implied]
- [ ] Verify row counts match Phase 1 audit
- [ ] Spot-check 5 random rows (label correctness, feature non-NULL)

## Phase 4 — Train + evaluate

- [ ] Fit home_goals + away_goals Poisson regressors on training set
- [ ] Build inference wrapper that converts (X_test → exp_h, exp_a → joint matrix → over25_prob)
- [ ] Evaluate ou25_dedicated_v1 on holdout — log_loss, brier, ECE
- [ ] Evaluate v14 on the same holdout matches
- [ ] Evaluate v20260524_market on the same holdout matches
- [ ] Evaluate v14_recreate_2026_05_11 on the same holdout matches
- [ ] Compute ROI@+5% edge for each (4 bundles)
- [ ] Compute ROI@+10% edge for each
- [ ] Save results table to `dev/active/ou25-dedicated-model-results.md`

## Phase 5 — Verdict + deploy/shelve

- [ ] Apply ship gate (≥5% log_loss OR ≥2pp ROI@+5% vs v14_recreate_2026_05_11)
- [ ] If ship: save bundle dir with `over_under.pkl + home_goals.pkl + away_goals.pkl + feature_cols.pkl`
- [ ] If ship: upload to Supabase Storage via `workers/model/storage`
- [ ] If ship: 12-24h `SHADOW_MODEL_VERSION=ou25_dedicated_v1` validation
- [ ] If ship: flip `MODEL_VERSION_OU=ou25_dedicated_v1` on Railway
- [ ] Add smoke test `OU25-DEDICATED-MODEL`
- [ ] Update PRIORITY_QUEUE.md (mark Done + write findings into the task body)
- [ ] If ship: update ROADMAP.md active model row + MODEL_WHITEPAPER.md §3 (OU25-MODEL-WHITEPAPER-UPDATE folds in)
- [ ] If shelve: document negative finding + keep env-var override
- [ ] Single commit with all of the above
