-- BETA-BOT-AUDIT follow-up (2026-09-09) — retire bot_1x2_specialist. Owner-authorized.
-- The widening investigation found its BASE record is negative-CLV (−2.69% median at
-- n=8; the +45% ROI was small-n noise), it is near-dormant (last fired 2026-08-23,
-- ~0.09/day), and every de-whitelisted variant is robustly negative (−5 to −6% CLV at
-- n in the thousands). Its 100%-unique coverage is real but the edge is not — so it was
-- padding the roster with a losing, dead strategy. Leaves bot_high_roi_global_v2 as the
-- sole beta nurture bot (+2.0% CLV, 71% unique — keep collecting, edge is load-bearing,
-- cannot be widened). See beta-bot widening audit 2026-09-09.
UPDATE bots
   SET is_active = FALSE,
       retired_at = NOW(),
       retired_reason = 'BETA-BOT-AUDIT 2026-09-09: negative CLV even at base (-2.69% n=8), near-dormant, no widenable edge',
       updated_at = NOW()
 WHERE name = 'bot_1x2_specialist' AND retired_at IS NULL;
