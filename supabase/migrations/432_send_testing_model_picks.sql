-- 432 — #155 owner decisions (2026-09-25): these two bots START SENDING their picks to /picks.
--   bot_v10_1x2_newplus_v1  ("Match result — new model", TESTING) — never takes a VIP-held pick
--                            (vip_exclude), odds capped at 3.00 (LANES), so sending it is safe.
--   bot_high_roi_global_v2  ("High-odds match result", BETA) — a BETA bot is sent by definition.
-- Under #155 a status decides distribution: TESTING and above are sent and counted in their own record.
UPDATE bots SET show_on_picks = true
 WHERE name IN ('bot_v10_1x2_newplus_v1', 'bot_high_roi_global_v2');
