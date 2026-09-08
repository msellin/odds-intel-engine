-- SHADOW-BOT-CONSOLIDATION-2026-09-08 — retire the 3 still-active beta bots that
-- have NO fold-robust profitable (edge x odds) frame in the per-bot 2D matrix
-- AND add no coverage. The other 14 consolidation candidates were already
-- retired. Owner-approved 2026-09-08 (moves /performance +10.22% -> +12.85% by
-- removing a -36% loser + two v10 duplicates — a byproduct of principled
-- cleanup, not the goal). Reversible: SET retired_at=NULL, is_active=true.
--
--   bot_proven_leagues_v2  -36% (n=337), no profitable frame at any edge x odds
--   bot_opt_home_lower     92% duplicate of bot_v10_all, no frame (correlated
--                          exposure, not coverage — DUPLICATE-BOTS-REVIEW)
--   bot_conservative       91% duplicate of bot_v10_all, no frame
UPDATE bots
   SET is_active = FALSE,
       retired_at = COALESCE(retired_at, NOW()),
       retired_reason = 'SHADOW-BOT-CONSOLIDATION 2026-09-08: no fold-robust profitable 2D frame + duplicate/negative (owner-approved)',
       maturity_label = 'retired',
       updated_at = NOW()
 WHERE name IN ('bot_proven_leagues_v2', 'bot_opt_home_lower', 'bot_conservative')
   AND retired_at IS NULL;
