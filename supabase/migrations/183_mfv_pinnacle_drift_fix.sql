-- DRIFT-FEATURE fix-forward (2026-06-04) — migration 179 was recorded as
-- applied in schema_migrations but the columns were not created. Root cause:
-- the Supabase migration parser concatenated the ALTER TABLE with the
-- subsequent COMMENT ON COLUMN statements without preserving semicolons,
-- producing invalid SQL that failed silently in CI while still being recorded.
-- This migration re-runs the column adds with one statement per file element
-- to dodge the parser bug.

ALTER TABLE match_feature_vectors ADD COLUMN IF NOT EXISTS pinnacle_drift_home FLOAT;

ALTER TABLE match_feature_vectors ADD COLUMN IF NOT EXISTS pinnacle_drift_draw FLOAT;

ALTER TABLE match_feature_vectors ADD COLUMN IF NOT EXISTS pinnacle_drift_away FLOAT;
