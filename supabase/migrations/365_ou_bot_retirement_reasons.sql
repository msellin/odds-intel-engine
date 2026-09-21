-- OU-BOTS-AWAIT-ERA-3 residue (2026-09-21).
--
-- Three bots were retired on 2026-09-14 16:50:50 with retired_at set and
-- retired_reason LEFT NULL, and no migration recorded at that timestamp
-- (_schema_migrations jumps 09-13 -> 09-15). So the DB said "retired" and gave
-- no account of why, while PRIORITY_QUEUE still described them as deliberately
-- kept alive gathering era-3 evidence. Two sources, neither right.
--
-- The reason is recoverable from the ledger and matches the migration-348
-- criterion applied the same day (retire on negative own-book CLV):
--
--     bot_coolbet_trigger_ou_v1   n=546  margin-corrected CLV -6.10%  ROI -8.17%
--     bot_unibet_trigger_ou_v1    n=299  margin-corrected CLV -5.71%  ROI -1.44%
--     bot_trigger_ou_model_v1     n=190  margin-corrected CLV -5.84%  ROI +2.52%
--
-- Note bot_trigger_ou_model_v1's POSITIVE ROI (+2.52%) beside a clearly negative
-- CLV — which is exactly why CLV is the promotion/retirement criterion here and
-- ROI is context: at n=190 an ROI of +2.5% is well inside noise, while a -5.8pp
-- CLV on the same picks is not.
UPDATE bots SET retired_reason =
  'Retired 2026-09-14 on negative own-book margin-corrected CLV, the same '
  'criterion as migration 348 applied that day. Reason backfilled 2026-09-21 '
  '(OU-BOTS-AWAIT-ERA-3): the original retirement set retired_at but left this '
  'NULL, so the DB recorded the fact without the cause. Measured: '
  'margin-corrected CLV -6.10% (n=546). ROI is context only.'
 WHERE name = 'bot_coolbet_trigger_ou_v1' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
  'Retired 2026-09-14 on negative own-book margin-corrected CLV, the same '
  'criterion as migration 348 applied that day. Reason backfilled 2026-09-21 '
  '(OU-BOTS-AWAIT-ERA-3). Measured: margin-corrected CLV -5.71% (n=299).'
 WHERE name = 'bot_unibet_trigger_ou_v1' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
  'Retired 2026-09-14 on negative own-book margin-corrected CLV, the same '
  'criterion as migration 348 applied that day. Reason backfilled 2026-09-21 '
  '(OU-BOTS-AWAIT-ERA-3). Measured: margin-corrected CLV -5.84% (n=190) '
  'DESPITE a positive ROI of +2.52% — at that n the ROI is inside noise and the '
  'CLV is not, which is why CLV is the criterion and ROI is context.'
 WHERE name = 'bot_trigger_ou_model_v1' AND retired_reason IS NULL;
