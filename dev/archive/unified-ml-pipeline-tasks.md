# Unified ML Pipeline — Task Checklist

> Working list. Mark items `[x]` as completed. See `unified-ml-pipeline-plan.md` for the full architecture.

## Stage 0 — Derived-data backfills

- [x] **0a — ELO historical backfill** (done 2026-05-10)
  - File: `scripts/backfill_elo_historical.py` (new)
  - Logic: walk finished matches by `date ASC` from earliest backfilled date; for each match call same K=30 / home+100 / goal-diff math from `update_elo_ratings()`; upsert into `team_elo_daily`
  - Smoke test: latest team ELO matches what `update_elo_ratings()` would produce on tomorrow's run
  - Must be idempotent (re-running shouldn't double-count)

- [x] **0b — Team form cache historical backfill** (done 2026-05-10 — 79,619 snapshots)
  - File: `scripts/backfill_team_form_historical.py` (new)
  - Logic: walk distinct (team, date) pairs from finished matches; for each call `compute_team_form_from_db(team_id, before=date)` and upsert `team_form_cache`
  - Smoke test: form for known team on known date matches manual computation from `matches` table

- [x] **0c — Referee stats rebuild** (done 2026-05-10 — 0 → 2,515 rows; UUID cast bug fixed in build_referee_stats)
  - Action: one-off call `python -c "from workers.api_clients.supabase_client import build_referee_stats; print(build_referee_stats())"`
  - Verify count went up significantly vs pre-call snapshot
  - No new code needed — function exists at `supabase_client.py:2775`

- [x] **0d — team_season_stats from match_stats aggregation** (done 2026-05-10)
  - File: `scripts/backfill_team_season_stats.py` (new) — single-pass aggregation, two SQL queries (home + away halves), merge per (team_api_id, league_api_id, season), then upserts via `store_team_season_stats` (same writer fetch_enrichment uses).
  - First run: 9,473 (team, league, season) groups identified; smoke test `ML-PIPELINE-UNIFY Stage 0d` enforces shape.
  - Note: only computes for matches with both `home_team_api_id` AND `leagues.api_football_id` populated.

- [~] **0e — MFV historical rebuild** (re-armed 2026-05-10 — earlier process died; ready to re-run after 0d finishes. Smoke-tested 2026-05-09: ELO coverage went 53% → 100%)
  - File: `scripts/backfill_mfv_historical.py` (new)
  - Logic: list distinct dates where finished matches exist but no MFV row; call `build_match_feature_vectors(date)` for each
  - Smoke test: MFV row count grows from ~6,467 to ≥25,000
  - Must run AFTER 0a, 0b, 0c, 0d so feature lookups have data

## Stage 1 — Wire training to production

- [x] **1a — Standardize train.py output paths** (done 2026-05-10)
  - `train.py` now writes to `data/models/soccer/{version}/{result_1x2,over_under,btts,feature_cols}.pkl`. Smoke test enforces the rename.

- [x] **1b — MODEL_VERSION env var** (done 2026-05-10)
  - `xgboost_ensemble.py` reads `os.environ.get("MODEL_VERSION", DEFAULT_MODEL_VERSION)`. Smoke test enforces the read.

- [x] **1c — Add home_goals + away_goals regression models to train.py** (done 2026-05-10)
  - Added `_train_goals_regressor` shared core + `train_home_goals_model` / `train_away_goals_model` thin wrappers.
  - Uses XGBoost `count:poisson` objective (matches xgboost_ensemble's Poisson side); 5-fold TimeSeriesSplit reports RMSE + Poisson deviance.
  - `train_all` runs them inline so a v10 bundle is self-contained — no more "copy from v9a" hint.
  - Smoke test `ML-PIPELINE-UNIFY Stage 1c` enforces the regressor presence + filenames.

- [x] **1d — Smoke test: train.py wiring** (done 2026-05-10)
  - Source-inspection test asserts filenames + paths + --version arg. Full round-trip (train → save → load) deferred to Stage 4 when we have data populated.

## Stage 2 — Missing-data handling

- [x] **2a — Imputation + indicator columns** (done 2026-05-10)
  - `_impute_features` does per-league mean → global mean → 0 fill. `INFORMATIVE_MISSING_COLS = [h2h_win_pct, opening_implied_*, bookmaker_disagreement, referee_*]` get `<col>_missing` indicators.
  - `_prepare_xy` shared by all three classifiers + the two goal regressors — single drop point: rows with NaN target only.
  - Saved `feature_cols.pkl` includes the augmented list (FEATURE_COLS + `_missing` cols), so xgboost_ensemble's `feature_cols` load picks them up at inference.

- [x] **2b — Smoke test: ML-PIPELINE-UNIFY Stage 2a** (done 2026-05-10)
  - Source-inspection smoke test asserts `_impute_features`, `INFORMATIVE_MISSING_COLS`, `_missing` token, AND that `valid = X.notna().all(axis=1)` row-drop is NOT present in code (docstring reference allowed).
  - Full row-retention assertion (`len(X_clean) >= 0.95 * len(X_input)`) deferred to Stage 4a — needs a real training run to measure, no point asserting on synthetic data.

## Stage 3 — A/B harness

- [x] **3a — Migration 087: model_version columns** (done 2026-05-10)
  - `model_version TEXT` added to `predictions` + `simulated_bets`. Existing rows backfilled to `'v9a_202425'`. Indexes on `(model_version, created_at)` and `(model_version, pick_time)`.
  - `live_bets` table doesn't exist — only the two columns needed.
  - Migration applies via GH Actions on push.

- [x] **3b — Live pipeline plumbing** (done 2026-05-10)
  - `_active_model_version()` helper in `supabase_client.py` reads `MODEL_VERSION` once. `store_prediction`, `bulk_store_predictions`, and `store_bet` all stamp the row. Smoke test enforces.

- [ ] **3c — Shadow mode**
  - Deferred until first real shadow candidate (v10) exists — implementing now would be theater.
  - Sketch: load second model bundle; produce parallel predictions; write to `predictions` with shadow tag; do NOT drive `simulated_bets`.

- [x] **3d — compare_models.py** (done 2026-05-10)
  - Pulls overlapping settled ensemble predictions for two versions, computes per-market log_loss + Brier + Δ. Negative Δ favours `version_a`.
  - Optional `--since DATE` and `--market` filters. Smoke test asserts arg shape and the `source = 'ensemble'` restriction.
  - ROI/CLV breakdown deferred — those flow from `simulated_bets` driven only by primary version, not shadow. Bot-level ROI compared via `/admin/bots` after promotion past shadow.
  - `model_comparisons` table not added — keep results in scrollback for now; if we need history, add with migration 088 later.

## Stage 4 — First retrain

- [x] **4a — Train v10_pre_shadow** (done 2026-05-10)
  - `python3 workers/model/train.py --version v10_pre_shadow` ran on the full 47,292 MFV rows. CV mean: 1X2 log_loss 0.7578 / acc 66.5%, OU 2.5 Brier 0.2460 / acc 55.5%, BTTS acc 52.6%, home_goals RMSE 1.13 / poisson_dev 0.99, away_goals RMSE 1.02 / poisson_dev 1.01.
  - Two fix-ups needed mid-run: coerce `FEATURE_COLS` to `pd.to_numeric` at load (Postgres NUMERIC → `decimal.Decimal` blew up `Series.mean()`), and `match_outcome` map updated to accept `'home'`/`'draw'`/`'away'` (MFV stores lowercase, not H/D/A).
  - Bundle saved to `data/models/soccer/v10_pre_shadow/` (gitignored — local disk only).

- [ ] **4b — CV comparison vs v9a_202425** — **BLOCKED on `ML-INFERENCE-MFV-WIRE`**
  - v9a's saved model expects Kaggle-era column names absent from MFV. Direct A/B on the same input requires the inference rewrite or a translation layer.

- [ ] **4c — Deploy as shadow**
  - Set `MODEL_VERSION_SHADOW=v10_pre_shadow` on Railway, redeploy
  - Live for ≥14 days

- [ ] **4d — Real comparison after shadow window**
  - `python scripts/compare_models.py v10_pre_shadow v9a_202425 --since 2026-XX-XX`
  - Need ≥200 settled matches per market for meaningful signal

- [ ] **4e — Promote or roll back**
  - If win on log_loss + Brier across all markets: promote (`MODEL_VERSION=v10`)
  - If mixed/loss: keep v9a as primary, log finding, plan next iteration
  - Update memory: what worked, what didn't

## Stage 5 — Continuous loop

- [x] **5a — Weekly Sunday retrain cron** (done 2026-05-10)
  - APScheduler job `weekly_retrain` registered for Sunday 03:00 UTC. `job_weekly_retrain` in `workers/scheduler.py` shells out to `python -m workers.model.train --version v{YYYYMMDD}` with a 30min timeout, then chains into auto-comparison.
  - Logged via `_run_job` wrapper — pipeline_runs entry per fire.

- [x] **5b — Auto-comparison job** (done 2026-05-10)
  - Same `job_weekly_retrain` invokes `python scripts/compare_models.py {new_version} {production_version}` after retrain succeeds.
  - Notification deferred — email/digest channel routing not wired (the comparison output lands in scheduler logs; ops dashboard catches via `_recent_errors` if either step fails).
  - Storing comparison runs to a `model_comparisons` table also deferred (no migration 088 — keep results in scheduler stdout for now, add the table when there's a UI to read it).

- [ ] **5c — Promotion stays manual** (no code; documented in scheduler block)
  - Cron prepares the candidate bundle on disk; the operator flips `MODEL_VERSION` env on Railway.

## Stage 6 — Backtesting harness (optional, parallel)

- [x] **6a — `scripts/backtest_pre_match_bots.py`** (done 2026-05-10)
  - Walks finished matches in the date range, pulls latest pre-kickoff ensemble prediction per (match, market) + best (max) pre-kickoff odds per (market, selection), applies each bot's edge / threshold / odds_range / min_prob / league_filter / tier_filter, picks the top-edge candidate per (bot, match), records flat-stake outcome.
  - Output CSV: `dev/active/backtest-pre-match-results.csv` (one row per active bet) + a per-bot summary table.
  - **Scope honesty**: replays the *filtering* layer only — does NOT reproduce the calibration stack, Pinnacle veto, sharp_consensus gate, alignment scoring, Kelly stake sizing, or league-bet exposure cap. Those depend on real-time caches not reconstructable from history. Use this output as a directional answer ("did this bot ever have edge in this league/era?"), NOT as a faithful replay.
  - Smoke run on 2026-04-15 → 2026-05-08 (6,142 matches): 3,253 bets across 16 bots; ROI table prints to terminal. Full-history run pending.
  - Smoke test `ML-PIPELINE-UNIFY Stage 6a` enforces script presence.

- [ ] **6b — Run all 16 bots over the historical window**
  - Output a CSV: `dev/active/backtest-pre-match-results.csv`
  - One row per (bot, match) — bet/no-bet, stake, odds, result, P&L

- [ ] **6c — Analysis**
  - Identify highest-edge subsets by league, by bot, by market
  - Retire underperformers
  - Update `bots` table — flag winners, retire losers
