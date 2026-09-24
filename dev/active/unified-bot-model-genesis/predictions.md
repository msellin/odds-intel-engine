# Genesis: the PREDICTIONS side (#139 UNIFIED-BOT-MODEL, Phase 5a)

Parent row: `PRIORITY_QUEUE.md` #139 UNIFIED-BOT-MODEL, Phase 5 ("unify only where two tables
hold the same thing"). This is **archaeology**: why each table exists, how it changed, what it
guarantees. A separate agent inventories today's readers and writers, so this doc does not.
Research only, written 2026-09-24. Every number was measured read-only against the VPS DB on
that date unless it says otherwise.

---

## 0. Scope: what counts as a "prediction table"

I searched `information_schema` for predict / model / calibrat / feature / elo / signal / fair /
shadow / inplay / national / wc names, and checked every hit's columns. Classification:

| class | tables |
|---|---|
| **Model outputs** (probabilities we produced) | `predictions`, `pick_triggers`, `candidate_funnel` (probabilities of candidates), `published_picks` (argmax log), `live_match_snapshots.model_ou25_prob` (a column, not a table) |
| **Model inputs / features** | `match_feature_vectors`, `match_signals`, `team_elo_daily`, `team_form_cache`, `team_elo_international`, `team_roster_strength` |
| **Model parameters / registry** | `model_versions`, `model_calibration` |
| **Model evaluation** | `model_evaluations`, `feature_importance` |
| **Third-party probabilities** | `book_fair_probs` (Sportradar via Tonybet), `wc_market_consensus`; AF's prediction lives in `predictions(source='af')` **and** `matches.af_prediction` |
| **Simulation output** | `wc_monte_carlo_results` |
| **Backups (frozen on purpose)** | `mfv_pre_elo_fix_backup`, `mfv_pre_elo_fix_backup_early`, `model_calibration_ou_domain_mismatch_backup` |
| **Not model data despite the name** | `wc_group_predictions` (users' bracket picks), `match_previews` (Gemini prose), `inplay_bot_stats` (tried/fired counters) |
| **Gone** | `prediction_snapshots` (never had a DROP migration), all `cs2_*` (migration 286), `live_xg_snapshots` (migration 401) |
| **Esports/tennis leftovers** | `lol_upcoming_matches` (14 rows, last 2026-06-08), `tennis_fixtures_today` (last 2026-07-07), `tennis_value_bets` (38 rows), `lol_bets` (0 rows) |

There is **no separate AF-predictions table** and **no separate shadow-model table**. Both were
designed into `predictions` as columns (`source`, `model_version`). This matters for §3.

---

## 1. Table by table

### 1.1 `predictions`, the central table (1,104,962 rows, 417 MB, last write 2026-09-24 15:05 = live)

**Birth.** `001_initial_schema.sql`, commit `69ab9176` 2026-04-27, "Live paper trading pipeline +
Supabase integration". Original shape: one row per (match, market) holding `model_probability`,
`implied_probability`, `edge_percent`, `confidence`, `reasoning`. It had **no uniqueness at all**.
It was the model's output for a match, the thing the value-bet UI read.

**Evolution**

| date | migration / commit | change | why (quoted or paraphrased from the source) |
|---|---|---|---|
| 2026-04-28 | 010 / `00b1bdba` "Tier 0 ML foundation" | `source text NOT NULL DEFAULT 'ensemble'`; `UNIQUE (match_id, market, source)` | S1 "multi-signal architecture": store poisson, xgboost, af and ensemble as separate rows, so components can be compared and the meta-model can train on them. |
| 2026-05-01 | 031 / `d4d4f18f` RAIL-14 | `implied_probability`, `edge_percent` become nullable | The psycopg2 migration. Predictions without odds (AF, poisson components) had nothing to put there. |
| 2026-05-05 | 047 / 054 | match_id rewrites and deletes for Kambi duplicates | Fixture dedup. Nothing about predictions themselves. |
| 2026-05-10 | 087 / `51f0d8eb` "wire training to production + A/B harness" | `model_version TEXT`, backfilled to `'v9a_202425'`; index `(model_version, created_at)` | "Without this column, 'did the new model help?' can only be answered by comparing dates … contaminated by league mix." Planned in `dev/archive/unified-ml-pipeline-plan.md` Stage 3a-3c. |
| 2026-05-24 | 127 / `af86d1da` MODEL-LIFECYCLE-PHASES | unique key becomes `(match_id, market, source, model_version)` | SHADOW-PREDICTIONS-UNIQUE: "shadow inference with a different model_version would either overwrite production or fail." This is what lets candidate bundles write beside production. |
| 2026-05-28 | 139 | RLS | Hygiene. |

**What lives in it today.** Six `source` values with different meanings (ANALYSIS_GOTCHAS §2):
`ensemble` is what the bots bet. `xgboost` is the raw component, **and also where shadow and
candidate versions land**. `poisson` is the raw component plus all `ah_*` markets. `af` is
API-Football's prediction, not ours. `national_team`, `national_team_v1`,
`national_team_v1_blended` and `national_team_v1_lineup` come from the NT predictor.
`model_version` includes the non-bundle sentinels `poisson_backfill` (225,016 rows backdated to
2023), `national_team_v1*` and `v14_recreate_2026_05_11`.

**Two write paths, only one of which works.**
- `bulk_store_predictions()` (`supabase_client.py:1172`) does
  `ON CONFLICT (match_id, market, source, model_version) DO UPDATE SET model_probability, confidence, reasoning, implied_probability, edge_percent`.
  It **does not update `created_at`**, and the table has **no `updated_at`**.
- `store_prediction()` (`:1123`) still says `ON CONFLICT (match_id, market, source)`. That
  constraint was dropped in migration 127, so Postgres would reject every call with "no unique
  constraint matching". Nothing calls it. **Dead code, and broken.**

**Point-in-time consequence.** `run_morning` writes predictions at the end of every run
(`daily_pipeline_v2.py:4175`, unconditional). Each **shadow cohort** runs that function again
several times a day (`scheduler._shadow_run` → `run_morning(shadow_mode=True)`), so a
(match, market, source, version) row is **overwritten in place all day**. Its `created_at` keeps
the first write. Measured:
- **2,696 matches** since 2026-07-01 carry more than one ensemble `1x2_home` model_version.
- Among `shadow_bets` 1x2 since 2026-08-01, **1,206 of 3,656** (match, selection, version) groups
  were bet at **more than one distinct probability** during the day. `predictions` keeps one.
- Joining `simulated_bets` 1x2 (since 2026-08-01, n=265) to the matching `predictions` row
  (same match, market, `source='ensemble'`, version): only **126 join**. Of those, **67 equal**
  the bet's `model_probability` and **59 differ**. `shadow_bets`: 8,158 of 22,982 join, and 3,018
  of those differ.

So **`predictions` is a latest-value cache per (match, market, source, version). It is not a
history, and it is not the probability any bet was made on.** The analysis guard used across
scripts, `p.created_at <= m.date` (smoke `COVERAGE-EXPANSION-PROBE`, `backtest_pre_match_bots.py:158`),
proves only that the row was **first inserted** before kickoff, not that its **current value**
existed then. Measured, 37 ensemble rows since 2026-09-01 have `created_at > kickoff`. How many
were *updated* after kickoff cannot be measured, because nothing records it.

**Backfill with synthetic timestamps.** `scripts/predict_historical_matches.py`
(`00bc2a5b`, 2026-05-18) wrote the 225,016 `poisson_backfill` rows and then **set
`created_at = kickoff − 1h`** "so the backtest (which filters p.created_at < m.date for lookahead
safety) actually picks them up". It justifies this by saying they "were generated using only
pre-kickoff team form". That claim predates ELO-FORM-LEAK (found 2026-09-14,
`team_form_cache` included the match day) and **has not been re-verified**. Those rows'
`created_at` is a label, not a measurement.

**AF predictions are stored four times.**
1. `matches.af_prediction` jsonb (migration 008, 2026-04-28, "fetched once in morning pipeline").
   46,162 matches, live.
2. `predictions(source='af')` with parsed probabilities. Because the unique key includes
   `model_version`, which defaults to the active `MODEL_VERSION` env even though AF is
   third-party, **the same AF data is re-stamped under every production bundle's tag**
   (v9a_202425, v14, v20260524_market, …, v20260712). 74 matches carry more than one AF version
   for `1x2_home`, and 63 of those are identical. The tag carries no meaning for `af` rows.
3. `simulated_bets.af_home_prob/af_draw_prob/af_away_prob/af_agrees` (migration 008): **0 rows
   populated**. These columns are dead.
4. `match_feature_vectors.af_pred_prob_home`: 45,372 rows, live.

**Market vocabulary is its own namespace**, and that was decided on purpose:
`dev/archive/market-vocab-canonical-plan.md` says predictions' `1x2_home/draw/away` vocabulary
"is a SEPARATE namespace — DO NOT migrate". It also varies **inside** the table: club rows use
`over25` / `under25`, NT rows use `over_2_5` / `under_2_5` (still being written in the last 7 days).

### 1.2 `prediction_snapshots`, gone (the original point-in-time design)

**Birth.** `004_prediction_audit_trail.sql`, `3900d452` 2026-04-27, "track model confidence
across info stages". Keyed on **`bet_id → simulated_bets`**, one row per stage (`stats_only`,
`post_ai`, `pre_kickoff`, `closing`). Purpose: "measuring value-add of each data source (stats
vs AI vs lineups)". This was the project's **first explicit answer to "what did the model say
at pick time"**, and it hung off the bet rather than off the match.

**Death.** There is **no DROP migration anywhere in the repo**. The table is **absent** from the
live DB. It is also **absent from the 128-table Supabase dump of 2026-07-13**
(`~/backups/supabase-2026-07-13/oddsintel-public.dump`; its DDL header lists `predictions`,
`model_*`, `match_*` but not this). So it was dropped by hand on Supabase some time between
04-27 and 07-13. The engine still calls `store_prediction_snapshot()` from
`daily_pipeline_v2.py` (stage `stats_only`) and `news_checker.py` (`post_ai`, `pre_kickoff`).
Every call raises "relation does not exist", and the callers swallow it
(`except Exception: pass  # non-critical`). **Silent dead writer.**

### 1.3 `match_feature_vectors` (MFV) (176,693 rows, 122 MB, live; last build 2026-09-24 09:27)

**Birth.** `010_multi_signal_architecture.sql`, `00b1bdba` 2026-04-28. "One row per finished
match. Rebuilt nightly by settlement pipeline ETL. This is the actual ML training table — not
the EAV match_signals directly." PK = `match_id`. It holds features and **outcome labels in the
same row**.

**Evolution (columns):** 011 fixture_importance/referee/injury counts (04-28) · 012 standings,
h2h, rest days (04-28) · 019 market_implied_* (04-29) · 058 re-adds 16 missing columns (05-07) ·
093 Pinnacle O/U + btts market features (05-11, OU-MARKET-FEATURES) · 097 weather (05-11) · 128
`*_at_t6h` microstructure (05-24, MFV-B-ML3-V2) · 132/133 v3 signals such as player rating, injury
severity, league CLV efficiency, xg overperformance (05-25) · 179/183 `pinnacle_drift_*` (06-04;
**deliberately excluded from training**, DRIFT-FEATURE-NOT-A-TRAINING-FEATURE, because it needs
the closing price) · 378 half-time SUM-shaped features (09-23, #084, "the first SUM-shaped
features this project has had").

**Second role.** `build_match_feature_vectors_live()` (MFV-LIVE-BUILD) builds the **same row
pre-kickoff**, so XGBoost inference reads it via `_build_row_from_mfv`. The nightly builder then
**upserts over it** after the match. So MFV is both the inference input **and** the training row,
one row per match, and the pre-KO version is not kept.

**Point-in-time / leakage guards, in the order they were added**

| date | guard | what it fixed |
|---|---|---|
| 2026-06-04 → 09-11 | pinnacle_drift excluded from the production retrain | closing price is not available at inference |
| 2026-09-14 | ELO read `date < match_date` (was `<=`) and form bound fixed (ELO-FORM-LEAK, `61cd38b`) | `update_elo_ratings()` stamps **post-match** ELO with the run date, so `elo_diff` scored AUC 0.7396 against a de-vigged market's 0.7270, which is "impossible for a pre-match feature" (MODEL_WHITEPAPER, "ELO and form were leaked into training until 2026-09-14"). |
| 2026-09-14 | Backups 339 and 341 taken **before** the rebuild | "REVERT" and "MEASUREMENT — Task #3's retrain has to be compared against the model that exists today". |
| 2026-09-14 | 340 + `coalesce_columns` (MFV-UPSERT-NON-DESTRUCTIVE) | The rebuild NULLed `goals_for_avg_*` on 59.7% of rows because it re-read a **pruned** `match_signals`: "REBUILDING A DERIVED TABLE FROM A PRUNED SOURCE IS LOSSY". |
| 2026-09-14 | `scripts/leakage_canary.py` + smoke `LEAKAGE-CANARY` | any non-market feature beating the market-derived ceiling fails CI (ANALYSIS_GOTCHAS "run the canary"). |
| 2026-09-21 | odds read bounded to `o.timestamp <= m.date` (MODEL-TRAINING-DEBT) | 80.3% of matches' "closing proxy" came **after** kickoff, 22% after full time. |

**Guards that are still missing (found in this pass):**
- **`built_at` is insert time, not build time.** The upsert never includes `built_at`, and it
  has `DEFAULT now()` only on insert. Of 15,763 finished matches from the last 30 days, **15,617
  have `built_at` before kickoff** while **15,684 carry outcome labels**. So `built_at < kickoff`
  says nothing about whether the stored features were computed pre-KO. `scripts/train_b_ml3.py:274`
  computes a time-to-kickoff from it.
- **The signals read has no kickoff bound** (`supabase_client.py:1708`, `ORDER BY captured_at DESC`,
  latest value wins). The odds read got that bound on 09-21 and signals did not. For finished
  matches in the last 14 days, **6,696** signals were captured after kickoff, led by
  `league_clv_efficiency` (3,942), `team_avg_player_rating_home/away` (~2,000) and
  `injury_severity_score_*`. Whether `team_avg_player_rating` written after the match includes
  that match's own ratings **is not verified**. Treat it as a candidate leak.
- **The predictions read has no `model_version` filter** (`:1532`). The last row iterated wins,
  across 2,696 multi-version matches. This only matters for the analysis columns
  `ensemble_prob_home`, `poisson_prob_home`, `xgboost_prob_home`, `af_pred_prob_home` and
  `model_disagreement`, which are **not** in any live bundle's `feature_cols` (checked for
  v20260712, v20260830, v20260903_cut0820, v20260920). They are read by meta and analysis
  scripts (`train_b_ml3.py`, `own_signal_residual_test.py`, `discover_strategies.py`, …).

**Backups.** `mfv_pre_elo_fix_backup` (49,395 rows, rows from 2026-05-01 onward, last built_at
09-13) and `mfv_pre_elo_fix_backup_early` (41,451 pre-May rows). ANALYSIS_GOTCHAS says keep them:
they are "the only baseline against which a retrained model can be compared". **Never pool them
with live MFV** ("`elo_diff` means two different things depending on when the row was written").

### 1.4 `match_signals` (4,365,759 rows, 1.1 GB, live)

**Birth.** Migration 010 (2026-04-28): an "append-only store. Same signal can have many rows
(captured at different times). ML training uses value closest to kickoff." It is the EAV source
MFV pivots from.

**Evolution.** It grew to 49.3M rows (**30x duplication**: every run rewrote every signal) →
SIGNALS-STORE-ON-CHANGE (`14edc1c`, 2026-09-03) made writers skip unchanged values →
`prune_match_signals.py --pass never-read` and then `--pass duplicates` (SIGNALS-DEDUPE-BACKLOG,
done 2026-09-07) kept **the latest row per distinct value** (it originally kept the earliest; that
was corrected after it changed the latest-read value for 27,795 pairs). VACUUM FULL took it from
13 GB to 742 MB.

**Point-in-time consequence.** "Append-only, as-of any time" is **no longer true for history
before 2026-09-07**. A value observed at 08:00 and re-seen until 18:00 now survives only as its
18:00 copy. An as-of-10:00 read of pre-09-07 history returns an older value or nothing. The
pruner's docstring says openly that "only the first-seen timestamp of a repeated value is lost,
which no reader consumes".

### 1.5 `model_calibration` (1,808 rows, live; last fit 2026-09-23 23:37)

**Birth.** `024_model_calibration.sql`, `cf88a9af` 2026-04-30 (PLATT + PLATT-AUTO). "Weekly
Platt scaling parameters per market. Pipeline reads latest row per market." It is append-only
history, and readers take the latest row per `market`.

**Evolution.** `platt_c` was added for the O/U 2-feature logistic (CAL-PLATT-UPGRADE, 2026-05-12),
and `ll_model` / `ll_market` later. It **became a generic parameter store** under MOD-2
(`70aa2538`, 2026-05-05): `blend_weight_1x2[_tN]`, `shrinkage_alpha_t{1-4}_{1x2|goalline}` and
`dc_rho_tier_{1-5}` all sit in `market` as pseudo-market keys. 29 keys today, alongside the real
Platt markets.

**The domain-mismatch incident (335, `9929721f`, 2026-09-13).** The O/U curve had been fitted on
raw `predictions.model_probability` but was **applied to `shrunk`** (≈90% Pinnacle). "Fitted in one
domain, applied in another." Its output range was [0.30, 0.67], and it inflated published O/U
edges by about 8pp for 09-03 → 09-13. Those rows were moved to
`model_calibration_ou_domain_mismatch_backup` (12 rows, frozen 09-03). O/U now runs stage-1
shrinkage only.

**Missing guard.** The table has **no `model_version` column**, although MODEL_WHITEPAPER §5.2
states "Platt/logistic fitting must be run on a single model version at a time. Mixing versions
produces a blended curve that is not valid for any individual version." The fit scripts *can*
filter by version (`fit_platt.py:68`) but never record which one, and readers take the latest
per market regardless. The whitepaper's clean-retrain section shows the cost: "the production
calibrator is wrong for this model … fitted against leak-trained output". The trigger engine
worked around it by stamping the calibrator revision into its *own* model_version (`+selcal1`,
`pick_triggers.py:72`, TRIGGER-CALIBRATOR-REVISION 2026-09-11).

### 1.6 `model_versions` (51 rows, live; last 2026-09-23)

**Birth.** `090_model_versions.sql`, `ac730ce8` 2026-05-10 (ML-BUNDLE-STORAGE). The "metadata
index" for bundles in the Supabase Storage `models` bucket: training window, `feature_cols`,
`cv_metrics`, `promoted_at` / `demoted_at`. Service-role only.

**What it does not do.** It is **not** the source of truth for what is live. That is the
`MODEL_VERSION` env (`xgboost_ensemble.py:46`, `_active_model_version()` defaults to `'v14'`)
plus `SHADOW_MODEL_VERSION`. The registry's lifecycle columns have drifted. **Five versions have
`promoted_at` set and `demoted_at` NULL** (v20260712, v20260719, v20260823, v20260830,
v20260903_cut0820). Ledger and prediction tags that are not bundles are absent: `poisson_backfill`,
`national_team_v1*`, and in `shadow_bets` 3,563 NULL tags plus `*+selcal1`,
`pinnacle_shin_devig+selcal1`, `corners_paper_devig_v1`, `team_total_paper_devig_v*` and
`fh_1x2_paper_devig_v*`. There is **no FK** from any `model_version` column to this table.

### 1.7 `model_evaluations` (603 rows, live; last 2026-09-23)

**Birth.** `001_initial_schema.sql`. Wired by `2fc702f0` 2026-04-27 (P1.4): "Settlement pipeline
aggregates settled bets into model_evaluations by date/market." The name suggests model
evaluation, but it is a **daily roll-up of settled `simulated_bets`** (hits, ROI, avg CLV) by
market. It is also overloaded as a **post-mortem store**: `market='post_mortem'` rows carry
Gemini loss-classification JSON in `notes`. The market vocabulary is mixed (`1x2`, `1X2`, `O/U`,
`over_under_25`, `combo`). Everything in it can be derived from the ledger except the post-mortem
text.

### 1.8 `feature_importance` (1,270 rows, **dead**, single run 2026-05-05 07:45)

**Birth.** `040_feature_importance.sql`, `70aa2538` 2026-05-05 (P3.5): "Stores correlation
between match signals and outcomes, computed weekly. Used to show which signals drive results in
each league." It ran once, and that single run predates the ELO leak fix, so the correlations
include leaked `elo_*`.

### 1.9 `team_elo_daily` (268,710 rows, live; last date 2026-09-23) and `team_form_cache` (234,299 rows, live)

**Birth.** `005_data_quality_tables.sql`, `2fc702f0` 2026-04-27: ELO "enabling trajectory
analysis and fast lookup during live prediction", and form cache "Replaces on-demand computation
in features.py". Both are keyed `(team_id, date)`.

**The semantics that caused the biggest leak in the project.** A row dated D is **post-match-D**
(settlement stamps it with the run date after processing D's results). Readers must use
`date < match_date`. That was fixed in the MFV builder on 2026-09-14, and the whitepaper requires
it. Form has the mirror problem: `compute_team_form_from_db(tid, today)` bounded on
`< today T23:59:59` and so included today's fixture.

### 1.10 National-team predictor: `team_elo_international`, `team_roster_strength`, `wc_market_consensus`, `wc_monte_carlo_results`

**Births.** All from WC-2026 work. 164 (`67bc5c30`, 2026-06-02) created `team_elo_international`:
"National-team ELO needs different math (different K per competition tier, neutral venues …) so
we keep it in a separate table to avoid polluting club ELO trajectories". 176/177 (`1d57a39c`,
06-04) created `team_roster_strength` (club-ELO of the starting XI plus squad value) and
`wc_market_consensus`. The latter was built because the model "produced 'Brazil 22% Morocco 50%'
… while every public market source has Brazil at 55-69%". 184 (06-04) created
`wc_monte_carlo_results`.

**State.** `team_elo_international` was **last written 2026-06-04** (a one-shot script), but
`predictions(source='national_team_v1')` is **still written daily** (last 2026-09-24 04:01) and
`shadow_bets` carries `national_team_v1+selcal1`. So a live predictor is reading an ELO frozen
3.5 months ago. `team_roster_strength` was last written 06-05, `wc_market_consensus` 06-26 and
`wc_monte_carlo_results` 07-19. These last three are **dead** (WC ended). `wc_group_predictions`
is user bracket data, not model output. Two source spellings (`national_team` 616 rows, one day,
06-02; then `national_team_v1`) cover the same markets. Only 115 of 616 overlapping pairs agree,
so they are two runs, not duplicates.

### 1.11 `pick_triggers` (1,971 rows, live; rolling window)

**Birth.** `319_pick_triggers.sql`, `a0a870d8` 2026-09-09 (BOOK-AGNOSTIC-EDGE-ENGINE Stage A):
"the model's CALIBRATED probability and the price band at which ANY book's offer becomes a bet …
a standing limit order". Unique `(match, market, selection, strategy)`. `cal_prob` is "fixed at
prediction time". Rows are **deleted 1 day after kickoff** (`pick_triggers.py:337`), so it is a
working set, not history. It also carries **sharp-anchor strategies** (`sharp_1x2`, `sharp_ou25`,
`sharp_1x2_tight`, with `model_version='pinnacle_shin_devig+selcal1'`), which are not model
predictions at all: they are de-vigged Pinnacle. The history of what fired is in `shadow_bets`.

### 1.12 `candidate_funnel` (1,559 rows, live since 2026-09-23)

**Birth.** `384_candidate_funnel.sql`, `07f1a7da` 2026-09-23 (#082): "keep the rejected
population … no question of the form 'would a lower floor … have helped?' could ever be
answered". One row per (day, source, bot, match, market, selection), updated in place with the
**latest** decision. It stores price and probability **and never an edge**
(EDGE-IS-DERIVED-NOT-STORED). `fair_source` mixes `model_cal`, `pinnacle_shin` and
`consensus:N`. It has 90-day retention.

### 1.13 `book_fair_probs` (20,210 rows, live since 2026-09-23)

**Birth.** `383_book_fair_probs.sql`, `29df6c1f` 2026-09-23 (#101 Tonybet): the supplier's
(Sportradar) margin-free probability. "LATEST VALUE, NOT A HISTORY … Because sweeps stop at
kickoff, the surviving value IS the fair close." It explicitly warns: "It is the SUPPLIER's line,
not a sharp market. Test it like any anchor before any gate or pick reads it."

### 1.14 `published_picks` (50,050 rows, live; last 2026-09-24 06:45)

**Birth.** `185_published_picks.sql`, `1aeb5425` 2026-06-05 (GROWTH-ACCURACY-PICKS-LOG):
"Append-only, kickoff-timestamped log of model picks … `picked_at` column is the credibility
anchor". `is_backfilled` separates live rows from rows "reconstructed from predictions × matches",
a distinction it calls "credibility-load-bearing". This is a **ledger that snapshots
`model_probability` and `model_version`**, and it is the reason `predictions` itself never needed
to be immutable for the accuracy page.

### 1.15 `live_match_snapshots.model_xg_*` / `model_ou25_prob`: dead columns in a live table

Added with the Tier A/B/C pipeline (`c4a4182d` 2026-04-27). `model_ou25_prob` is a copy of the
pre-match prediction (`live_tracker.py:568`): **125 rows ever, last 2026-04-30**. `model_xg_*`
was **never written**. The table itself is live and kept (#087 re-check, migration 401 comments).

---

## 2. Relationship to the bet ledgers: snapshot, not reference

**Finding.** Every ledger stores **its own copy** of the probability. None references a
`predictions.id` (there is no FK and no id column for it anywhere):

| ledger | probability snapshot columns | features-at-pick |
|---|---|---|
| `simulated_bets` | `model_probability` (raw), `calibrated_prob`, `edge_percent`, `kelly_fraction`, `model_version`, `model_disagreement`, `af_*` (dead) | `dimension_scores`, `alignment_*`, `news_impact_score`, `lineup_confirmed`, `odds_at_open`, `odds_drift`, `meta_clv_score` |
| `shadow_bets` | `model_probability`, `calibrated_prob`, `edge_percent`, `kelly_fraction`, `model_version` (incl. `+selcal1`), `decision_quote_age_min` | none |
| `picks_forward_test` | `p_sharp`, `edge`, `anchor_odds`, `anchor_overround`, `anchor_quoted_at`, `rule_version` | anchor-only; no model |
| `published_picks` | `model_probability`, `model_version`, `picked_at` | none |
| `real_bets` | `edge_pct_taken`, `captured_odds` / `actual_odds`; probability only via `simulated_bet_id` / `shadow_bet_id` | none |

**Why a snapshot and not a reference (verified, not assumed):**
1. The design intent was point-in-time from day one. `prediction_snapshots` (04-27) hung off
   `bet_id` with a unique `(bet_id, stage)`. Migration 087's comment on
   `simulated_bets.model_version`: "Same row never changes versions — promotion creates new bets
   tagged with the new version. Old bets retain their tag."
2. **A reference could not work**, because `predictions` is overwritten in place (§1.1): 59 of 126
   joinable `simulated_bets` and 3,018 of 8,158 joinable `shadow_bets` already disagree with
   their prediction row. A third of shadow (match, selection, version) groups were bet at more
   than one probability in a day.
3. The trigger engine made it explicit: `pick_triggers.cal_prob` is "fixed at prediction time",
   and the fill copies it into `shadow_bets`.

---

## 3. Same information vs look-alikes

**Genuinely the same information (candidates for unification):**

| pair | evidence | note |
|---|---|---|
| AF prediction in `matches.af_prediction` ⇄ `predictions(source='af')` ⇄ `mfv.af_pred_prob_home` ⇄ `simulated_bets.af_*` | same upstream call (`fetch_predictions`, once a day since P-PRED-1, 2026-05-10) | The sb columns are 0 rows, so dead. The `af` rows are needlessly re-stamped per model_version (§1.1). One source (the jsonb, or one un-versioned `af` row) would do. |
| `model_evaluations` (non-post-mortem rows) ⇄ aggregate over `simulated_bets` | P1.4 commit: "aggregates settled bets" | A view over the future `picks`. The `post_mortem` rows are a different thing (LLM text). |
| `published_picks` ⇄ a filter over `predictions` × `matches` | the backfilled rows were literally reconstructed that way | **But** live rows are an immutable timestamped claim and `predictions` is not. Same content, different guarantee, so do not merge. |
| `live_match_snapshots.model_ou25_prob` ⇄ `predictions` over25 | copied value | Dead columns. |

**Look alike but differ (keep separate):**

| pair | why they differ |
|---|---|
| `predictions` vs `pick_triggers` | Different **stage**: raw/ensemble model output vs **calibrated** probability plus a trigger window per *strategy*. Different lifecycle: cache vs 1-day working set. `pick_triggers` also holds non-model sharp anchors. |
| `predictions` vs `candidate_funnel` | Funnel is per **bot** and per day, stores calibrated plus raw plus threshold plus rejecting step, and carries sharp and consensus fair sources. It is decision telemetry, not model output. |
| `predictions` vs `book_fair_probs` | Third-party supplier fair price, per bookmaker, including handicap lines. Different source and grain (book dimension). |
| `predictions(source='xgboost')` vs `(source='ensemble')` | Same match and market, different quantity. ANALYSIS_GOTCHAS §2: "Comparing an ensemble number against an xgboost number compares different things." Shadow versions live only under `xgboost`. |
| `predictions` club vs `national_team_*` | Different model, different ELO table, different market vocabulary (`over_2_5` vs `over25`). |
| `team_elo_daily` vs `team_elo_international` | Deliberately split in migration 164 ("different math … avoid polluting club ELO trajectories"). |
| `match_feature_vectors` vs `match_signals` | Wide, one row per match, features plus labels (training and inference) vs EAV time series (source). MFV is derived, but **cannot be rebuilt losslessly** from the pruned signals (migration 340). |
| MFV vs `mfv_pre_elo_fix_backup*` | Identical schema, **different truth**: the backups are the leaked-era rows. Must never be pooled. |
| `model_calibration` Platt rows vs its `blend_weight_*` / `shrinkage_alpha_*` / `dc_rho_*` rows | Same table, different kinds of parameter. Already "unified" too far. |

---

## 4. Live vs dead (last write, 2026-09-24)

| table | last write | status |
|---|---|---|
| predictions | 2026-09-24 15:05 | LIVE |
| match_feature_vectors | 2026-09-24 09:27 (via the live builder; `built_at` is insert-only) | LIVE |
| match_signals | 2026-09-24 15:05 | LIVE |
| model_calibration | 2026-09-23 23:37 | LIVE |
| model_versions | 2026-09-23 | LIVE |
| model_evaluations | 2026-09-23 | LIVE |
| team_elo_daily / team_form_cache | 2026-09-23 | LIVE |
| pick_triggers | 2026-09-24 15:05 | LIVE (rolling) |
| candidate_funnel | 2026-09-24 15:05 | LIVE (new 09-23) |
| book_fair_probs | 2026-09-24 15:01 | LIVE (new 09-23) |
| published_picks | 2026-09-24 06:45 | LIVE |
| team_elo_international | 2026-06-04 | FROZEN, but read by a live predictor |
| team_roster_strength | 2026-06-05 | DEAD |
| wc_market_consensus | 2026-06-26 | DEAD |
| wc_monte_carlo_results | 2026-07-19 | DEAD |
| wc_group_predictions | 2026-06-10 | DEAD (user data, not model) |
| feature_importance | 2026-05-05 (single run) | DEAD |
| mfv_pre_elo_fix_backup(_early) | 2026-09-13 / 2026-05-10 | FROZEN ON PURPOSE |
| model_calibration_ou_domain_mismatch_backup | 2026-09-03 | FROZEN ON PURPOSE |
| live_match_snapshots.model_* | 2026-04-30 / never | DEAD COLUMNS |
| simulated_bets.af_* | never | DEAD COLUMNS |
| prediction_snapshots | table absent | DEAD WRITER (swallowed errors) |
| lol_upcoming_matches / tennis_fixtures_today / tennis_value_bets / lol_bets | 06-08 / 07-07 / — / empty | DEAD (esports and tennis retired) |

---

## 5. Consolidated invariants

- **Invariant P1**: `predictions` holds at most one row per (match, market, source, model_version),
  and its value is the **latest** write. `created_at` is the **first** write. (Migration 127;
  `bulk_store_predictions` DO UPDATE omits `created_at`; no `updated_at` column.)
- **Invariant P2**: Candidate/shadow model versions coexist with production only because
  `model_version` is in the unique key. (Migration 127, "would either overwrite production or fail")
- **Invariant P3**: Shadow and candidate versions are written under `source='xgboost'`, not
  `'ensemble'`. (ANALYSIS_GOTCHAS §1)
- **Invariant P4**: `predictions.market` is a separate vocabulary (`1x2_home`, `over25`, `ah_home_-1.00`)
  and was deliberately not canonicalised. (dev/archive/market-vocab-canonical-plan.md)
- **Invariant P5**: `af` rows are third-party. Their `model_version` is the active production
  tag at write time and means nothing. (`_active_model_version()`; the AF data is identical across tags)
- **Invariant P6**: `poisson_backfill` rows have a **synthetic** `created_at = kickoff − 1h`.
  (`predict_historical_matches.py:313`)
- **Invariant L1**: A bet's probability is **the snapshot on the bet row**. It is never re-derived
  from `predictions`. (Migration 087 comment; measured divergence in §2)
- **Invariant L2**: A bet row's `model_version` never changes after insert. (Migration 087 comment)
- **Invariant L3**: `model_version` strings on ledgers may carry a calibrator suffix (`+selcal1`)
  and non-bundle sentinels. They are not FK-able to `model_versions`. (`pick_triggers.py:57-72`)
- **Invariant F1**: A `team_elo_daily` row dated D is **post-match D**. A feature for a match on
  D must read `date < D`. (ELO-FORM-LEAK; supabase_client.py ELO read comment)
- **Invariant F2**: MFV pre-match inputs must be strictly pre-kickoff. Enforced for ELO, form and
  odds. **Not enforced for signals** (§1.3).
- **Invariant F3**: MFV is one row per match, overwritten. The pre-KO inference row and the
  post-match training row are the **same row**, so the exact features used at inference are not
  retained. (`build_match_feature_vectors_live` + nightly upsert)
- **Invariant F4**: MFV upserts must not NULL-out signal-sourced columns (`coalesce_columns=MFV_SIGNAL_SOURCED_COLUMNS`),
  because the source is pruned and rebuilding is lossy. (Migration 340)
- **Invariant F5**: Leaked-era MFV rows live only in the backups and must never be pooled with
  rebuilt rows. (ANALYSIS_GOTCHAS "MFV coverage changed on 2026-09-14")
- **Invariant F6**: `pinnacle_drift_*` is never a training feature. (MODEL_WHITEPAPER §3.1b)
- **Invariant F7**: Leakage canary: no non-market feature may out-correlate the de-vigged market.
  (smoke `LEAKAGE-CANARY`)
- **Invariant S1**: `match_signals` keeps the latest row per distinct value. First-seen times
  before 2026-09-07 are lost. (prune_match_signals.py docstring)
- **Invariant C1**: `model_calibration` readers take the latest row per `market` key. Platt must
  be fitted in the domain it is applied to (`shrunk` for O/U). (Migrations 024, 335)
- **Invariant C2 (documented, NOT enforced)**: Calibration is valid only for the model version
  it was fitted on. (MODEL_WHITEPAPER §5.2; there is no column to enforce it)
- **Invariant T1**: `pick_triggers.cal_prob` is fixed when the window is computed. Rows expire
  kickoff + 1 day. (Migration 319; `pick_triggers.py:337`)
- **Invariant T2**: Edges are derived on read, never stored, in `candidate_funnel`. (Migration 384)
- **Invariant B1**: `book_fair_probs` is the latest value, and the survivor is the fair close.
  It is not a sharp anchor until tested. (Migration 383)
- **Invariant PP1**: `published_picks` live rows are immutable, timestamped claims, and backfilled
  rows must stay labelled. (Migration 185)

---

## 6. Things a naive merge would break

1. **Merging `predictions` into `picks`, or making picks reference `predictions`**, would replace
   point-in-time probabilities with latest-overwritten ones on roughly 47% (sim) and 37%
   (shadow) of joinable 1x2 rows. That silently rewrites every CLV/ROI-by-probability analysis
   and every calibration fit that reads the ledger.
2. **Collapsing the `source` dimension** (for example "one probability per match/market") would
   lose the shadow A/B harness, because shadow versions are only under `xgboost`, and it would mix
   AF's third-party numbers with ours.
3. **Dropping `model_version` from the unique key** (for example to dedupe the repeated AF rows)
   would make every shadow run overwrite production again. That is the exact bug migration 127
   fixed.
4. **Trusting `created_at` or `built_at` as "value was known at"** during a backfill or
   reconciliation. Both are insert times on overwritten rows, and `poisson_backfill.created_at`
   is fabricated. A new unified table must carry **`computed_at` updated on every write**, or be
   append-only.
5. **Canonicalising `predictions.market`** into the ledger vocabulary would break every reader
   keyed on `1x2_home` / `over25` / `ah_*`. The vocabulary plan explicitly excluded it. Note also
   the NT `over_2_5` variant.
6. **Merging the MFV backups into MFV** ("they are the same schema") would reintroduce the leaked
   `elo_diff`, the one feature the canary exists to stop.
7. **Rebuilding MFV from `match_signals`** as part of a migration is lossy (migration 340, −59.7%
   on goals_for_avg), and signal history before 09-07 has lost first-seen times.
8. **Merging `team_elo_international` into `team_elo_daily`** would pollute club ELO trajectories
   with national-team K and neutral-venue math (migration 164's stated reason).
9. **Treating `pick_triggers` or `candidate_funnel` as prediction history.** Both are overwritten
   or pruned working sets. Their history is in `shadow_bets`.
10. **Pointing ledger `model_version` at `model_versions` with an FK** would fail on
    `+selcal1`, `pinnacle_shin_devig+selcal1`, `*_paper_devig_v*`, `national_team_v1*` and 3,563
    NULLs in `shadow_bets`.
11. **Using `model_versions.promoted_at` to decide which rows were "production"**: five versions
    are promoted and never demoted. The truth was the env var at write time, which only the
    row's own `model_version` records.
12. **Moving calibration into a per-version table without choosing a version for existing rows.**
    Current rows have no version, so the backfill would have to infer it, and that is guesswork.

---

## 7. Recommendation per table

| table | verdict | evidence |
|---|---|---|
| `predictions` | **KEEP SEPARATE** from `picks`. Do not unify. Fix within it: add `computed_at` (updated on each write), or make shadow-cohort writes append rather than overwrite, **before** anything reads it as history. Stop re-stamping `af` rows per model_version. | §1.1, §2, §6.1–6.4 |
| `prediction_snapshots` | **DEAD WRITER. Remove the calls** (`daily_pipeline_v2.py` stats_only; `news_checker.py` post_ai / pre_kickoff) and `store_prediction_snapshot()`. Its intent is fulfilled by the snapshot columns on the unified `picks` row. | absent from DB and from the 07-13 dump; errors swallowed |
| `store_prediction()` (function) | **DROP** (dead and broken ON CONFLICT target). | no callers; the constraint was dropped in 127 |
| `match_feature_vectors` | **KEEP SEPARATE.** If Phase 5 wants features-at-pick, add them to `picks` (as today's `dimension_scores`) or add a pre-KO MFV snapshot. Do not merge. Add a kickoff bound to the signals read and a `model_version` filter to the predictions read. | §1.3 |
| `mfv_pre_elo_fix_backup`, `_early` | **KEEP (frozen)** until the clean retrain comparison (#2/#3 lineage) is closed. Then archive to the dump and drop. | ANALYSIS_GOTCHAS: "the only baseline" |
| `match_signals` | **KEEP SEPARATE** (it is the source, not a prediction). | §1.4 |
| `model_calibration` | **KEEP, but split its roles**: Platt curves need a `model_version` (and domain) column. `blend_weight_*` / `shrinkage_alpha_*` / `dc_rho_*` could move to a params table. Not a unification target with predictions. | §1.5, C2 |
| `model_calibration_ou_domain_mismatch_backup` | **DROP-AS-DEAD** once `OU-CALIBRATOR-REFIT-ON-SHRUNK` closes (12 rows, only an incident record). | migration 335 |
| `model_versions` | **KEEP** as the bundle registry. Reconcile `promoted_at` / `demoted_at` (5 open promotions) or stop treating it as lifecycle truth. Do **not** FK ledgers to it. | §1.6 |
| `model_evaluations` | **REPLACE WITH A VIEW** over the unified `picks` for the roll-up rows. Move `post_mortem` JSON to its own home (or keep only those rows). | §1.7 |
| `feature_importance` | **DROP-AS-DEAD** (one run, 2026-05-05, leaked-era correlations). | §1.8 |
| `team_elo_daily`, `team_form_cache` | **KEEP SEPARATE** (inputs). | F1 |
| `team_elo_international` | **KEEP**, but flag that it has been frozen since 06-04 while `national_team_v1` still predicts and shadow-bets daily. Either refresh it or retire the NT predictor. | §1.10 |
| `team_roster_strength`, `wc_market_consensus`, `wc_monte_carlo_results` | **DROP-AS-DEAD** (WC over; last writes 06-05 / 06-26 / 07-19). Check web readers first (`team_roster_strength` has one web file reference). | §1.10 |
| `wc_group_predictions` | Out of scope (user data). Retire with the WC product. | — |
| `pick_triggers` | **KEEP SEPARATE** (standing-order working set; history is in the ledger). | §1.11 |
| `candidate_funnel` | **KEEP SEPARATE** (decision telemetry of rejected candidates; different grain). It could key to a future `picks.id` for the accepted rows. | §1.12 |
| `book_fair_probs` | **KEEP SEPARATE** (third-party, per-book). | §1.13 |
| `published_picks` | **CANDIDATE TO FOLD into `picks`** as an arm/bot with `immutable=true`, **only** if `is_backfilled` and `picked_at` survive as columns. Otherwise keep. | §1.14, PP1 |
| `simulated_bets.af_*`, `live_match_snapshots.model_*` | **DROP COLUMNS** (0 rows / dead since 04-30). | §1.1, §1.15 |
| AF storage (4 copies) | **UNIFY to one**: `matches.af_prediction` plus one un-versioned `predictions(source='af')` row. Drop the sb columns. | §3 |
| esports and tennis leftovers | **DROP-AS-DEAD** (retired products). Belongs in the #087 dead-data sweep. | §0 |

**Bottom line for Phase 5.** On the predictions side there is **no second table holding the same
model output as `predictions`**. The only true duplication is AF (4 copies) and derivable
roll-ups (`model_evaluations`). The unified `picks` table must **carry its own probability
snapshot** (`model_probability`, `calibrated_prob`, `model_version` with calibrator suffix,
`computed_at`), because `predictions` cannot serve as history.

---

## 8. Defects found in passing (read-only task, so **not filed**; the parent should put them under #139 or file them)

1. `store_prediction_snapshot()` writes to a table that does not exist, and the errors are
   swallowed (§1.2).
2. `store_prediction()` has a dead, broken `ON CONFLICT` target (§1.1).
3. The `predictions` overwrite leaves `created_at` stale, so the `created_at <= kickoff` guard is
   weaker than the smoke test implies (§1.1).
4. The MFV signals read has no kickoff bound. 6,696 post-KO signals in 14 days, possible leak via
   `team_avg_player_rating_*` (§1.3).
5. The MFV predictions read has no `model_version` filter (§1.3; analysis columns only).
6. MFV `built_at` is never updated on upsert (§1.3).
7. `model_calibration` has no `model_version` although the whitepaper requires per-version fits (§1.5).
8. `model_versions`: 5 promoted versions never demoted (§1.6).
9. `team_elo_international` frozen since 06-04 while the NT predictor is live (§1.10).
10. `picks_forward_test` has **no immutability trigger** yet (the Phase 5 design wants one; no
    triggers on the table today).
