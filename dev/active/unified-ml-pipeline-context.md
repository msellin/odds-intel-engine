# Unified ML Pipeline — Context

> Decisions, key files, current state. Read this first when resuming a session.

## Where we are right now

Plan written 2026-05-10 immediately after BACKFILL-LIVELOCK fix landed. Backfill at terminal coverage (47,228 finished matches; match_stats 73.4%, match_events 93.4%). MFV currently at 6,467 rows. **Stage 0 has not started yet.**

## Key files

| File | Why it matters |
|------|----------------|
| `workers/model/xgboost_ensemble.py` | Production model loader — loads from `data/models/soccer/{MODEL_VERSION}/`. Hard-coded `MODEL_VERSION = "v9a_202425"` at line 28 — needs to read env. |
| `workers/model/train.py` | Trainer that reads `match_feature_vectors`, writes to `data/models/{result_model,over25_model,btts_model}.pkl`. **Output paths don't match xgboost_ensemble.py expectations.** |
| `workers/api_clients/supabase_client.py:1050` | `build_match_feature_vectors(client, date_str)` — MFV ETL. Pulls from predictions, odds_snapshots, team_elo_daily, team_form_cache. NULL-tolerant: builds rows even when these are missing. |
| `workers/api_clients/supabase_client.py:2775` | `build_referee_stats()` — single-call rebuild for referee aggregates. No new code needed for Stage 0c. |
| `workers/jobs/settlement.py:1210` | `update_elo_ratings()` — daily forward-only ELO writer. Stage 0a backfill must replicate this logic in date-walking script. |
| `workers/jobs/settlement.py:1305` | `update_team_form_cache()` — daily forward-only form writer. Stage 0b same pattern. |
| `workers/api_clients/supabase_client.py:1703` | `store_team_season_stats(api_id, league, season, parsed)` — writer signature. Currently only called from `fetch_enrichment.py:231` with AF API data, never from `match_stats` aggregation. |
| `data/models/soccer/v9a_202425/` | Current production model files: `result_1x2.pkl`, `over_under.pkl`, `home_goals.pkl`, `away_goals.pkl`, `feature_cols.pkl`. Trained on Kaggle CSVs, not our DB. |
| `dev/active/unified-ml-pipeline-plan.md` | The full plan |
| `dev/active/unified-ml-pipeline-tasks.md` | Task checklist |

## Decisions made

1. **Don't rip out v9a_202425 production model.** Keep it as the safe default until a measured A/B win promotes a successor. The wiring change in Stage 1b makes `MODEL_VERSION` env-controlled — flipping is one Railway redeploy.

2. **MFV builds tolerate NULL features.** Confirmed by reading `build_match_feature_vectors` — it returns rows even when predictions/odds_snapshots/ELO/form are missing. The blocker is downstream: train.py drops rows with any NULL. Fix lives in Stage 2 (ML-MISSING-DATA), not in MFV builder.

3. **Stage 0d (team_season_stats from match_stats) replicates fetch_enrichment's writer signature.** Don't change the table schema — just write to it from a different source. Live `fetch_enrichment.py` keeps writing the AF-API path forward; the backfill script fills in historical seasons.

4. **Promotion stays manual.** Even after Stage 5 cron, no auto-flip of MODEL_VERSION. The cron prepares the candidate + comparison report; a human approves.

5. **Backtesting (Stage 6) is parallelizable but off the critical path.** It's the highest-ROI use of historical data for *strategy* work, but separate from the *model retrain* loop. Treat as parallel track.

6. **A/B harness (Stage 3) is the lever that pays back forever.** Even if v10 doesn't beat v9a, the harness lets every future change be evaluated cleanly. This is why we're not skipping straight to retrain.

## What I previously got wrong

Earlier in the session I claimed "historical matches without predictions/odds_snapshots can't produce MFV rows." Verified false by reading `build_match_feature_vectors` — those columns just become NULL in the row. The actual blocker was train.py's `X.notna().all(axis=1)` row-drop. Fix is Stage 2.

I also conflated "won't help XGBoost retrain easily" with "won't help anything." The backfill helps **runtime feature computation** (h2h, in-play baselines, referee, form features once Stage 0 runs) regardless of whether XGBoost ever gets retrained. Stage 0 alone delivers value.

## How to resume

Read `unified-ml-pipeline-tasks.md`. Find the lowest unchecked item. If Stage 0 not done, start with 0c (referee — one function call, lowest risk) to verify the harness, then 0a or 0b in parallel.

Always run new backfill scripts on a **single date / small subset first** before unleashing on the full historical window. Reasons: (1) catches schema/dependency bugs in seconds vs minutes, (2) lets you eyeball one row of output for sanity, (3) gives a cost estimate for the full run.

## Update 2026-05-10 (afternoon batch)

Stage 0d, 1c, 2a/2b, 4a, 5a/5b, 6a all shipped in one push. Full status update is in `PRIORITY_QUEUE.md` header; tasks file has per-item details.

**MFV row count check (afternoon).** Plan said ~6,467; actual = **47,292 rows**. Stage 0e was effectively already complete from a prior session. Re-running `backfill_mfv_historical.py --skip-existing-dates` is a safe no-op confirmation.

**Stage 4a result (47,292 rows, 5-fold TimeSeriesSplit).** Two small fixes were needed before train.py ran clean: coerce all `FEATURE_COLS` to `pd.to_numeric` at load (Postgres NUMERIC came back as `decimal.Decimal`, blowing up `Series.mean()` in mixed-NaN columns), and accept lowercase `home`/`draw`/`away` in `match_outcome` map (MFV stores lowercase, not H/D/A).

  | Market | Mean Acc | Mean log_loss / Brier |
  |---|---|---|
  | 1X2     | 66.5%    | 0.7578 (log_loss) |
  | OU 2.5  | 55.5%    | 0.2460 (Brier) |
  | BTTS    | 52.6%    | — |
  | Home goals (Poisson) | RMSE 1.13 | poisson_dev 0.99 |
  | Away goals (Poisson) | RMSE 1.02 | poisson_dev 1.01 |

  Top 1X2 feature importances: `elo_diff` (0.281), `form_ppg_home` (0.081), `form_ppg_away` (0.071), `elo_away` (0.057), `elo_home` (0.054), `opening_implied_draw_missing` (0.041), `opening_implied_away_missing` (0.035), `opening_implied_home_missing` (0.025). The `_missing` indicators showing up in the top 10 vindicates Stage 2a — the model is learning from missingness, not just imputed values.

  Caveat — fold 5 (most-recent slice) reports 1X2 log_loss 0.4804 vs ~0.81 for folds 1-4. Likely a class-balance shift in late-season fixtures. Worth investigating before promotion but not blocking the offline harness.

**Stage 4b status.** Real comparison vs v9a is blocked on `ML-INFERENCE-MFV-WIRE` — v9a's saved model expects Kaggle-era column names absent from MFV, so we can't score it on the same input. Until that lands, the v10 CV scores above stand alone.

Newly discovered issue — **`xgboost_ensemble.py` inference path uses Kaggle-era column names** (`home_elo`, `h_*`, `a_*`, `xg_diff`, `form_diff`, `tier`). When `feature_cols.pkl` from a v10+ bundle is loaded, the `pd.DataFrame([row])[feature_cols]` line at xgboost_ensemble.py:181 will KeyError on `elo_home`/`form_ppg_home`/etc. Logged as `ML-INFERENCE-MFV-WIRE` in the queue. **Stage 4c shadow deploy is blocked until this is fixed.** Stage 4a/4b (offline retrain + CV via compare_models.py) is unblocked and can run as soon as 0e finishes.

Two clean fixes:
1. **(Preferred)** Rewrite `_predict_xgb` to fetch the `match_feature_vectors` row directly by `match_id` and feed it to the model — drops the two-table fetch (`team_form_features` + ELO) for one MFV read.
2. Build a translation layer mapping Kaggle names → MFV names — works but adds a brittle adapter.

Either way the rewrite touches ~50 lines in xgboost_ensemble.py:138-220.

Pending operational steps (NOT calendar-bound):
- Run 0e (`python3 scripts/backfill_mfv_historical.py`) once 0d finishes — should take MFV from ~6,467 → ≥25,000 rows.
- Run 4a (`python3 workers/model/train.py --version v10_pre_shadow`) — first measured retrain on full MFV.
- Run 4b CV comparison — manual eyeball vs v9a baseline.

Calendar-bound (cannot compress):
- 4c shadow deploy: needs ML-INFERENCE-MFV-WIRE first, then 14 days live.
- 4d real-world comparison: needs ≥200 settled matches per market in shadow window.
- 4e promotion: gated on the OU-cleanup track + 4d outcome.
- 5b "4 consecutive weeks green" — calendar.
