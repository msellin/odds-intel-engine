# OddsIntel — Model Whitepaper

> Technical specification of the prediction and betting system.
> Written for data scientists, auditors, and technical stakeholders.
> Last updated: 2026-05-10

---

## 1. Problem Statement

Bookmaker odds encode probabilities of match outcomes. When a bookmaker's implied probability diverges from the true probability, a positive expected value (EV) opportunity exists. OddsIntel builds a quantitative model to:

1. Estimate match outcome probabilities independently of bookmaker odds
2. Identify matches where our estimate diverges meaningfully from the market
3. Size bets proportionally to the estimated edge using Kelly criterion
4. Track performance via Closing Line Value (CLV) — the gold standard for betting model validation

The core thesis: **bookmaker pricing is less efficient in lower-tier leagues** (divisions 2-4, smaller countries) because bookmakers invest less modelling effort there. Our model exploits this structural inefficiency.

---

## 2. Data Sources

| Source | Data | Frequency | Cost |
|--------|------|-----------|------|
| API-Football (Ultra) | Fixtures, results, odds (13 bookmakers incl. Pinnacle), lineups, injuries, standings, H2H, player stats, live data | Multiple daily | $29/mo |
| ESPN | Settlement results backup | Daily | Free |
| Gemini 2.5 Flash | AI news analysis (injuries, manager changes, tactical shifts) | 4x daily | Free |

**Coverage:** 280+ leagues, 13 bookmakers tracked (including Pinnacle), ~280 matches analysed daily.

Note: Kambi API was removed 2026-05-06 after analysis showed Unibet odds (the main Kambi source) are already included in the API-Football 13-bookmaker feed.

### 2.1 Data quality gates (added 2026-05-10, ODDS-QUALITY-CLEANUP)

Three sources from the AF feed were found to ship clearly broken Over/Under
data: the synthetic `api-football` source (100% of OU pairs invalid, avg
implied-sum 0.63 — not a betting market), `William Hill` (88% Under-favored
on OU 1.5, line labels appear shifted), and `api-football-live` (in-play live
odds leaking into pre-match best-price aggregation). All three are excluded
from OU markets at both ingestion and the read-path best-price aggregator.
1X2 and BTTS rows from the same sources are kept — those markets verified
clean across every bookmaker (<0.05% invalid pair rate).

A second gate — **implied-sum sanity** (`1/over + 1/under ≥ 1.02`) — drops both
sides of any mathematically-impossible pair, auto-quarantining future broken
sources without code changes. Constants live in `workers/utils/odds_quality.py`.

**Verified unaffected** (no rebuild needed): `match_feature_vectors`
(`build_match_feature_vectors` reads `market='1x2'` only), Platt calibration
(fits on predictions vs match outcomes, no odds), ELO (match results only).
Bot bankrolls and `simulated_bets` were repaired in the same commit (~$257 of
phantom PnL erased across 8 bots; 53 settled OU bets voided where their
`odds_at_pick` no longer matched any surviving snapshot).

---

## 3. Feature Engineering

### 3.1 Feature Set — Production Model (Kaggle v9a, 36+ features)

The currently deployed XGBoost model (`xgboost_ensemble.py`) was trained on the Kaggle v9a dataset.
All rolling statistics computed from the **10 most recent matches** per team, split by home/away venue.

| Group | Features | Count |
|-------|----------|-------|
| **Home Form** | win%, PPG, goals scored/conceded, goal diff, O2.5%, BTTS%, clean sheet% | 8 |
| **Home at Home** | venue-specific: win%, goals scored/conceded, O2.5% | 4 |
| **Away Form** | Same 8 metrics for away team | 8 |
| **Away at Away** | venue-specific: win%, goals scored/conceded, O2.5% | 4 |
| **Head-to-Head** | home win%, avg goals, O2.5%, BTTS%, total meetings (last 10 H2H) | 5 |
| **League Position** | normalised rank, points to relegation/title, in-relegation flag, position diff | 7 |
| **Rest & Context** | rest days raw (home/away), log-transformed rest days `log(days+1)` (REST-NONLINEAR), fixture urgency (points gap / games rem × 3), games remaining, away turf experience | 8 |
| **ELO** (at inference) | home ELO, away ELO, ELO differential, expected win probability from ELO | 4 |
| **Form vs ELO Residual** | `form_vs_elo_expectation_home/away` = actual recent PPG minus ELO-predicted PPG (`3 × p_win + 0.27`) — isolates hot/cold streaks from baseline quality (FORM-ELO-RESIDUAL) | 2 |

**Defaults:** When insufficient history exists (new teams, new season), features default to league averages or neutral values (e.g. H2H defaults to 0.33 for 3-way split).

### 3.1b Feature Set — AF Retrain Model (`workers/model/train.py`)

The new AF model trains on `match_feature_vectors` — live pipeline data accumulated since 2026-04-27, plus historical rebuild via `scripts/backfill_mfv_historical.py` (Stage 0e of ML-PIPELINE-UNIFY).
Column names match the table exactly.

**Missing-data handling (Stage 2a, 2026-05-10).** The prior `valid = X.notna().all(axis=1)` row-drop lost ~30-40% of rows because H2H is structurally absent for newly-promoted pairings, opening odds are absent for pre-2026-Q2 matches the engine wasn't yet watching, and referee features are absent for unstaffed fixtures. The new pipeline imputes per-league mean (with a global-mean fallback for leagues with no observations) and adds `<col>_missing` indicator columns for the features where missingness *itself* carries signal:

  `h2h_win_pct_missing`, `opening_implied_home_missing`, ..., `referee_over25_pct_missing`, `pinnacle_implied_home_missing`, ..., `pinnacle_implied_btts_yes_missing` (full list in `INFORMATIVE_MISSING_COLS`, `train.py:48`)

The model can split on the indicator alongside the imputed value, learning that "we don't have H2H" predicts differently from "H2H exists and shows 50%". Saar-Tsechansky & Provost (2007 JMLR) shows this matches KNN imputation in accuracy at 1/100th the cost.

**Base feature set (FEATURE_COLS, 42 columns as of `v_20260525_signals`+):**
*(was 32 columns in v15+ → added 10 more in the 2026-05-25 signal batch — see "MFV-V3 batch" rows below)*

| Group | Column name(s) | Count |
|-------|---------------|-------|
| **ELO** | `elo_home`, `elo_away`, `elo_diff` | 3 |
| **Form** | `form_ppg_home`, `form_ppg_away` | 2 |
| **Goals** | `goals_for_avg_home`, `goals_for_avg_away`, `goals_against_avg_home`, `goals_against_avg_away` | 4 |
| **Standings** | `league_position_home`, `league_position_away`, `points_to_relegation_home`, `points_to_relegation_away`, `points_to_title_home`, `points_to_title_away` | 6 |
| **H2H** | `h2h_win_pct` | 1 |
| **Rest** | `rest_days_home`, `rest_days_away` | 2 |
| **Injury / News** | `injury_count_home`, `injury_count_away` | 2 |
| **Match context** | `fixture_importance` | 1 |
| **Referee** | `referee_cards_avg`, `referee_home_win_pct`, `referee_over25_pct` | 3 |
| **Weather** | `weather_temp_c`, `weather_wind_kmh`, `weather_rain_mm`, `weather_humidity` | 4 — Open-Meteo at kickoff; ~0% coverage on 2026-05-11, rising as venues geocode |
| **Market** | `opening_implied_home`, `opening_implied_draw`, `opening_implied_away`, `bookmaker_disagreement` | 4 |
| **League** | `league_tier` | 1 |
| **Form momentum (MFV-V3 batch 2026-05-25)** | `form_momentum_home`, `form_momentum_away` (= last-3 ppg − last-10 ppg) | 2 |
| **Injury severity (MFV-V3 batch)** | `injury_severity_score_home`, `injury_severity_score_away` (SEVERE×3 + MODERATE×1.5 + MINOR×0.5 + UNKNOWN×1) | 2 |
| **League market signals (MFV-V3 batch)** | `league_draw_rate_ytd` (backtest +11.6pp Q4-vs-Q1 draw lift), `season_progress` (late vs early +7.7pp Over 2.5), `line_velocity` (Pinnacle T-12h→T-2h slope; REVERSE signal -6.6pp CLV-beat at high \|v\|), `league_clv_efficiency` (60d mean pseudo_clv per league) | 4 |
| **Team xG / ratings (MFV-V3 batch)** | `xg_overperf_home/away` (rolling 10-match goals − xG, regression-to-mean indicator), `team_avg_player_rating_home/away` (AF player ratings, sparse ~5% coverage) | 4 |

**Pinnacle features (PINNACLE_FEATURE_COLS, `--include-pinnacle`, v11+, 3 columns):**

| Group | Column name(s) | Coverage | Notes |
|-------|---------------|---------|-------|
| **Pinnacle 1X2** | `pinnacle_implied_home`, `pinnacle_implied_draw`, `pinnacle_implied_away` | ~23% | Latest pre-KO Pinnacle 1X2 snapshot; overround left in deliberately |

**OU/BTTS market features (OU_MARKET_FEATURE_COLS, `--include-ou-market`, v14+, 4 columns):**

| Group | Column name(s) | Coverage | Notes |
|-------|---------------|---------|-------|
| **Pinnacle OU 2.5** | `pinnacle_implied_over25`, `pinnacle_implied_under25` | ~22% | Overround guard `< 1.10` drops 2.4% bad pairs |
| **OU disagreement** | `ou25_bookmaker_disagreement` | ~60% | max-min implied_over25 across distinct books (blacklist-filtered) |
| **BTTS market** | `market_implied_btts_yes` | ~30% | avg 1/yes_odds across distinct books (Pinnacle BTTS = 0% coverage → multi-book) |

**Target columns** (from `match_feature_vectors` + matches JOIN):
- `match_outcome` (H/D/A) → 1X2 result model
- `over_25` (bool) → Over/Under 2.5 model
- `btts` (bool, derived at load time: `score_home > 0 AND score_away > 0`) → BTTS model

**Training commands:**
- v12-style (no Pinnacle): `python3 workers/model/train.py --version v_YYYYMMDD`
- v11+-style (with Pinnacle): add `--include-pinnacle`
- v14+-style (with OU/BTTS market): add `--include-pinnacle --include-ou-market`

As of 2026-05-11 the table holds **48,240 settled rows** (post-Stage-0e refresh).

**Bundle storage & versioning (ML-BUNDLE-STORAGE, 2026-05-10).** Every successful train auto-uploads the bundle to Supabase Storage (`models/<version>/*.pkl`) and registers a row in the `model_versions` table (trained_at, training_window, n_rows, feature_cols, cv_metrics, promoted_at, demoted_at, notes). This solves Railway's ephemeral-filesystem problem: a fresh container with `MODEL_VERSION=v_X` set hits `xgboost_ensemble._load_models()`, sees no local copy, calls `ensure_local_bundle()`, downloads from Storage, caches for the container's lifetime. Switch versions by setting `MODEL_VERSION` env var on Railway → next deploy auto-pulls. Historical bundles stay in Storage forever — rollback is one env-var change. Costs ~$0.05/mo for 5 years of weekly bundles. Full architecture, port-to-other-projects guide, and gotchas in `docs/ML_MODEL_REGISTRY.md`.

**Active production version:** `v20260524_market` since 2026-05-24. **Candidate queued for Sunday 2026-06-07 cron retrain:** `v_20260525_signals` — trained 2026-05-25 on the extended FEATURE_COLS (42 columns, adds 10 over the v14-era 32 — form_momentum, injury_severity_score, league_draw_rate_ytd, season_progress, line_velocity, xg_overperf, league_clv_efficiency, team_avg_player_rating). Offline eval vs production on 2,522 matches in 2026-05-20..05-24: **beats production on 4/5 markets** (1X2 −6.5% to −7.8% log-loss, BTTS −2.9%, OU 2.5 tied). Calibration regresses slightly on draw + over_25 — likely to be fixed by the standard Sunday 2026-06-07 weekly_retrain with a clean week of post-signal data. **Not deployed mid-Phase-3.5** (lock until 2026-06-07). Decision rule on 2026-06-07: run `offline_eval v20260607 v_20260525_signals v20260524_market` three-way and deploy market-by-market via per-market `MODEL_VERSION_*` env overrides if calibration is mixed. Full report: `dev/active/model-comparison-20260525-signals-vs-market.md`.

**Per-market evaluation (MARKET-EVAL-BTTS-AH, 2026-05-24).** The weekly held-out eval at `scripts/weekly_eval_and_compare.py` originally scored only the `result_1x2` and `over_under` XGBoost heads, leaving BTTS and AH unmeasured — those markets are derived in production from the Poisson `home_goals` + `away_goals` regressors via `workers.model.joint_probability.build_joint_matrix()`, and a new bundle could move them in either direction without the eval noticing. The script now also loads `home_goals.pkl` + `away_goals.pkl` from each bundle, builds the same DC-corrected joint matrix used at inference, and scores `btts_yes/no` log-loss against `(score_home > 0 AND score_away > 0)` truth plus four AH half-lines (`ah_home_±0.5`, `ah_home_±1.5`) against `(score_home − score_away + line) > 0`. Half-lines only so binary log-loss is unambiguous (no push complication). First retro run on the 2026-05-10..05-24 holdout (n=7,246) revealed both candidate bundles materially improve all derived markets vs v14: v20260524 is BETTER on 9 of 11 markets (1X2 −19 to −26%, AH −9 to −11%, BTTS −2%; OU 2.5 +9% worse, the same calibration drift previously seen on v20260517). v20260517 mirrors the shape (1X2 −13 to −20%, AH −6 to −11%, BTTS −1.3%, OU +13% worse). The AH gain in particular was previously invisible — production AH bots route through the same goals regressors but no held-out metric existed to compare them across versions. Future Sunday retrains report all 11 markets in the SUMMARY_JSON + email digest, so promotion decisions are no longer blind to BTTS/AH.

**Weekly-retrain feature regression (WEEKLY-RETRAIN-OU-FEATURES, 2026-05-24).** The OU 2.5 regression flagged by MARKET-EVAL-BTTS-AH (v20260517 / v20260524 both +9–13% worse than v14 on the `over_under` head) turned out not to be calibration drift — it was a missing-flag bug in the weekly retrain cron. `workers/scheduler.py:job_weekly_retrain` invoked `python -m workers.model.train --version $V` without `--include-pinnacle` or `--include-ou-market`, so every bundle produced by the Sunday cron silently dropped the 14 market-data columns that v14 was trained with (`pinnacle_implied_{home,draw,away,over25,under25}`, `ou25_bookmaker_disagreement`, `market_implied_btts_yes`, plus their `_missing` indicators). v14 was trained manually with those flags; the cron never re-included them. Fix: scheduler invocation now passes both flags, and a smoke test guards against re-omission. The `over_under` XGBoost head was the only head materially impacted (its features were market-data heavy); the `result_1x2` head's gains held up because most of its signal comes from ELO + form + tier (non-market features), so the regression on OU happened against a 1X2 backdrop of clear improvement and was easy to miss. Retroactive verification: a hand-trained `v20260524_market` bundle (same data, same date, both flags on) recovers OU log-loss from +8.5% worse than v14 to +4.4% worse while keeping the 1X2 / BTTS / AH gains. The residual 4.4% was traced to data composition: `scripts/diag_ou_data_drift.py` trained a second bundle `v14_recreate_2026_05_11` with the same code but the v14 date cutoff (`match_date <= 2026-05-11`, n=48,409); it ties v14 on every single market (within ±1.3% log-loss, OU literally at −0.0%). So the entire OU residual is the 5,400 matches added after 2026-05-11, mostly the 14 TIER-C-EXPAND leagues onboarded 2026-05-19 — those rows have different OU calibration and pull the `over_under` head slightly. Accepted on production (the net portfolio gain is positive and Platt absorbs most of the eval-level gap before the bot edge gate); v20260531 will be the next datapoint after another week of data settles.

### 3.2 ELO Rating System

Custom ELO implementation tracking every team globally:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| K-factor | 30 | Standard for football — balances responsiveness and stability |
| Home advantage | +100 points | Added to home team's rating before expected score calculation |
| Goal-diff multiplier | `(|GD| + 1)^0.5` | A 3-0 win updates ratings 2x more than a 1-0 win |
| Initial rating | 1500 | Standard baseline for all new teams |

**Expected probability:** `E_home = 1 / (1 + 10^((ELO_away - ELO_home) / 400))`

**Update rule:** `new_rating = old_rating + K * GD_mult * (actual - expected)`

ELO ratings are updated daily during settlement (21:00 UTC) after match results are confirmed.

### 3.3 Derived Signal Formulas (Group 2 refinements, 2026-05-07)

**REST-NONLINEAR:** Rest effect is non-linear — 2→3 days matters, 10→11 days is negligible. Signal: `rest_days_norm = log(rest_days + 1)`. Raw `rest_days` kept for reference.

**IMPORTANCE-GAMES-REM:** Points-urgency normalized by games remaining. `fixture_urgency = max(pts_to_relegation_gap, pts_to_title_gap) / (games_remaining × 3)`. Values >1.0 = mathematically desperate. Derived from `league_standings.played` + `2 × (total_teams - 1)` season formula.

**FORM-ELO-RESIDUAL:** `form_vs_elo_expectation = actual_ppg - expected_ppg`. Expected PPG from ELO: `3 × (1 / (1 + 10^((1500 - ELO) / 400))) + 0.27`. Strips baseline quality (already priced by market) to isolate genuine hot/cold streaks. Positive = team outperforming ELO; negative = underperforming.

**TURF-FAMILIARITY:** Away team's `away_team_turf_games_ytd` counts finished away matches on artificial turf this season. Quantifies visitor unfamiliarity — two home teams on turf = no signal; English visitor to Scandinavian turf = real edge.

### 3.4 Meta-model Feature Set (META-FEATURE-DESIGN, 2026-05-24 / updated B-ML3-BETS-MODE 2026-06-07)

This subsection documents the **B-ML3** meta-model — Stage-3 classifier that scores each emitted bet on `P(this bet beats closing line)` and gates placement. The meta-model is a downstream filter; it does NOT replace the primary 1X2/OU/BTTS/AH heads.

**Critical finding — inverted signal (2026-06-07 B-ML3-BETS-MODE):** The v21/v22/v23_xgb bundles trained on all 7K+ MFV rows had an **inverted signal** in production: high meta score → lower Pinnacle CLV (Q5 vs Q1 spread was −14.9pp). Root cause: distribution mismatch. The training set was all MFV rows (`target = pseudo_clv > 0`), but inference targets only bots' actually-fired bets — a completely different regime.

**Fix — `--bets-mode` training:** Bundle `v_20260607_bets` (production as of 2026-06-07) trains on actual bot-fired bets with **real Pinnacle closing-line CLV** as the label. Key differences:

| | Old bundles (v21–v23) | v_20260607_bets (active) |
|---|---|---|
| Training data | All MFV rows (7,716) | Fired bets only (n=305) |
| Label | `pseudo_clv > 0` (proxy) | `clv_pinnacle > median` (+7%) |
| CV AUC | 0.569–0.587 | **0.6712 ± 0.072** |
| Q5 vs Q1 CLV spread | −14.9pp (inverted) | **+18.4pp (correct)** |
| Features | 14–24 | 44 |

**Active bundle (2026-06-07): `v_20260607_bets`** — XGBoost, threshold=0.52, `META_B_ML3_ENABLED=true`.

**Goal of original B-ML3 (v21 design):** binary classifier with `target = (pseudo_clv > 0)` evaluated per (match × selection) row in `match_feature_vectors`. Training cohort filter: `match_date >= '2026-05-06'`; cohort size 9,971 rows / 5,572 with `opening_implied_home IS NOT NULL`.

**The shortlist had to satisfy three constraints, in order:**

1. **Coverage ≥ 30%** in the training window. Features below this threshold can't be learned reliably even with `_missing` indicators because the imputation would dominate the signal.
2. **Has to add information beyond the closing line.** ELO/form/position are partially redundant with the closing line itself (the bookmaker already prices them in). We keep some quality proxies but lean toward market-microstructure features the closing line doesn't fully capture.
3. **Empirical evidence of signal** when available — features that passed an AUC > 0.52 gate against pseudo_clv beat, OR have a clear validated mechanistic story.

**Final feature list (14 features):**

| # | Feature | Coverage | Why it's in |
|---|---|---|---|
| 1 | `ensemble_prob_<selection> − opening_implied_<selection>` (computed at train time) | 56% | The OPENING-LINE-BEAT proxy. Single likely-strongest feature. Captures the model's claimed edge directly. |
| 2 | `opening_implied_<selection>` | 56% | Raw market-implied prob at opening — calibration anchor and a strong proxy for "how confident does the market start at?". |
| 3 | `bookmaker_disagreement` | 46% | Spread between best and worst implied across accessible books. Higher = more inefficiency to exploit. |
| 4 | `odds_drift_home` | 56% | Post-opening movement. Sharp money pushing the line is informative independent of where it ends. |
| 5 | `steam_move` | 56% | Binary flag for fast/large drift. Coarser than #4 but more interpretable. |
| 6 | `elo_diff` | 98% | Quality differential. Even though closing line absorbs ELO, the residual matters in tier-3/4 leagues where pricing is thinner. |
| 7 | `form_ppg_home` | 65% | Recent points-per-game. Quality proxy but distinct from ELO in capturing in-season trajectory. |
| 8 | `form_ppg_away` | 66% | Same, away side. |
| 9 | `lineup_confirmed` | **100%** | **NEW (2026-05-24 NEWS-LINEUP-VALIDATE)**. Bets on matches with confirmed lineups hit +8.1% ROI vs −4.5% without (n=1,752 settled). +12pp ROI signal — the single strongest discovered feature. |
| 10 | `rest_days_home`, `rest_days_away` (2 cols) | 80% | Fatigue / rotation risk. Strong empirical signal in football. |
| 11 | `fixture_importance` | 58% | Cup vs league vs friendly weighting. |
| 12 | `league_position_home` | 61% | Table context for the home side. Captures relegation-fight / title-race intensity that's not fully in ELO. |
| 13 | `time_to_kickoff` (computed at train time, from `pick_time` vs `m.date`) | n/a (derived) | Hours before kickoff. Bets placed close to KO have different CLV characteristics. |
| 14 | `league_tier` (joined from `leagues`) | 100% | Bookmakers price T1 differently from T3/T4. Categorical with 4 levels. |

**Explicitly dropped from the initial proposal (with reason):**

| Feature | Reason |
|---|---|
| `news_impact_score` | **2026-05-24 NEWS-LINEUP-VALIDATE: AUC 0.30 vs home_win, 0.49 vs bet outcome.** Neither CI clears 0.50. Only 4.8% of matches carry non-zero values. Dropped from B-ML3 v1; revisit if news collection improves. |
| `model_disagreement` | 6.4% coverage. Too thin for reliable learning. |
| `form_momentum_home` | 0% coverage — column is empty. Latent bug in `_build_feature_row_batched`. |
| `overnight_line_move` | 0.2% coverage — capture is broken. |
| `injury_count_home/away` | 3% coverage. Filed under `INJURY-SEVERITY` for later refit when severity tagging lands. |
| `referee_cards_avg` | 4.8% coverage. Needs `BACKFILL-REFEREE-RECENT` to populate properly. |
| `weather_temp_c` / `weather_wind_kmh` / `weather_rain_mm` | 16% coverage. Defer until weather pipeline ships v2. |
| `pinnacle_line_move_home`, `pinnacle_ah_line_move`, `odds_volatility`, `sharp_consensus_home`, `importance_diff`, `venue_surface_artificial` | Not present as MFV columns. Required if 4-AI consensus list is used as-is — defer until pipeline produces them. |
| `pinnacle_implied_over25`, `ou25_bookmaker_disagreement`, `market_implied_btts_yes` | OU/BTTS-specific features. B-ML3 v1 scores 1X2-style outcomes; OU-specific meta-model (B-ML3-OU) would consume these in a follow-up. |
| ELO / form combined into a single quality score | Considered. Kept ELO and form as separate features — the meta-model can learn the interaction. Coefficient inspection post-training will reveal if both are pulling weight or one is redundant. |

**Post-training feature audit:** after the first B-ML3 fit, inspect logistic regression coefficients. Features with |coefficient| < 0.05 are candidates to drop for v2. Re-fit weekly via `META-RETRAIN` once the lifecycle wires up.

**Why not just train on every column with coverage > 30%?** Per the original META-FEATURE-DESIGN note: 12-15 features × 50 examples per feature = ~750 minimum rows for stable coefficients. We have 5,572 usable rows, so we could support up to ~110 features — but feature-bloat trades model interpretability for marginal signal. Holding to 14 leaves headroom for AH-XGBOOST features when those train into a B-ML3 v2.

### 3.5 B-ML3 Activation Validation (B-ML3-VALIDATE-ACTIVATION, 2026-05-25 / activated 2026-06-07)

**Status as of 2026-06-07: ACTIVE** — `META_B_ML3_ENABLED=true`, bundle `v_20260607_bets`, threshold 0.52.

The original June 10 gating plan was based on validating the old v21–v23 bundles via `meta_clv_score` column reads. That plan is superseded: old bundles had an inverted signal (§3.4); the new bets-mode bundle was validated by cross-validation (AUC 0.6712) and quintile analysis (Q5 vs Q1 CLV spread +18.4pp on n=314 out-of-training bets). Activation was moved forward to 2026-06-07.

**Ongoing regression guard:** Run `scripts/validate_meta_b_ml3.py` weekly. Since `meta_clv_score` in `simulated_bets` is now written from `v_20260607_bets`, scores accumulate as new bets are placed and settled. After ~3-4 weeks (≈200 settled bets), the script will show true out-of-sample quintile separation.

**Threshold choice:** 0.52 (conservative start). The training-set optimal threshold is 0.65 (fires 148/305 bets at precision=1.0 on training set — likely overfit). 0.52 filters only the clearest Q1 bets (meta score < 0.52 = bottom ~25%) while letting most of the volume through. Tighten toward 0.65 once 200+ OOS bets confirm the quintile spread holds.

**Original methodology** (still valid for weekly regression checks):
1. Cohort: settled bets with `meta_clv_score` populated from the current bundle.
2. Quintile binning: 5 bins by score, compute `mean_clv`, `hit_rate`, `roi_per_bet` per bin.
3. Signal holds if Q5 vs Q1 `mean_clv` spread stays ≥ +5pp. If it drops to < 2pp over a rolling 30d window, flip `META_B_ML3_ENABLED=false` and investigate.
4. Retrain bets-mode bundle weekly: `python3 scripts/train_b_ml3.py --bets-mode --model xgboost --version v_YYYYMMDD_bets` (as more bets accumulate real Pinnacle CLV, n grows, AUC stabilizes).

### 3.6 B-ML3 v3 — Null Result on MFV-V3 Features (2026-05-25)

After shipping the MFV-V3 signal batch on 2026-05-25 (LEAGUE-DRAW-YTD, LEAGUE-SEASON-PHASE, LINE-VELOCITY, SIG-12 xG overperf, INJURY-SEVERITY, AF-PLAYER-RATINGS), we retrained B-ML3 with all 10 new features added to `MATCH_LEVEL_FEATURES`. Bundle `v_20260525_v3_xgb` produced **CV AUC 0.5879 ± 0.0534** — virtually identical to v23_xgb's 0.587.

**Interpretation:** the new signals improve the MAIN model where they add raw outcome information (1X2 log_loss -6.5% to -7.8% in the corresponding v_20260525_signals MAIN bundle). But the meta-model can't extract additional CLV-beat signal from them because those signals partly *define* closing-line movement — they predict the same target Pinnacle's market-maker is reacting to. There's nothing left for the meta to learn after the MAIN model already consumes them.

**Action:** **keep v_20260525_v23_xgb as the active candidate** for the 2026-06-10 B-ML3-VALIDATE-ACTIVATION decision. Future meta-model improvements should target signals that the MAIN model *doesn't* consume — e.g. bet-time-specific features (recommendation order, exposure tier, bot identity, time-since-last-fire), not match-time signals.

### 3.7 LONGSHOT-GEO-AUDIT — Global Platt Overconfidence (2026-05-25)

Audit of 563 settled 1X2 bets over the last 60 days, binned by `calibrated_prob` in 5pp steps:

| Bin | n | avg predicted | actual win % | gap |
|---|---|---|---|---|
| 0.25-0.30 | 77 | 28.1% | 23.4% | -4.7pp |
| 0.30-0.35 | 100 | 30.7% | 15.0% | **-15.7pp** |
| 0.35-0.40 | 103 | 38.4% | 26.2% | **-12.1pp** |
| 0.40-0.45 | 243 | 42.5% | 36.2% | -6.2pp |
| 0.45-0.50 | 39 | 46.4% | 30.8% | **-15.6pp** |

**The 30-50% probability range is systematically overconfident by 12-16pp.** Originally hypothesized as geographically concentrated (South American / Eastern European home-advantage inflation) — per-country breakdown rejects that: no country diverges by ≥5pp from the global average in the 0.30-0.40 focus bin.

**Implication:** the bias is at the **global Platt calibration** layer, not per-country. Per-tier Platt isn't catching it. Filed `GLOBAL-PLATT-OVERCONFIDENCE` follow-up for the next post-Phase-3.5 weekly retrain — should consider:
- Re-fit Platt with stronger regularization on the 30-50% bins
- Switch Stage-2 calibrator from Platt to isotonic regression (handles non-monotonic miscalibration)
- Add an explicit 2-parameter (intercept + slope) Platt per (tier × bin) instead of the current per-tier scalar

Currently the bot edge gate compensates partially: a 5% min-edge requirement on a 30%-predicted bet implies the model needs `cal_prob > 0.30 + 0.05 = 0.35`, and the actual hit rate at predicted 0.35 is 26% — so the edge is mostly being eaten by the calibration error rather than translating to real ROI. Fixing Platt should mechanically improve real-money ROI on the 30-50% slice.

---

## 4. Model Architecture

### 4.1 Two Parallel Models

**Model A — Dixon-Coles Poisson:**

Estimates expected goals per team, then enumerates all scorelines (0-0 through 7-7) using Poisson probability mass functions:

```
exp_home = avg(home_goals_scored[-10]) * 1.08    # home advantage
exp_away = avg(away_goals_scored[-10]) * 0.92
exp_home = (exp_home + avg(away_goals_conceded[-10])) / 2   # blend with opponent
exp_away = (exp_away + avg(home_goals_conceded[-10])) / 2

For each scoreline (h, a):
    P(h, a) = Poisson(h; exp_home) * Poisson(a; exp_away) * tau(h, a)
```

**Dixon-Coles correction** adjusts the four low-scoring outcomes where the independence assumption breaks down. The rho parameter is estimated per league tier from historical scoreline frequencies (script: `scripts/fit_league_rho.py`, refreshed weekly). Default fallback: rho = -0.13 (literature standard) when fewer than 200 matches exist for a tier.

| Scoreline | Correction factor tau |
|-----------|----------------------|
| 0-0 | `1 - exp_h * exp_a * rho` |
| 1-0 | `1 + exp_a * rho` |
| 0-1 | `1 + exp_h * rho` |
| 1-1 | `1 - rho` |
| All other | 1.0 (no correction) |

This addresses the ~8% draw underestimation of independent Poisson. After correction, 1X2 probabilities are renormalised to sum to 1.0.

**Output:** P(home), P(draw), P(away), P(O/U 1.5), P(O/U 2.5), P(O/U 3.5), P(BTTS).

**Model B — XGBoost Classifier:**

Gradient boosted decision tree trained on historical match data:

| Hyperparameter | Value |
|----------------|-------|
| n_estimators | 200 |
| max_depth | 6 |
| learning_rate | 0.05 |
| subsample | 0.8 |
| colsample_bytree | 0.8 |
| objective | `multi:softprob` (3-class) |
| Calibration | Isotonic regression (5-fold CV) |
| Validation | TimeSeriesSplit (5 folds, no future data leakage) |

**Output:** P(home), P(draw), P(away), P(O/U 2.5), expected goals (home/away).

**Goal regressors (1c, 2026-05-10).** The XGBoost bundle now ships two `count:poisson` regressors (`home_goals.pkl`, `away_goals.pkl`) trained on `score_home` / `score_away` from the same MFV slice the classifiers use. Inference (`xgboost_ensemble._predict_goals`) consumes these to produce λ_home / λ_away for the Poisson side of the ensemble's score distribution. Before this change `train.py` only produced classifiers — operators had to copy `home_goals.pkl` / `away_goals.pkl` from `v9a_202425/` into every new bundle. A v10+ bundle is now self-contained.

Regressor hyperparameters mirror the classifiers (`n_estimators=200`, `max_depth=5`, `lr=0.05`, `subsample=0.8`, `colsample_bytree=0.8`) with `objective="count:poisson"` + `eval_metric="poisson-nloglik"`. CV reports per-fold RMSE and Poisson deviance.

### 4.1c How Each Market Gets Its Probability (2026-05-25)

Production routes the 6 market families through 3 distinct computational paths inside a single MAIN bundle:

| Market | Computational path | Calibration |
|---|---|---|
| **1X2** (home / draw / away) | `result_1x2.pkl` classifier head (XGBoost multi:softprob) | Stage 1 tier-shrinkage + Stage 2 Platt/isotonic |
| **OU 2.5** | `over_under.pkl` classifier head | Stage 1 tier-shrinkage + Stage 2 Platt/isotonic |
| **BTTS yes/no** | `btts.pkl` classifier head | Stage 1 tier-shrinkage + Stage 2 Platt/isotonic |
| **OU 1.5 / 3.5 / 4.5** | Derived from `home_goals.pkl` + `away_goals.pkl` Poisson regressors → Dixon-Coles joint goal matrix → integrate margin distribution above/below the line | No separate fit — λ already came from a calibrated 1X2 inversion (`_solve_lambdas_calibrated`) |
| **Asian Handicap** | Same Poisson + DC joint matrix → `_ah_model_prob()` integrates margin distribution over the handicap line, handling whole/half/quarter line push-adjustments | **AH-CAL-BYPASS** (2026-05-24): Stage 1 shrinkage SKIPPED (would double-shrink because λ already came from already-Platt-calibrated 1X2 probs). Stage 2: aggregate `asian_handicap` Platt fitted 2026-05-28 from live settled simulated_bets (n=128). Per-line keys (e.g. `asian_handicap_Home -0.5`) had <50 samples each — one aggregate key used instead. `apply_platt()` has `_MARKET_ROOTS` fallback: if specific key not found, tries startswith-match on the market root before returning raw prob. |
| **Double Chance** (1X / X2 / 12) | Direct sums of Stage-2-calibrated 1X2 probabilities | Stage 1 skipped (**AH-CAL-BYPASS** — same double-shrink risk). Stage 2: per-selection Platt fitted 2026-05-28 from live settled simulated_bets — `double_chance_1x` (n=58), `double_chance_x2` (n=105). Was urgent: uncalibrated DC had model_prob 0.791 vs actual hit rate 0.595 = 19.6pp gap. `double_chance_12` skipped (insufficient data). |

**PLATT-LIVE-STOPGAP refresh (2026-06-06).** Manual run of `scripts/fit_platt_live.py` + `scripts/fit_platt_inplay_e.py` landed 6 fresh `model_calibration` rows at 10:35 UTC:

| Market key | n | ECE before | ECE after | Notes |
|---|---:|---:|---:|---|
| `asian_handicap_away -0.5` | 50 | 20.6% | **2.9%** | First per-line AH Platt; aggregate `asian_handicap` row still exists as fallback |
| `btts_yes` | 154 | 16.1% | **1.7%** | Best calibration improvement of the batch |
| `btts_no` | 87 | 11.7% | 8.1% | Still above 5% gate — partial |
| `double_chance_1x` | 63 | 25.7% | 12.7% | Partial (small sample) |
| `double_chance_x2` | 170 | 22.7% | **~0%** | Replaces 2026-05-28 fit |
| `inplay_e_under_25` | 216 | 21.9% | 8.9% in-sample / **4.0% LOO** | LOO clears 5% gate |

Stopgap reasoning: Sunday cron (`fit_platt.py`) under `MODEL_VERSION=v20260524_market` would skip these markets for thin per-version samples; this run blends model versions to fit TODAY rather than waiting 4-8 weeks. Long-term fix tracked as AH-PLATT-WIRE secondary (Sunday-cron consolidation + per-version filter).

**INPLAY-CALIBRATED-PROB-WIRE (2026-06-06).** Discovered that ALL 898 historical in-play bets had `simulated_bets.calibrated_prob = NULL` — the in-play bet builder (`_build_inplay_bet_data` at `workers/jobs/inplay_bot.py`) never propagated `cal_model_prob` from `trigger.extra` to the dedicated column. Strategy E was computing the calibrated probability via `apply_platt()` but only storing it inside the `reasoning` JSON. Fix: 4-line propagation in `_build_inplay_bet_data`. Going forward, inplay_e bets persist `calibrated_prob` to the column for downstream analysis. Other in-play strategies still write NULL until they wire `apply_platt` per INPLAY-CALIBRATION-COMPLETE (rolling, gated on ≥100 settled bets per strategy).

**FIT-PLATT-INPLAY-EXCLUDE (2026-06-06).** With in-play bets now writing `calibrated_prob`, the next prematch Platt fit risked corruption (in-play O/U distribution differs from prematch; both share `market='o/u'`). Added `match_minute_at_pick IS NULL` filter to `scripts/fit_platt.py` OU + BTTS queries and to `scripts/threshold_check.py` CAL-PLATT counting queries. Both files now operate on prematch-only cohort and stay in lockstep (verified by FIT-PLATT-THRESHOLD-CONTRACT smoke).

So when `MODEL_VERSION=v_20260525_depth8` is set, the same 5-file bundle drives all 6 market families. Improving the goal regressors (`home_goals.pkl`, `away_goals.pkl`) automatically improves AH + OU 1.5/3.5/4.5 via the joint matrix.

**Dedicated AH classifier head (candidate, 2026-05-25).** `scripts/train_ah_xgboost.py` trains a standalone XGBoost AH classifier on `main-line` AH cohort (~3,200 settled matches). First bundle `data/models/ah_xgb/v_20260525` produced **CV AUC 0.7308 ± 0.0205**. NOT wired into production yet — would require:
1. Inference router that picks AH-XGBoost output over `_ah_model_prob()` when bundle present
2. Env gate (`AH_XGB_ENABLED=true`)
3. A/B comparison vs the Poisson-derived path on settled AH bets

Filed as future work. Production AH stays on the Poisson-derived path. The MARKET-EVAL-BTTS-AH eval (2026-05-24) showed Poisson-derived AH log-loss already improves -9 to -11% in candidate vs v14, so the dedicated head isn't urgent.

### 4.2 Ensemble

```
P(outcome) = w * Poisson_prob + (1 - w) * XGBoost_prob
```

Default blend weight w = 0.5 (equal). The weight is learned and stored in the `model_calibration` table (market key `blend_weight_1x2`) via `scripts/fit_blend_weights.py`, and loaded at pipeline startup — falls back to 0.5 if no learned value exists. Model disagreement (`|Poisson - XGBoost|`) is stored per bet as an uncertainty signal.

### 4.3 Data Tier Fallback

Not all matches have sufficient data for both models:

| Tier | Availability | Models Used | Stake Multiplier |
|------|-------------|-------------|-----------------|
| A | Full historical stats + odds (18 leagues) | Poisson + XGBoost ensemble | 100% |
| B | Results-only history (22+ leagues) | Poisson only | 50% |
| C | No history, AF supplies xG (af_goals_home/_away) | AF 1X2 + Poisson grid driven by AF xG | 20% |
| D | No history and AF has no xG | Skipped — no model can fire | Not bet on |

**TIER-C-AF-XG (2026-05-19):** Tier C was previously hardcoded to a 50/50 OU prior + league-average BTTS with `exp_home/exp_away = None` (so AH and OU 1.5/3.5 never fired). API-Football's `/predictions` endpoint actually returns per-team expected goals (`af_goals_home`, `af_goals_away`) for every match it covers — typically ~70-80% of fixtures including most non-CSV-covered leagues. The fallback now parses those xG values and feeds them into the same `_poisson_probs()` grid Tier A uses (same Dixon-Coles rho, same per-league draw inflation). 1X2 probabilities still come from AF's blended percentages (form + H2H + standings — stronger than xG alone); OU 1.5/2.5/3.5/4.5, BTTS, and AH are now model-priced. The +8% `DATA_TIER_EDGE_BUMP` for Tier C is kept unchanged so the existing safety margin still applies.

---

## 5. Calibration Pipeline

Raw model probabilities are systematically overconfident (10-15%). Two-stage calibration corrects this.

### 5.1 Stage 1 — Tier-Specific Market Shrinkage

Blend model probability toward an implied probability anchor, with the blend weight depending on how efficient the market is for that league tier:

```
shrunk = alpha * model_prob + (1 - alpha) * anchor_implied_prob
```

**Anchor:** Pinnacle-implied probability when available (fallback to market-average across the 13 tracked bookmakers). Pinnacle vig is 2-3% vs 5-8% for soft books — their implied probabilities are closer to true probabilities. This applies to all markets (1X2 home/draw/away, O/U over/under) since PIN-2 (2026-05-06).

**1X2 markets (default alphas):**

| Tier | alpha | Model weight | Market weight | Rationale |
|------|-------|-------------|---------------|-----------|
| 1 (top flight) | 0.20 | 20% | 80% | EPL/La Liga: market is very efficient |
| 2 | 0.30 | 30% | 70% | Championship level |
| 3 | 0.50 | 50% | 50% | Balanced |
| 4 (lower) | 0.65 | 65% | 35% | Market least efficient, trust model more |

**CAL-ALPHA-ODDS (implemented 2026-05-06):** For bets at odds > 3.0 (longshots), `alpha = max(alpha_tier - 0.20, 0.10)` — reducing model weight to pull calibrated probability harder toward the anchor. Live data (77 settled bets) showed the 0.30-0.40 probability bin is catastrophically miscalibrated (13% actual win rate vs 35.5% predicted), driven by longshot home bets where the model overestimates vs the market.

**Goal-line markets (BTTS, O/U) use higher alpha** — the Poisson/Dixon-Coles model is specifically designed for goal totals, so we trust it more relative to the bookmaker:

| Tier | alpha (goal-line) |
|------|------------------|
| 1 | 0.35 |
| 2 | 0.45 |
| 3 | 0.65 |
| 4 | 0.80 |

All alpha values are **learned and updatable**: the pipeline loads them from the `model_calibration` table at startup (keys `shrinkage_alpha_t{tier}_{market_type}`), falling back to the hardcoded defaults above if no learned values exist.

### 5.2 Stage 2 — Post-Hoc Calibration Correction

A learned correction applied to the shrunk probability, fitted from settled prediction/bet outcomes.

**1X2 markets (1x2_home / 1x2_draw / 1x2_away) — standard Platt sigmoid:**
```
calibrated = 1 / (1 + exp(-(a * shrunk + b)))
```
- Parameters `a` (slope) and `b` (intercept) fitted per market by minimising negative log-likelihood
- Fitted from settled `predictions` table rows (source=ensemble) with known outcomes
- Requires 100+ samples per market; graceful no-op if unavailable

**O/U markets (over_under_25_over / over_under_25_under) — 2-feature logistic (CAL-PLATT-UPGRADE, 2026-05-12):**
```
calibrated = 1 / (1 + exp(-(w0 * shrunk + w1 * log(odds) + intercept)))
```
- `w0`, `w1`, `intercept` stored as `platt_a`, `platt_c`, `platt_b` in `model_calibration`
- `platt_c IS NULL` for 1X2 markets (uses 1-feature path); non-null for O/U (2-feature path)
- Fitted from settled `simulated_bets` rows (market='O/U') using `calibrated_prob` (= shrunk_prob before first deployment) and `odds_at_pick`
- Requires 300+ samples; sklearn LogisticRegression with C=1.0 regularisation
- Script: `scripts/fit_platt.py`

The `log(odds)` feature allows the correction to learn that "model says 40% at odds 3.6" should be corrected differently than "model says 40% at odds 1.8" — the primary failure mode identified in the 2026-05-06 calibration review. 1X2 will be upgraded once ≥ 300 settled 1X2 bets are available (~2 weeks after O/U).

**Model-version segmentation (important):** Every `simulated_bets` row carries a `model_version` column. Platt/logistic fitting must be run on a single model version at a time. Mixing versions produces a blended curve that is not valid for any individual version. Filter: `WHERE model_version = 'vX'`. Minimum sample size applies per version, not in aggregate.

Current status (2026-05-28): O/U 2-feature logistic deployed. BTTS 1-feature Platt fitted 2026-05-27 (offline holdout, n=139). DC per-selection Platt and AH aggregate Platt fitted 2026-05-28 via `scripts/fit_platt_live.py` (MLE Nelder-Mead fit directly on `model_probability + result` from settled `simulated_bets` — no odds feature, simpler than the logistic path but effective as an ad-hoc stopgap). 1X2 upgrade (add log-odds feature) pending (~300 settled bets). DNB pending (insufficient data). `fit_platt_live.py` supports `--dry-run` and `--markets` flags; re-run any time after batch settlements to refresh DC/AH/BTTS params before the next weekly retrain.

### 5.3 Stage 3 — Veto Gate

An additional hard filter applied after calibration, before bet placement:

**Pinnacle disagreement veto (PIN-VETO, implemented 2026-05-06; extended PIN-4, 2026-05-12):** If `calibrated_prob − anchor > 0.12` → bet is skipped entirely. For 1X2 and O/U 2.5 markets, `anchor` is Pinnacle's vig-adjusted implied probability (PIN-3, 2026-05-06). For all other markets (BTTS, double chance, asian handicap, O/U non-2.5 lines), no Pinnacle signal is stored, so `anchor = 1/best_odds` (best-available-book implied probability) is used as a proxy (PIN-4, 2026-05-12). This prevents calibration over-correction from surfacing bets with 20–40% displayed EV where the gap vs the market is implausibly large.

**Pinnacle-required gate for OU markets (OU-PIN-REQUIRED, implemented 2026-05-10):** OU price aggregation in `_load_today_from_db` skips any `(match, market, selection)` triple where Pinnacle has no row at all — not just non-Pinnacle rows that exceed the 2× cap. Without a Pinnacle reference, a single mislabelled book row (Asian-total prices stored in the OU 1.5 slot, etc.) gets promoted by MAX-across-books and the bot bets at fake prices. Coverage on next-2-day pre-match data: Pinnacle prices ~58% of OU 1.5 / ~85% of OU 2.5 matches — bots place fewer bets in small leagues but every placement is validated against the sharpest book. Together with `OU-PINNACLE-CAP` (2× cap on non-Pinnacle when Pinnacle is present, 2026-05-10), this would have blocked all 19 voids in `bot_ou15_defensive`'s pre-guard 38-bet history.

Empirical validation on 77 settled home bets: all winning bets had gap ≤ 12.9%; losing bets averaged 14.1% gap (max 21.7%). Catches 22/34 losses at the cost of filtering 6/40 wins.

This veto addresses a structural bias: both XGBoost (`is_home` feature) and Poisson (separate home/away lambdas) encode home advantage. When blended 50/50, home advantage may stack. The market already prices it in — so the model's excess confidence shows up as a large positive gap vs Pinnacle.

Threshold 0.12 is calibrated on home bets only. Draw/away/O/U thresholds should be tuned independently once 50+ settled bets per market accumulate.

**Sharp consensus gate (CAL-SHARP-GATE, implemented 2026-05-06):** For 1X2 home bets, also skipped when `sharp_consensus_home < -0.02` (sharps collectively price home less likely than soft books).

### 5.4 Validation

Calibration quality measured by **Expected Calibration Error (ECE)**:

```
ECE = sum over bins: (bin_count / total) * |actual_win_rate - predicted_probability|
```

Using 20 equal-width bins from 0% to 100%. A perfectly calibrated model has ECE = 0. Target: ECE < 3%.

Validation script: `scripts/check_calibration.py` — produces calibration table with 5% bins, flags deviations > 5%.

---

## 6. Edge Detection & Bet Sizing

### 6.1 Edge Calculation

```
edge = calibrated_prob - implied_prob
implied_prob = 1 / decimal_odds
```

A bet is placed only when edge exceeds a tier-specific threshold:

| Tier | 1X2 Favourite | 1X2 Longshot | Over/Under |
|------|--------------|-------------|------------|
| 1 | 8% | 12% | 8% |
| 2 | 5% | 8% | 6% |
| 3 | 4% | 6% | 5% |
| 4 | 3% | 5% | 4% |

Lower tiers require less edge because the market is less efficient — even a small model advantage has a higher probability of being real.

### 6.2 Kelly Criterion Stake Sizing

```
kelly_fraction = (calibrated_prob * odds - 1) / (odds - 1)
stake = min(kelly_fraction * 0.15 * bankroll, 0.01 * bankroll)
```

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly fraction | 0.15x (1/6.7 Kelly) | Conservative — reduces variance at cost of slower growth |
| Max stake | 1.0% of bankroll | Hard cap prevents any single bet from dominating |
| Minimum stake | EUR 1.00 | Sub-EUR 1 bets are noise — not placed |

### 6.3 Stake Multipliers

Applied sequentially after Kelly calculation:

| Multiplier | Values | Purpose |
|------------|--------|---------|
| Data tier | A: 1.0, B: 0.5, C: 0.25 | Reduce exposure on less certain predictions |
| Odds movement penalty | 0.0 to 0.8 | Scale down when market moves against pick (see below) |

### 6.4 Odds Movement Filter

Tracks how odds have moved since opening:

```
drift = current_implied_prob - opening_implied_prob
```

| Drift | Action |
|-------|--------|
| > +1% (favourable) | No penalty — market confirms pick |
| -1% to -10% (adverse) | Soft penalty: `penalty = |drift| / 0.10 * 0.8` |
| < -10% (extreme adverse) | **Hard veto** — bet not placed |

This prevents betting against strong market signals (e.g. late injury news that moves the line).

---

## 7. Signal System

61 signals collected per match across 6 groups, stored in an append-only EAV table (`match_signals`).

### 7.1 Signal Groups

| Group | Signals | Source | Timing |
|-------|---------|--------|--------|
| 1. Model | Poisson, XGBoost, AF, ensemble probabilities | Pipeline | 05:30 UTC |
| 2. Market | Opening odds, bookmaker disagreement, overnight line move, odds volatility, CLV | Odds pipeline | Every 2h |
| 3. Team Quality | ELO, form PPG, form slope, goals, league position, H2H, rest days | Enrichment | 04:15 UTC |
| 4. Information | News impact, injury counts, lineup confirmation/confidence | News checker | 4x daily |
| 5. Context | Referee stats, fixture importance, importance asymmetry, league averages | Enrichment | 04:15 UTC |
| 6. Live | Score, minute, shots, xG, possession, live odds, events | Live tracker | Every 5 min |

### 7.2 Signal Timeline

```
T-24h    Fixtures published (daily 04:00 UTC)
T-16h    Enrichment: standings, H2H, injuries, referee stats, form
T-14h    Odds: first snapshot of the day
T-12h    Predictions: Poisson + XGBoost + ensemble
T-10h    Betting: edge detection, Kelly sizing, bet placement
T-6h     News: first Gemini analysis pass
T-3h     News: second pass (closer to kickoff)
T-1h     Lineups published, lineup signals updated
T-0      Kickoff → live signal collection every 30-60 seconds (Railway LivePoller)
T+FT     Settlement: results, P&L, CLV, ELO update, pseudo-CLV
T+FT+1h  Post-match: stats, events, player stats enrichment
```

---

## 8. Bot Strategies

26 paper trading bots run simultaneously: 16 pre-match bots (same ensemble prediction, different market/league filters) + 10 in-play bots (rule-based strategies using live xG or shot-proxy + Bayesian posterior, `workers/jobs/inplay_bot.py`). Pre-match bots differ in:

- **Which markets** they bet (1X2 home/draw/away, O/U 1.5/2.5/3.5, BTTS yes/no)
- **Which leagues** they target (all, lower tiers only, specific countries)
- **What edge threshold** they require (conservative: 10%, aggressive: 3%)
- **What odds range** they accept (e.g. 1.30-4.50 vs 2.50-3.00)
- **Which selections** they filter (e.g. draw-only, away-only, over-only)

### 8.1 Strategy Categories

| Category | Bots | Approach |
|----------|------|----------|
| Broad coverage | `bot_v10_all`, `bot_aggressive` | All leagues, lower thresholds |
| Lower-tier specialist | `bot_lower_1x2`, `bot_high_roi_global` | Tiers 2-4 where pricing is softest |
| Conservative | `bot_conservative` | 10%+ edge only, highest selectivity |
| Country/region | `bot_greek_turkish` | Specific regions with backtest-confirmed edge |
| Optimizer — away value | `bot_opt_away_british`, `bot_opt_away_europe` | Away wins at mid-range longshot odds (2.20-3.50) in British Isles / top-5 Europe; cross-era validated (+16-19% ROI) |
| Optimizer — home underdog | `bot_opt_home_lower` | Home underdogs at longshot odds (3.00-5.00) in T2-4 Europe; cross-era +14% ROI |
| Optimizer — O/U | `bot_opt_ou_british`, `bot_ou25_global` | Over/Under value in British lower divisions and globally |
| Market specialist — BTTS | `bot_btts_all`, `bot_btts_conservative` | Both-teams-to-score: broad (all leagues) and selective (T1-2, 7%+ edge) |
| Market specialist — O/U | `bot_ou15_defensive`, `bot_ou35_attacking` | O/U 1.5 (defensive leagues) and O/U 3.5 (high-scoring leagues) |
| Draw specialist | `bot_draw_specialist` | Draws underbet in T2-4; odds range 2.80-4.50 |

### 8.2 Bot Timing Cohorts

All 16 bots are assigned to one of three timing windows as an A/B test to identify the optimal bet placement time:

| Cohort | UTC window | Bots | Rationale |
|--------|-----------|------|-----------|
| morning | 06:00 | `bot_v10_all`, `bot_lower_1x2`, `bot_aggressive`, `bot_ou25_global`, `bot_opt_ou_british` | Early odds capture before sharp money moves lines |
| midday | 11:00 | `bot_conservative`, `bot_greek_turkish`, `bot_high_roi_global`, `bot_ou15_defensive`, `bot_ou35_attacking`, `bot_draw_specialist` | Post-injury-news refresh, standings updated |
| pre_ko | 15:00–19:00 | `bot_opt_away_british`, `bot_opt_away_europe`, `bot_opt_home_lower`, `bot_btts_all`, `bot_btts_conservative` | Confirmed lineups, most information available |

CLV and ROI are tracked per cohort to determine which window produces the best edge.

### 8.3 In-Play Strategies

In-play bots use the same ensemble model for pre-match context (xG, O/U probability, win probabilities) combined with live snapshot data. Two categories:

**xG / shot-proxy strategies (stats-gated):** A, D, E, G, H, Q — require live xG or shots data; limited to ~22% of leagues where AF provides live statistics. Fall back to shot-proxy (sot×0.10 + off×0.03) with higher edge floors.

**Score-state strategies (no stats needed):** B, C, I, J, L, M, N, O, P — work from live 1x2 odds + score + prematch model probabilities; available for all leagues with live 1x2 coverage (~22% of snapshots).

| Strategy | Entry condition | Bet | Model |
|----------|----------------|-----|-------|
| O — Underdog Hold | Prematch underdog (win prob < 35%) leads 1-0 at min 25-55, live odds ≥ 2.80 | Win for leading underdog | Bivariate Poisson from 1-0: P(hold lead) > market implied |
| P — Post-Equalizer | Team equalises to 1-1 at min 30-75 (within 4min window), live win odds ≥ 2.20 | Win for equalising team | Bivariate Poisson from 1-1: market anchors on draw, depressing win prices |

Both use `_poisson_win_prob(lambda_a, lambda_b, lead_a)` — a double-Poisson convolution over remaining goals — to estimate win probability and compare to implied odds. Edge threshold: O ≥ 4%, P ≥ 3%.

### 8.3.5 Cross-Match Accumulator Bots (COMBO-RESTRUCTURE 2026-05-22)

Four additional bots (`bot_acca_value`, `bot_acca_proven`, `bot_combo_system`, `bot_combo_proven_system`) build cross-match accumulators from the same day's predictions. All four share a common post-COMBO-RESTRUCTURE config:

- **N=5 fixed** (min_legs=5, max_legs=5) — 3yr backtest showed N=3/4 are marginally -EV; N=5 captures the compound edge
- **OU15/over required in pool** — the entire accumulator edge concentrates in days where OU15/over qualifies (73.3% leg win rate vs 44.3% without it; without OU15 all structures are approx -EV)
- **≥8% per-leg edge** — tighter than single-bet bots; reduces leg count but raises hit rate
- Source: `_scan_todays_candidates()` queries `predictions` + `odds_snapshots` directly (not dependent on other bots' `simulated_bets`)

| Bot | Structure | Tickets | Leg pool |
|-----|-----------|---------|----------|
| `bot_acca_value` | `straight` (5-fold) | 1 | All acca-eligible markets (btts, ou25, ou35, ou15) |
| `bot_acca_proven` | `straight` (5-fold) | 1 | Proven markets only (ou25, ou35, btts) + OU15/over |
| `bot_combo_system` | `fours_up` | 6 | All acca-eligible markets |
| `bot_combo_proven_system` | `fours_up` | 6 | Proven markets only + OU15/over |

**fours_up structure:** generates all sub-combos of size 4..N. For N=5: five 4-folds + one 5-fold = 6 tickets, each staked at `total_stake/6`. Tolerates one losing leg (the five 4-folds that don't include the loser still pay). Settlement dispatches to `_settle_system_fours_up()` in `settlement.py`.

**Backtest basis** (`scripts/backtest_system_variants.py`, 3yr data, normalised €1/day stake):
- N=5 + OU15 + straight: +1199% ROI (9 qualifying days)
- N=5 + OU15 + fours_up: +791% ROI (risk/reward tradeoff: lower variance than straight)
- N=5 without OU15: straight −0.9%, fours_up −19.4% (no edge without OU15)

**Admin recording:** manually placed combo bets can be logged via the `/admin/place-bets` table — click "Record" on any combo row to open `RecordComboModal`, enter actual Coolbet odds + stake. Stores to `real_bets` with `combo_legs JSONB` + `system_type TEXT` and settles automatically once all legs finish.

### 8.3.6 Per-market real-money placement thresholds (PER-MARKET-EDGE-V2 2026-06-06)

Bots create `simulated_bets` for every pick that clears the bot's own edge floor (typically 3-10%, varies by bot). The Coolbet auto-placer then decides which of those simulated bets become real-money bets in `real_bets`. As of 2026-06-06 this gate is **per-market**, not a single global threshold.

| Market | Real-money floor | Rationale |
|---|---|---|
| 1X2 | 10% | Backtest of 1,207 settled 1X2 simulated_bets shows ROI +2.9% at ≥5% threshold vs **+14.1%** at ≥10% — edge is strongly predictive |
| O/U | 3% | Profitable at every floor (≥3%: +3.1% ROI); higher gates lose volume without improving expectation |
| Asian Handicap | 5% | Edge non-monotonic; flat ROI ~5% across thresholds — moderate floor preserves volume |
| BTTS | 10% | Backtest negative at ≥3-7% (−5% ROI); needs ≥10% to recover (+2.7% on n=63 — thin, monitored) |
| Double Chance | Retired | Losing at every threshold tested (≥3%: −10.8%, ≥10%: −17.2%). Paper-tracking continues; real-money placement stopped |
| Combo / DNB | 10% / 5% | Combos gate like 1X2; DNB shares structure with single-outcome 1X2 |

Source: `scripts/edge_threshold_backtest.py` (3,086 settled simulated_bets, 2026-05-01 → 2026-06-06). Implemented as `_MIN_EDGE_BY_MARKET` in `workers/automation/coolbet_placer.py`; frontend badge mirror in `src/lib/engine-data.ts` (`COOLBET_AUTO_MIN_EDGE_BY_MARKET`). The per-market floor is applied in two places:

1. **Pick-time gate**: after the SQL prefilter (global 3% floor), `_min_edge_for(market)` drops candidates below the market's specific floor.
2. **Live-edge gate**: at placement time, the additive edge `cal_prob − 1 / live_odds` is recomputed against the same per-market floor (replaces the legacy `_MIN_REMAINING_EDGE` single value).

The change is operator-side only. `/value-bets` (subscriber-facing) continues to surface every bot pick at every edge — the per-market thresholds gate only the real-money placement decision. A port to `/value-bets` (probably as an opt-in Pro-tier filter) is deferred pending 4-6 weeks of post-change data. `/admin/real-bets` shows Era v1 (pre 2026-06-06T17:00:00Z) vs Era v2 (post) so the lift is measurable in isolation.

### 8.4 Backtest Foundation

Bot strategies are validated against a 354,518-match dataset (275 leagues, 2005-2015):

- **Lower tiers outperform:** Tier 4 ROI is -7.0% vs Tier 1 at -12.4% (5% better)
- **Consistently profitable leagues:** Singapore S.League (+27.5%), Scotland League Two (+12.3%), Austria Erste Liga (+5.5%)
- **12 of 22 consistently profitable leagues are tier 3-4** (55% of winners)
- **Geographic edge:** Less commercially-covered regions (Singapore, small South American leagues) show more opportunity

---

## 9. Performance Measurement

### 9.1 Primary Metric: Closing Line Value (CLV)

CLV is the industry standard for evaluating betting models independently of short-term variance:

```
CLV (soft-book) = (odds_at_pick / soft_closing_odds) - 1
CLV (Pinnacle)  = (odds_at_pick / pinnacle_closing_odds) - 1   ← primary metric (PIN-5)
```

- **Positive CLV** means we consistently got better odds than the closing line — the market moved in our direction after our bet. This is the strongest evidence of a real edge.
- **Negative CLV** means the market moved against us — our model may be seeing phantom edges.
- **Pinnacle CLV is the stronger signal.** Pinnacle closes at the sharpest line; beating it means we found edge before the most informed market participants did.

CLV is meaningful even when P&L is negative (variance can dominate in small samples).

Both `clv` (soft-book) and `clv_pinnacle` (Pinnacle-specific) are stored on `simulated_bets` and tracked per cohort.

### 9.2 Secondary Metrics

| Metric | What it measures |
|--------|-----------------|
| ECE (Expected Calibration Error) | How well predicted probabilities match actual frequencies |
| Hit rate by confidence bin | Model's ability to rank match certainty |
| ROI by league tier | Where the model adds value vs where the market is too efficient |
| Model disagreement (Poisson vs XGBoost) | Uncertainty indicator — high disagreement = less confident bet |

### 9.3 Track Record Transparency

The public track record page displays:
- Average CLV across all settled bets
- Value bets identified vs total matches analysed
- League coverage
- Model accuracy by confidence level
- All data publicly verifiable (every settled prediction logged with timestamp)

---

## 10. Alignment System (Experimental)

An external signal filter currently in **log-only mode** — it records alignment scores on every bet but does not yet influence staking or filtering.

### 10.1 Six Dimensions

| # | Dimension | Signal | +1 | -1 |
|---|-----------|--------|----|----|
| 1 | Odds Movement | Market drift direction | Shortened (agrees) | Lengthened (disagrees) |
| 2 | News | Gemini impact analysis | Positive news for selection | Key injury/suspension |
| 3 | Lineup | Confirmation status | Confirmed | Not yet confirmed |
| 4 | Situational | Rest + home advantage in lower tiers | Favourable | Unfavourable |
| 5 | Sharp consensus | Sharp vs soft bookmaker pricing gap (`sharp_consensus_home` signal) | Sharp books agree with pick | Sharp books disagree |
| 6 | Pinnacle anchor | Pinnacle implied probability vs model probability | Pinnacle doesn't strongly disagree (gap > −3%) | Pinnacle strongly disagrees (gap < −8%) |

Note: dimensions 5 and 6 only fire for 1X2 home picks. O/U and draw picks use dimensions 1-4.

### 10.2 Activation Criteria

Alignment will be activated (move from log-only to staking modifier) after:
- 300+ settled bets with alignment data
- Statistical evidence that HIGH alignment correlates with higher ROI
- Tracking live since 2026-04-27 (~10 bets/day) — estimated activation: late May 2026

---

## 11. Known Limitations

1. **Top-tier market efficiency:** Tiers 1-2 show negative ROI historically. The model adds little beyond what bookmakers already price in for EPL, La Liga, etc.

2. **Feature overlap with market:** Form, ELO, and xG proxy are publicly available signals. Bookmakers use similar (or better) versions. The model's edge comes from lower-tier inefficiency, not from superior features.

3. **Sample size:** Live trading began 2026-04-27 (~3 days). Statistical significance requires 500+ settled bets. Current CLV and ROI numbers are directional, not conclusive.

4. **No proprietary data:** All data comes from public APIs. No private injury feeds, no in-house scouting, no pitch-level telemetry.

5. **Dixon-Coles rho needs more data:** The parameter is now estimated per league tier (not global static) from historical scoreline frequencies. However tier-level grouping is a coarse approximation — a per-league rho would be more precise but requires ~500+ matches per league to be stable. Additionally, Dixon-Coles only corrects the four low-scoring outcomes (0-0, 1-0, 0-1, 1-1) — higher-scoring draws (2-2, 3-3) remain underestimated due to the positive correlation between team scoring that results from game-state effects. Draw inflation factor (×1.08, pending CAL-DRAW-INFLATE) addresses this.

6. **Isotonic calibration is trained once:** The XGBoost model's isotonic calibration is fitted during training on historical data. It doesn't adapt to live prediction drift (Platt scaling addresses this partially).

7. **Conditional miscalibration at high odds (observed 2026-05-06):** Live data (77 settled bets) shows the model's calibration fails specifically on longshot bets (predicted 30-40%, odds > 3.0). In the 0.30-0.40 probability bin: 23 bets, 35.5% predicted, 13% actual win rate. The primary driver is likely double-counted home advantage (Poisson encodes it via separate home/away lambdas; XGBoost has it as a feature), amplified by edge detection selecting exactly the bets where the model most overestimates. The Pinnacle veto (gap > 0.12) was deployed immediately; the remaining fixes (odds-conditional alpha, sharp consensus gate) are tracked as CAL-ALPHA-ODDS and CAL-SHARP-GATE.

---

## 12. Improvement Roadmap

| Phase | Items | Status |
|-------|-------|--------|
| **Foundation** | ELO, form, Poisson, XGBoost ensemble, Kelly sizing, calibration | Done |
| **Calibration** | Tier-specific shrinkage, Platt (1X2), 2-feature logistic (O/U), weekly recalibration | Done (O/U upgraded 2026-05-12; 1X2 upgrade pending) |
| **Risk controls** | Odds movement penalty/veto, data tier multipliers, max stake cap | Done |
| **Signal infrastructure** | 61 signals, append-only store, wide ML training table, pseudo-CLV | Done |
| **Next: Meta-model** | Second-stage model predicting bet profitability (target = CLV) | Needs 3,000+ matches |
| **Next: Alignment activation** | Use external signal filter to modify stakes | Needs 300+ settled bets |
| **Sharp bookmaker features** | Pinnacle disagreement veto (all markets), Pinnacle implied signals (all markets), Pinnacle line movement, Pinnacle-anchored CLV, sharp/soft consensus | PIN-VETO + PIN-1..5 + P5.1 done |
| **Calibration improvements (live data)** | Odds-conditional alpha, sharp consensus gate, Pinnacle anchor, Pinnacle-anchored CLV, O/U 2-feature logistic | CAL-ALPHA-ODDS / CAL-SHARP-GATE / CAL-PIN-SHRINK / CAL-PLATT-UPGRADE (O/U) done; CAL-DRAW-INFLATE / CAL-PLATT-UPGRADE (1X2, pending ~300 bets) pending |
| **Dynamic blend weights** | Weekly recalculation of Poisson/XGBoost blend per tier | Done — `scripts/fit_blend_weights.py`, Sunday refit |
| **Next: Historical backfill** | 43K+ matches with stats + events from API-Football (no historical odds available) | In progress — automated cron |
| **Next: XGBoost retrain on backfill** | Retrain on live AF data — `workers/model/train.py` is ready (28 features, `load_training_data()` loads from DB). Run: `python3 workers/model/train.py` | After ~3,000 completed rows in `match_feature_vectors` (~June 2026) |
| **In-play Phase 1: Rule-based paper trading (P3.4)** | 8 strategies (A, A2, B, C, C_home, D, E, F). xG source: real AF stats for top leagues (~UCL/Libertadores/Sudamericana); shot proxy `sot*0.10 + off_target*0.03` for all others. Proxy bets use higher edge floors (+1.5–2pp). League gate (MIN=3 xG matches) only enforced for real-xG mode. All bets log `xg_source: live\|shot_proxy` for backtest segmentation. Safety: staleness <60s, score re-check, red card skip. Fixed 1-unit stake. Runs inside LivePoller every 30s. | **Live since 2026-05-06** — proxy fallback added 2026-05-07 |
| **Next: In-play Phase 2 ML** | LightGBM Poisson regression predicting `lambda_home/away_remaining` from live match state. Replaces rule-based triggers with model probability. Quarter Kelly + time-decay staking. | Needs 500+ live-tracked matches + 200 settled paper bets (~June 2026) |

---

## 13. Code Reference

| Component | File | Key function/class |
|-----------|------|--------------------|
| Poisson model | `workers/jobs/daily_pipeline_v2.py` | `_poisson_probs()` |
| XGBoost ensemble | `workers/model/xgboost_ensemble.py` | `ensemble_prediction()` |
| Calibration (shrinkage + Platt) | `workers/model/improvements.py` | `calibrate_prob()`, `apply_platt()` |
| Kelly sizing | `workers/model/improvements.py` | `compute_kelly()`, `compute_stake()` |
| Odds movement | `workers/model/improvements.py` | `compute_odds_movement()` |
| Alignment | `workers/model/improvements.py` | `compute_alignment()` |
| Platt fitting | `scripts/fit_platt.py` | `fit_and_store()` |
| DC rho fitting | `scripts/fit_league_rho.py` | `run()` |
| Calibration validation | `scripts/check_calibration.py` | `check_calibration()` |
| XGBoost training (Kaggle v9a) | `scripts/retrain_xgboost.py` | Legacy — trains on Kaggle CSV |
| AF model training | `workers/model/train.py` | `train_all()` / `load_training_data()` — run directly |
| ELO updates | `workers/jobs/settlement.py` | ELO update section |
| CLV computation | `workers/jobs/settlement.py` | Settlement + pseudo-CLV |
| Signal collection | `workers/jobs/daily_pipeline_v2.py` | Signal writing throughout pipeline |
| Bot strategies | `workers/jobs/daily_pipeline_v2.py` | `BOTS_CONFIG` dict (lines 67-340) |
| Feature vectors ETL | `workers/api_clients/supabase_client.py` | `build_match_feature_vectors()` |

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| **CLV** | Closing Line Value — ratio of odds at time of pick to odds at kickoff, minus 1. Positive = beat the closing line. |
| **ECE** | Expected Calibration Error — weighted average of |predicted - actual| across probability bins. Lower is better. |
| **Kelly criterion** | Optimal bet sizing formula: `f = (p*b - 1) / (b - 1)` where p = probability, b = decimal odds. We use 0.15x fractional Kelly. |
| **Implied probability** | `1 / decimal_odds` — the probability a bookmaker's odds represent (before margin). |
| **Edge** | `model_probability - implied_probability`. Positive = model thinks outcome is more likely than the market. |
| **Platt scaling** | Post-hoc sigmoid calibration: `1/(1+exp(-(a*p+b)))`. Used for 1X2 markets. O/U uses a 2-feature logistic `sigmoid(w0*p + w1*log(odds) + b)` to handle odds-conditional miscalibration. |
| **Dixon-Coles** | Correction to bivariate Poisson for low-scoring outcomes (0-0, 1-0, 0-1, 1-1) where independence assumption fails. |
| **Data tier** | Classification of prediction quality: A (full data, ensemble), B (results-only, Poisson), C (no history, AF xG drives Poisson grid + AF 1X2), D (no history and no AF xG — skipped). |
| **Pseudo-CLV** | CLV computed for ALL matches (not just bets) by comparing opening and closing implied probabilities. Used as ML training target. |

---

## Appendix B: Backtest Summary (354K matches)

Dataset: Beat the Bookie, 275 leagues, 2005-2015.

| Metric | Value |
|--------|-------|
| Total matches | 354,518 |
| Total bets (edge > threshold) | 187,895 |
| Overall ROI | -12.4% |
| Hit rate | 26.9% (breakeven: 29.2%) |
| Tier 4 ROI | -7.0% (best tier) |
| Best league | Singapore S.League: +27.5% (316 bets, 5/5 seasons positive) |
| Consistently profitable leagues | 22 (12 of 22 are tier 3-4) |

Key insight: the model shows edge primarily in **lower-tier, less commercially-covered leagues** where bookmaker pricing efficiency is lowest.
