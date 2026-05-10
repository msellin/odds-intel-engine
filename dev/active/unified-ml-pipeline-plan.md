# Unified ML Pipeline — Architecture Plan

> Started: 2026-05-10. Goal: turn the three disconnected ML pieces into one coherent loop that uses the data the historical backfill just produced. Owner: agent + Margus.

## The problem we're solving

Today the system has three unconnected pieces:

1. **Production XGBoost** — `xgboost_ensemble.py` loads `data/models/soccer/v9a_202425/`. Frozen Kaggle CSV training (~80K rows, 2022-25). Disconnected from our DB.
2. **`workers/model/train.py`** — Writes to `data/models/{result_model,over25_model,btts_model}.pkl`. Trains on `match_feature_vectors`. **Nothing reads these files.** Different paths, different filenames.
3. **`match_feature_vectors` (MFV)** — 6,467 rows. Built nightly by `build_match_feature_vectors(date)` from settlement. Pulls `predictions`, `odds_snapshots`, `team_elo_daily`, `team_form_cache`, `match_signals`. Only grows for matches that go through the live pipeline.

Plus the historical backfill we just finished (47,228 finished matches, 34,675 with stats, 44,102 with events) doesn't automatically feed any of these — `team_elo_daily` and `team_form_cache` are forward-only writers, so backfilled matches from 2023 have no ELO chain or form cache.

The goal is **one closed loop**:

```
DB raw data (matches, match_stats, match_events, odds_snapshots)
  ↓ derived-data backfill (ELO, form, referee, team_season_stats)
  ↓
match_feature_vectors  (one row per finished match, all features populated)
  ↓ train.py
data/models/soccer/{version}/  (versioned, loaded by xgboost_ensemble.py)
  ↓ live betting pipeline
predictions (model_version-tagged) → simulated_bets (model_version-tagged)
  ↓ settlement
model_evaluations (per-version metrics)
  ↓ shadow harness compares versions on overlapping matches
  ↓ promote new version or roll back
LOOP back to top: nightly settlement extends MFV → weekly retrain
```

## Stages, in execution order

### Stage 0 — Derived-data backfills (~1 day, mostly parallel)

**Premise.** The backfill stored raw matches/stats/events but the derived tables that train.py reads (ELO, form, referee, team_season_stats) are forward-only daily writers. They have no entries for historical matches. Without these, MFV rows for historical matches have NULL features and get dropped at training time.

**Order is mostly independent — most can run in parallel, some have dependencies.**

| ID | What | Tables produced | Effort | Depends on | Parallelizable |
|----|------|-----------------|--------|------------|----------------|
| 0a | ELO historical backfill | `team_elo_daily` | ~3h | — | yes |
| 0b | Team form cache backfill | `team_form_cache` | ~2h | — | yes |
| 0c | Referee stats rebuild | `referee_stats` | ~30m (one call) | — | yes |
| 0d | Team season stats from match_stats aggregation | `team_season_stats` | ~3h | — | yes |
| 0e | MFV historical rebuild | `match_feature_vectors` | ~2h | 0a, 0b, 0c, 0d | no |

After Stage 0: `match_feature_vectors` should grow from ~6,467 rows → ~30,000+ rows (constrained by the subset of finished matches with successful score_home/away + at least one foundational team-stat). Some odds-derived columns (`opening_implied_*`, `bookmaker_disagreement`) will be NULL on historical rows — handled in Stage 2.

### Stage 1 — Wire training to production (~half day)

**Premise.** `train.py` outputs don't match what `xgboost_ensemble.py` loads. They need to.

| ID | What | Effort |
|----|------|--------|
| 1a | Standardize train.py output to `data/models/soccer/{version}/{result_1x2,over_under,home_goals,away_goals,feature_cols}.pkl` | 1h |
| 1b | Add `MODEL_VERSION` env var read in `xgboost_ensemble.py:_load_models`, default to current `v9a_202425` for safety | 30m |
| 1c | `train.py` CLI accepts `--version` arg, writes to that subdir, also dumps `feature_cols.pkl` | 30m |
| 1d | Add a `home_goals` + `away_goals` model to train.py (current production has these; train.py doesn't) | 1h |
| 1e | Smoke test: round-trip a model save → reload via xgboost_ensemble | 30m |

After Stage 1: a fresh `python train.py --version v10_test` produces files that `xgboost_ensemble.py` can load when `MODEL_VERSION=v10_test` is set.

### Stage 2 — Missing-data handling (~half day)

**Premise.** `train.py` does `valid = X.notna().all(axis=1)` which drops any row with any NULL feature. Historical matches lack opening odds (we didn't capture them in 2023). Without this fix, all historical rows get dropped and the MFV expansion is wasted.

This is the existing **ML-MISSING-DATA** task in PRIORITY_QUEUE.md, ~3h.

| ID | What | Effort |
|----|------|--------|
| 2a | Replace per-row drop with imputation: per-league mean fill for numeric, indicator columns (`{col}_missing`) for the columns where missingness is informative (h2h_*, referee_*, opening_implied_*) | 2h |
| 2b | Smoke test: assert training set retains ≥95% of input rows after preprocess | 30m |

After Stage 2: training set scales with MFV rather than being capped by completeness of any single feature.

### Stage 3 — A/B harness (~half day)

**Premise.** Without per-row model-version tagging, you can never measure whether a retrain helped. Comparison-by-deploy-date is contaminated by league mix, weather, fixture density.

| ID | What | Effort |
|----|------|--------|
| 3a | Migration NNN: add `model_version TEXT` to `predictions`, `simulated_bets`, `live_bets` (NULL allowed — backfill existing rows with current `'v9a_202425'`) | 1h |
| 3b | Live pipeline reads `MODEL_VERSION` from env, passes to all `predictions` and `simulated_bets` writes | 1h |
| 3c | Optional `MODEL_VERSION_SHADOW` — when set, the shadow model also predicts (separate row in `predictions` with shadow tag), but does NOT drive `simulated_bets`. Keep shadow predictions visible only to admin pages | 1h |
| 3d | `scripts/compare_models.py {version_a} {version_b} [--since=DATE]` — pulls overlapping settled matches, computes log_loss / Brier / hit rate / ROI / CLV per market, prints diff table. Stores comparison run to a new `model_comparisons` table for history | 2h |

After Stage 3: `predictions.model_version IS NOT NULL` for every new pred. Shadow mode can be flipped on by setting an env var and rolling Railway. The comparison script gives a single defensible answer to "did the new model help?"

### Stage 4 — First retrain through the harness (~half day)

| ID | What | Effort |
|----|------|--------|
| 4a | Run `python workers/model/train.py --version v10_pre_shadow` on the full MFV (post-Stage 2) | 30m run + manual review |
| 4b | Compare `v10_pre_shadow` CV scores in terminal vs `v9a_202425` baseline (manually re-run baseline if needed) | 30m |
| 4c | If meaningfully better in CV: deploy as `MODEL_VERSION_SHADOW=v10_pre_shadow` to Railway. Live for ~2 weeks | trivial deploy |
| 4d | After shadow window, run `compare_models.py v10_pre_shadow v9a_202425 --since={shadow_start_date}` | 30m |
| 4e | Promote (set `MODEL_VERSION=v10`) or roll back — document outcome | trivial deploy + memory |

After Stage 4: First measured retrain in production. We know what the lift was, by market, by tier.

### Stage 5 — Continuous loop (ongoing)

Once Stages 0-4 land, the loop runs itself:
- Nightly settlement already calls `build_match_feature_vectors(yesterday)` — MFV grows by ~250-400 rows/day.
- Add a weekly Sunday cron: `python workers/model/train.py --version v{YYYYMMDD}` → produces a candidate version.
- `compare_models.py` runs as part of the same cron; if delta exceeds a threshold and the new version wins, post a Slack/Discord/Email notification asking the operator to flip `MODEL_VERSION` env.

We do NOT auto-promote. Human approval before swapping the production model.

### Stage 6 — Backtesting harness (optional, parallel to Stages 1-4)

**Premise.** Stage 4 takes 2 weeks to deliver a real-world A/B because shadow mode is forward-only. Backtesting closes that gap.

| ID | What | Effort |
|----|------|--------|
| 6a | `scripts/backtest_pre_match_bots.py` — for each historical match: reconstruct the state the bot would have seen at kickoff (odds_snapshots taken before kickoff, predictions taken before kickoff), run bot logic, record what it would have bet, compare to actual outcome | 1 day |
| 6b | Backtest 16 pre-match bots over the historical window | 2h compute |
| 6c | Use results to retire underperforming bots, identify highest-edge subsets | manual analysis |

Stage 6 is not on the critical path for "first retrained model in production" but is the highest-ROI use of the historical data for **strategy work** rather than model work.

## Critical path

Shortest path to a measured production model:

```
Stage 0 (derived backfills, parallel) → Stage 1 (wire) → Stage 2 (NaN handling)
                                                              ↓
                                                       Stage 4a-4b (offline retrain)
                                                              ↓
                                                  Stage 3 (harness; can start in parallel with 0/1/2)
                                                              ↓
                                                       Stage 4c-4e (shadow → promote)
```

If parallelized: ~2 days of agent/operator work + ~2 weeks elapsed for the shadow window.

## Risks

| Risk | Mitigation |
|------|-----------|
| Historical ELO computed retroactively might not match what real-time ELO would have shown (no live updates between matches in the same week) | Document this. ELO is approximate anyway. Worst case: feature is slightly noisier on backfilled rows, but the distribution shift is small (matches happen on the same dates as live games would have). |
| MFV backfill might surface bugs in feature computation that NULL-handling masks today | Run on a small sample first (one date), eyeball the output, then full run |
| New model could overfit on a particular league subset | TimeSeriesSplit CV in train.py already partially guards. Stage 4 shadow window is the real test. |
| Shadow mode adds load to `predictions` table | Each match adds one extra row per market per shadow model. ~80 matches/day × 4 markets = 320 rows/day. Trivial. |
| A retrain that doesn't beat baseline | Stage 4e roll-back is trivial — no data lost, just don't promote. The harness pays back immediately on the *next* attempt. |

## Out-of-scope (deliberate)

- Switching to LightGBM/CatBoost (ML-HYPERPARAMS in queue) — separate experiment after this baseline retrain
- Pinnacle-as-feature (ML-PINNACLE-FEATURE) — separate
- Per-league models (ML-PER-TIER) — separate
- In-play model retrain — that's a different model with different inputs, separate doc

## Success criteria

- After Stage 0: `match_feature_vectors` row count > 25,000 (currently 6,467).
- After Stage 1+2: `python train.py --version test` produces files xgboost_ensemble loads cleanly.
- After Stage 3: every row in `predictions` written after Stage 3 deploy has `model_version IS NOT NULL`.
- After Stage 4: documented log_loss/Brier delta between v9a_202425 and v10 on overlapping settled matches, with at least 200 settled matches per market in the comparison.
- After Stage 5: weekly retrain cron green for 4 consecutive weeks.
