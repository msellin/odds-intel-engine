-- ML-BUNDLE-STORAGE — registry table for trained model bundles.
--
-- Bundles live as binaries in Supabase Storage (`models` bucket, prefix=
-- '<version>/'). This table is the metadata index: when each version was
-- trained, the data window it saw, its CV metrics, when it was promoted
-- to production, and when superseded. Operators read this table to decide
-- which version to run; xgboost_ensemble._load_models() reads it
-- indirectly via the storage layer when MODEL_VERSION env var changes.

CREATE TABLE IF NOT EXISTS model_versions (
    version                TEXT PRIMARY KEY,
    trained_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    training_window_start  DATE,
    training_window_end    DATE,
    n_training_rows        INTEGER,
    feature_cols           JSONB,                                 -- list of column names
    cv_metrics             JSONB,                                 -- {1x2: {log_loss, acc}, over_25: {...}, ...}
    storage_bucket         TEXT NOT NULL DEFAULT 'models',
    storage_prefix         TEXT NOT NULL,                         -- e.g. 'v12_post0e/'
    promoted_at            TIMESTAMPTZ,                           -- set when this version becomes primary
    demoted_at             TIMESTAMPTZ,                           -- set when superseded
    notes                  TEXT,                                  -- free-form: "Pinnacle features added", "post Stage 0e"
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_versions_promoted
    ON model_versions (promoted_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_model_versions_trained
    ON model_versions (trained_at DESC);

ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY;

-- Service role only — model registry is operator data, not public.
DROP POLICY IF EXISTS model_versions_service_only ON model_versions;
CREATE POLICY model_versions_service_only ON model_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
