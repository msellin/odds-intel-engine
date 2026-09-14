-- MFV-REBUILD-CLEAN-FEATURES (2026-09-14) — backup before the ELO/form rebuild.
--
-- Master task list #2. Task #1 (ELO-FORM-LEAK, 61cd38b) fixed the READ so the
-- feature builder takes ratings strictly BEFORE the match date. The stored rows
-- were written under the old `date <= match_date` bound and are still leaked:
-- 80.2 pct of them differ from their strictly-pre-match value, and elo_diff
-- scores AUC 0.7396 against a de-vigged market at 0.7270 -- above a benchmark
-- that holds strictly more information, which is impossible for a pre-match
-- feature and is the whole tell.
--
-- `scripts/backfill_mfv_historical.py --from 2026-05-01` rebuilds them through
-- the fixed path. This table is the copy taken first, for two reasons and not
-- only the obvious one:
--
--   1. REVERT. If the rebuild is wrong these rows are the only record of what
--      the live bundles were actually trained on.
--   2. MEASUREMENT. Task #3's retrain has to be compared against the model that
--      exists today, and that comparison is meaningless without the inputs that
--      produced it. Deleting the contaminated data would destroy the baseline
--      for the experiment that justifies the whole exercise.
--
-- Scope is 2026-05-01 onward (~48k of 90,846 rows). Earlier rows go back to
-- 2023-01-26 and sit outside every live training window; rebuilding them would
-- multiply the run time for data no current model reads. If task #3 turns out to
-- need deeper history, extend the backup and the rebuild together.
--
-- REVERT:
--   UPDATE match_feature_vectors m SET elo_home = b.elo_home, elo_away = b.elo_away,
--          elo_diff = b.elo_diff, form_ppg_home = b.form_ppg_home,
--          form_ppg_away = b.form_ppg_away
--     FROM mfv_pre_elo_fix_backup b WHERE b.match_id = m.match_id;

CREATE TABLE IF NOT EXISTS mfv_pre_elo_fix_backup AS
SELECT mfv.*
  FROM match_feature_vectors mfv
  JOIN matches m ON m.id = mfv.match_id
 WHERE m.date >= '2026-05-01';

-- Not a primary key: this is an immutable snapshot, not a live table. An index
-- is what the revert and the before/after comparison both need.
CREATE INDEX IF NOT EXISTS mfv_pre_elo_fix_backup_match_id_idx
    ON mfv_pre_elo_fix_backup (match_id);

COMMENT ON TABLE mfv_pre_elo_fix_backup IS
  'Snapshot of match_feature_vectors (matches from 2026-05-01) taken immediately '
  'before the ELO-FORM-LEAK rebuild on 2026-09-14. These are the LEAKED features '
  'every live model bundle was trained on. Keep until task #3 (retrain) has been '
  'evaluated against them -- they are the baseline for that comparison, not just '
  'a rollback.';
