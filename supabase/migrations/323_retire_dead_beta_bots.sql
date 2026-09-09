-- BETA-BOT-AUDIT (2026-09-09) — retire two beta bots the A/B gradeability sweep
-- ruled dead weight. Owner-authorized (these touch the /performance beta cohort).
--
--   bot_dnb_specialist    — 0 settled picks in EITHER ledger since 2026-05-28.
--                           Registered active-beta and has never once fired. Dead.
--   bot_summer_specialist — overall ROI -5.7%, CLV median -2.0%, its 1x2 leg is
--                           -52% (n=17), and 52% of its picks merely echo
--                           bot_v10_all (no added breadth). Retiring it also lifts
--                           the /performance headline (it was dragging it down).
--
-- Kept (collecting, not published): bot_high_roi_global_v2 (+2.0% CLV, 71% unique
-- coverage — the real second-bot candidate) and bot_1x2_specialist (100% unique
-- but near-dormant). See the beta-bot audit 2026-09-09.
UPDATE bots
   SET is_active = FALSE,
       retired_at = NOW(),
       retired_reason = CASE name
         WHEN 'bot_dnb_specialist' THEN 'BETA-BOT-AUDIT 2026-09-09: never fired (0 settled ever) — dead'
         WHEN 'bot_summer_specialist' THEN 'BETA-BOT-AUDIT 2026-09-09: -5.7% ROI, -2.0% CLV, 1x2 leg -52%, 52% v10 duplicate'
       END,
       updated_at = NOW()
 WHERE name IN ('bot_dnb_specialist', 'bot_summer_specialist')
   AND retired_at IS NULL;
