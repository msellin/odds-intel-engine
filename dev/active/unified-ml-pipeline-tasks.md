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

- [ ] **0d — team_season_stats from match_stats aggregation**
  - File: `scripts/backfill_team_season_stats.py` (new)
  - Logic: for each (team, league, season) with finished matches, aggregate from `match_stats` joined to `matches`: goals_for, goals_against, played count, win/draw/loss split
  - Upsert into `team_season_stats`
  - Smoke test: a known team's stats match manual aggregation query
  - Note: only computes for matches that have `match_stats` rows (~73.4% coverage)

- [~] **0e — MFV historical rebuild** (in progress 2026-05-10 — running at ~+1K rows/min, will finish overnight; smoke-tested on 2026-05-09: ELO coverage went 53% → 100%)
  - File: `scripts/backfill_mfv_historical.py` (new)
  - Logic: list distinct dates where finished matches exist but no MFV row; call `build_match_feature_vectors(date)` for each
  - Smoke test: MFV row count grows from ~6,467 to ≥25,000
  - Must run AFTER 0a, 0b, 0c, 0d so feature lookups have data

## Stage 1 — Wire training to production

- [x] **1a — Standardize train.py output paths** (done 2026-05-10)
  - `train.py` now writes to `data/models/soccer/{version}/{result_1x2,over_under,btts,feature_cols}.pkl`. Smoke test enforces the rename.

- [x] **1b — MODEL_VERSION env var** (done 2026-05-10)
  - `xgboost_ensemble.py` reads `os.environ.get("MODEL_VERSION", DEFAULT_MODEL_VERSION)`. Smoke test enforces the read.

- [ ] **1c — Add home_goals + away_goals regression models to train.py**
  - These exist in `v9a_202425/` but train.py doesn't produce them
  - Add `train_home_goals_model` + `train_away_goals_model` (Poisson regression on score_home / score_away)
  - **Workaround for now**: `train_all` prints a hint to copy from v9a_202425 — works for first retrain, but blocks fully self-contained version bundles

- [x] **1d — Smoke test: train.py wiring** (done 2026-05-10)
  - Source-inspection test asserts filenames + paths + --version arg. Full round-trip (train → save → load) deferred to Stage 4 when we have data populated.

## Stage 2 — Missing-data handling

- [ ] **2a — Imputation + indicator columns**
  - Edit `workers/model/train.py`: replace `valid = X.notna().all(axis=1)` with:
    - For h2h_*, referee_*, opening_implied_*, bookmaker_disagreement: add `{col}_missing` indicator (1 if NULL, else 0); fill with per-league mean, fall back to global mean
    - Update FEATURE_COLS to include the new `_missing` columns
  - This is **ML-MISSING-DATA** in PRIORITY_QUEUE (~3h)

- [ ] **2b — Smoke test: row retention**
  - Assert `len(X_clean) >= 0.95 * len(X_input)` after preprocess

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

- [ ] **4a — Train v10_pre_shadow**
  - `python workers/model/train.py --version v10_pre_shadow`
  - Captures CV log_loss / Brier / accuracy in terminal output

- [ ] **4b — Manual CV comparison vs v9a_202425**
  - Re-run baseline CV by loading v9a_202425 model and scoring on the same Stage 0 dataset (caveat: v9a was trained on Kaggle data; CV scores aren't directly comparable but we can compute metrics on the new data)

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

- [ ] **5a — Weekly Sunday retrain cron**
  - Add APScheduler job in `workers/scheduler.py`: Sunday 03:00 UTC, runs `train.py --version v{YYYYMMDD}`
  - Logs to `pipeline_runs`

- [ ] **5b — Auto-comparison job**
  - Right after retrain, run `compare_models.py {new_version} {production_version}` and write result to `model_comparisons`
  - If delta crosses a configured threshold, post notification (existing email/digest channel)

- [ ] **5c — Promotion stays manual**
  - Document: only humans flip `MODEL_VERSION`. The cron prepares the candidate; the operator decides.

## Stage 6 — Backtesting harness (optional, parallel)

- [ ] **6a — `scripts/backtest_pre_match_bots.py`**
  - For each finished match in the backfill window, reconstruct the kickoff-time view: odds_snapshots filtered to `< match.date`, predictions filtered to `< match.date`, fresh features from MFV
  - Run pre-match bot strategy logic against that view
  - Record what each bot would have bet, what the actual outcome was, what the P&L would be

- [ ] **6b — Run all 16 bots over the historical window**
  - Output a CSV: `dev/active/backtest-pre-match-results.csv`
  - One row per (bot, match) — bet/no-bet, stake, odds, result, P&L

- [ ] **6c — Analysis**
  - Identify highest-edge subsets by league, by bot, by market
  - Retire underperformers
  - Update `bots` table — flag winners, retire losers
