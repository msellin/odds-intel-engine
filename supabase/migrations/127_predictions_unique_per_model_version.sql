-- SHADOW-PREDICTIONS-UNIQUE (2026-05-24): allow multiple model versions to
-- coexist for the same (match, market, source). Required for Phase B shadow
-- inference: candidate models run alongside production and write predictions
-- with their own model_version tag, so compare_models.py finally has
-- overlapping data to diff.
--
-- Before: UNIQUE (match_id, market, source) — only ONE row per triple, so
--   shadow inference with a different model_version would either overwrite
--   production or fail.
-- After:  UNIQUE (match_id, market, source, model_version) — production and
--   shadow rows can coexist; ON CONFLICT clauses key on the 4-tuple.
--
-- Idempotent: only drops the constraint if present, only adds the new one
-- if absent. Safe to re-run.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_prediction_match_market_source'
          AND conrelid = 'predictions'::regclass
    ) THEN
        ALTER TABLE predictions DROP CONSTRAINT uq_prediction_match_market_source;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_prediction_match_market_source_version'
          AND conrelid = 'predictions'::regclass
    ) THEN
        ALTER TABLE predictions
        ADD CONSTRAINT uq_prediction_match_market_source_version
        UNIQUE (match_id, market, source, model_version);
    END IF;
END $$;
