-- Widen cs2_predictions UNIQUE to include model_version.
-- Original (bo3gg_id, scan_time) prevented running two backfills (e.g.
-- elo_v1_backfill_v2 + elo+pq_v1_backfill) on the same historical matches:
-- the second run silently skipped every row via ON CONFLICT DO NOTHING.
--
-- We want one row per (match, scan, model) so calibration can compare
-- models head-to-head on identical match sets.

ALTER TABLE cs2_predictions
    DROP CONSTRAINT IF EXISTS cs2_predictions_bo3gg_id_scan_time_key;

ALTER TABLE cs2_predictions
    ADD CONSTRAINT cs2_predictions_uniq
    UNIQUE (bo3gg_id, scan_time, model_version);
