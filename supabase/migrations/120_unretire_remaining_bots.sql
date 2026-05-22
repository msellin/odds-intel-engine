-- BOTS-UNRETIRE-WEEKEND (2026-05-22): un-retire the four bots that migration 117
-- intentionally skipped, so the May 23-24 ~1000-match cohort produces signal
-- across every strategy we've ever shipped.
--
-- Targets:
--   bot_aggressive       — retired 2026-05-17 by migration 104. -5.7% ROI on 441
--                          settled. Migration 104 said "never re-enable" because
--                          bot_aggressive_v2 supersedes it; user is overriding
--                          that for the weekend to gather a v1-vs-v2 dataset on
--                          the same fixtures (clean A/B over big sample).
--   inplay_a2,
--   inplay_c_home,
--   inplay_f             — retired 2026-05-09 during the inplay reorg. Their
--                          logic was absorbed into surviving inplay bots, so
--                          un-retiring will produce duplicate bets on the same
--                          (match, market, selection). That's accepted noise
--                          for this weekend — duplicates are paper-only, no real
--                          money risk, and the extra rows feed calibration work
--                          starting next week.
--
-- All four already have is_active=true (migration 117 only touched the main 8)
-- but retired_at is non-null, and the pipeline gate
-- (workers/jobs/daily_pipeline_v2.py: `is_active AND retired_at IS NULL`)
-- treats either flag as a skip. Nulling retired_at is enough to revive.

UPDATE bots
SET is_active  = true,
    retired_at = NULL
WHERE name IN (
    'bot_aggressive',
    'inplay_a2',
    'inplay_c_home',
    'inplay_f'
);

-- Sanity: every bot row should now be active with no retired_at.
DO $$
DECLARE
    still_retired INT;
BEGIN
    SELECT COUNT(*) INTO still_retired
    FROM bots
    WHERE retired_at IS NOT NULL OR is_active = false;
    IF still_retired > 0 THEN
        RAISE NOTICE 'BOTS-UNRETIRE-WEEKEND: % bots still flagged retired/inactive', still_retired;
    END IF;
END $$;
