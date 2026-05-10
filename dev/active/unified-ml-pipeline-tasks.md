# Unified ML Pipeline — Task Checklist

> Working list. Mark items `[x]` as completed. See `unified-ml-pipeline-plan.md` for the full architecture.

## Stage 0 — Derived-data backfills

- [ ] **0a — ELO historical backfill**
  - File: `scripts/backfill_elo_historical.py` (new)
  - Logic: walk finished matches by `date ASC` from earliest backfilled date; for each match call same K=30 / home+100 / goal-diff math from `update_elo_ratings()`; upsert into `team_elo_daily`
  - Smoke test: latest team ELO matches what `update_elo_ratings()` would produce on tomorrow's run
  - Must be idempotent (re-running shouldn't double-count)

- [ ] **0b — Team form cache historical backfill**
  - File: `scripts/backfill_team_form_historical.py` (new)
  - Logic: walk distinct (team, date) pairs from finished matches; for each call `compute_team_form_from_db(team_id, before=date)` and upsert `team_form_cache`
  - Smoke test: form for known team on known date matches manual computation from `matches` table

- [ ] **0c — Referee stats rebuild**
  - Action: one-off call `python -c "from workers.api_clients.supabase_client import build_referee_stats; print(build_referee_stats())"`
  - Verify count went up significantly vs pre-call snapshot
  - No new code needed — function exists at `supabase_client.py:2775`

- [ ] **0d — team_season_stats from match_stats aggregation**
  - File: `scripts/backfill_team_season_stats.py` (new)
  - Logic: for each (team, league, season) with finished matches, aggregate from `match_stats` joined to `matches`: goals_for, goals_against, played count, win/draw/loss split
  - Upsert into `team_season_stats`
  - Smoke test: a known team's stats match manual aggregation query
  - Note: only computes for matches that have `match_stats` rows (~73.4% coverage)

- [ ] **0e — MFV historical rebuild**
  - File: `scripts/backfill_mfv_historical.py` (new)
  - Logic: list distinct dates where finished matches exist but no MFV row; call `build_match_feature_vectors(date)` for each
  - Smoke test: MFV row count grows from ~6,467 to ≥25,000
  - Must run AFTER 0a, 0b, 0c, 0d so feature lookups have data

## Stage 1 — Wire training to production

- [ ] **1a — Standardize train.py output paths**
  - Edit `workers/model/train.py`: change `MODELS_DIR` to point at `data/models/soccer/`; accept `--version` arg; outputs go to `data/models/soccer/{version}/{result_1x2,over_under,home_goals,away_goals,feature_cols}.pkl`
  - Match exact filenames `xgboost_ensemble.py` already loads

- [ ] **1b — MODEL_VERSION env var**
  - Edit `workers/model/xgboost_ensemble.py`: read `os.environ.get("MODEL_VERSION", "v9a_202425")` instead of hard-coded `MODEL_VERSION = "v9a_202425"`
  - Cache key includes the version so swapping doesn't break

- [ ] **1c — Add home_goals + away_goals models to train.py**
  - These regression models exist in `v9a_202425/` but train.py doesn't produce them
  - Add `train_home_goals_model` + `train_away_goals_model` (Poisson regression on score_home / score_away)

- [ ] **1d — Smoke test: round-trip**
  - In smoke_test.py: train a tiny model, save with `--version=smoke_test`, set `MODEL_VERSION=smoke_test`, call `_load_models()`, verify it returns models without error
  - Clean up test artifacts after

## Stage 2 — Missing-data handling

- [ ] **2a — Imputation + indicator columns**
  - Edit `workers/model/train.py`: replace `valid = X.notna().all(axis=1)` with:
    - For h2h_*, referee_*, opening_implied_*, bookmaker_disagreement: add `{col}_missing` indicator (1 if NULL, else 0); fill with per-league mean, fall back to global mean
    - Update FEATURE_COLS to include the new `_missing` columns
  - This is **ML-MISSING-DATA** in PRIORITY_QUEUE (~3h)

- [ ] **2b — Smoke test: row retention**
  - Assert `len(X_clean) >= 0.95 * len(X_input)` after preprocess

## Stage 3 — A/B harness

- [ ] **3a — Migration NNN: model_version columns**
  - Add `model_version TEXT` to: `predictions`, `simulated_bets`, `live_bets` (allow NULL)
  - Backfill existing rows with `'v9a_202425'` via UPDATE statement in the migration
  - Add an index on `(model_version, created_at)` for compare-script queries
  - Migration file: `supabase/migrations/087_model_version.sql`

- [ ] **3b — Live pipeline plumbing**
  - Read `os.environ.get("MODEL_VERSION", "v9a_202425")` in `xgboost_ensemble.py` once at startup
  - Pass version through to all `predictions` and `simulated_bets` writes
  - Audit: every `INSERT INTO predictions` and `INSERT INTO simulated_bets` includes the column

- [ ] **3c — Shadow mode**
  - Read `os.environ.get("MODEL_VERSION_SHADOW")` (optional)
  - When set, load second model, predict in parallel, write predictions row tagged `model_version=shadow_version`
  - Shadow predictions do NOT drive `simulated_bets` — bot still uses primary `MODEL_VERSION`

- [ ] **3d — compare_models.py**
  - File: `scripts/compare_models.py` (new)
  - CLI: `compare_models.py {version_a} {version_b} [--since DATE] [--market 1x2|over_under|btts]`
  - Pulls overlapping settled `predictions` for both versions on the same matches
  - Computes per market: log_loss, Brier, hit_rate, ROI, CLV
  - Output: rich table to stdout, JSON-row write to `model_comparisons` table for history (migration 088)

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
