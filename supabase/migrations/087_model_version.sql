-- Stage 3a — A/B harness: add model_version to predictions + simulated_bets
--
-- Without this column, "did the new model help?" can only be answered by
-- comparing dates (pre-deploy vs post-deploy) — contaminated by league mix,
-- weather, fixture density. With it, scripts/compare_models.py can pick out
-- overlapping settled matches per market and produce a clean log_loss/Brier/
-- CLV diff between any two versions.
--
-- All existing rows are backfilled to 'v9a_202425' (the only model bundle
-- in production at the time of this migration). Going forward, the betting
-- pipeline reads MODEL_VERSION from env at startup and stamps every new row.
--
-- Shadow mode (Stage 3c): a second model can run in parallel by setting
-- MODEL_VERSION_SHADOW=<version>. Its predictions land in `predictions` with
-- the shadow tag but do NOT drive `simulated_bets` — the bot still uses
-- the primary MODEL_VERSION.

ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS model_version TEXT;

ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS model_version TEXT;

UPDATE predictions
   SET model_version = 'v9a_202425'
 WHERE model_version IS NULL;

UPDATE simulated_bets
   SET model_version = 'v9a_202425'
 WHERE model_version IS NULL;

-- Index for compare-script queries: find all settled bets for a given version
-- since some date. Without this the comparison script does a seq scan on
-- ~50K rows per version per call — bearable but wasteful.
CREATE INDEX IF NOT EXISTS idx_predictions_version_created
  ON predictions (model_version, created_at);

CREATE INDEX IF NOT EXISTS idx_simulated_bets_version_picktime
  ON simulated_bets (model_version, pick_time);

COMMENT ON COLUMN predictions.model_version IS
  'Active MODEL_VERSION env value when this prediction was written. '
  'Used by scripts/compare_models.py for A/B evaluation.';

COMMENT ON COLUMN simulated_bets.model_version IS
  'MODEL_VERSION at bet placement. Same row never changes versions — promotion '
  'creates new bets tagged with the new version. Old bets retain their tag '
  'for historical comparison.';
