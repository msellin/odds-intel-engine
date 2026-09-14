-- MFV-REBUILD-EARLY-ERA (2026-09-14) — backup the pre-2026-05-01 rows.
--
-- Migration 339 snapshotted matches from 2026-05-01 because that was the
-- model-relevant window for the READ fix. Task #3 (retrain) changes the
-- requirement: a model trained on the full corpus would be trained on
-- 41,450 LEAKED rows against 49,522 clean ones -- 45 pct contaminated, which
-- would reintroduce the defect the retrain exists to remove.
--
-- team_elo_daily reaches back to 2023-01-26 (252,096 rows), so the early era is
-- genuinely rebuildable rather than merely old, and all 41,450 pre-May rows
-- carry a leaked elo_diff today.
--
-- Same discipline as 339, and for the same two reasons: revert, and -- more
-- usefully -- a measuring instrument. The 2026-09-14 rebuild silently NULLed
-- goals_for_avg_home on 59.7 pct of rows because it read a since-pruned
-- match_signals, and NOTHING about the run looked wrong; only a column-level
-- coverage diff against the 339 snapshot revealed it. That failure mode is now
-- structurally prevented (bulk_upsert coalesce_columns, MFV-UPSERT-NON-
-- DESTRUCTIVE), but the diff is how we will know the prevention worked.

CREATE TABLE IF NOT EXISTS mfv_pre_elo_fix_backup_early AS
SELECT mfv.*
  FROM match_feature_vectors mfv
  JOIN matches m ON m.id = mfv.match_id
 WHERE m.date < '2026-05-01';

CREATE INDEX IF NOT EXISTS mfv_pre_elo_fix_backup_early_match_id_idx
    ON mfv_pre_elo_fix_backup_early (match_id);

COMMENT ON TABLE mfv_pre_elo_fix_backup_early IS
  'Snapshot of match_feature_vectors (matches before 2026-05-01) taken before the '
  'early-era ELO-FORM-LEAK rebuild on 2026-09-14. Companion to '
  'mfv_pre_elo_fix_backup. Together they hold the LEAKED features every live '
  'model bundle was trained on -- the baseline the retrain is judged against.';
