-- STALE-FLAG-AUDIT 2026-06-01
--
-- Followup to today's 3 retirements (bot_dc_specialist, bot_lower_1x2,
-- bot_aggressive). Audit revealed 4 more bots with retired_reason populated
-- but is_active=true — but only 2 of them are still bleeding. The other 2
-- have actually recovered and the reason text is misleadingly stale.
--
-- Retire (matches the reason — bleeding + low/no recent firing):
--   • bot_draw_specialist — n=4 / -100% ROI / -39.6% CLV last 30d, last fire
--     2026-05-13 (18d ago). Reason still accurate: "Same loss profile as
--     bot_aggressive's draw bucket; draws are the worst 1X2 selection..."
--   • inplay_f — n=3 / -17.3% ROI last 30d, last fire 2026-05-08 (24d ago).
--     Already de facto retired per inplay reorganization 2026-05-09; flag fix.
--
-- Clear retired_reason (bot recovered — reason text is now misleading):
--   • bot_conservative — n=8 / +104% ROI / +27.2% CLV last 30d, last fire
--     2026-05-30. Reason was "Never fired in production since launch — criteria
--     too tight." Bot was re-enabled via migration 122 (2026-05-22) and is
--     now firing profitably. Stale reason text contradicts current behaviour.
--   • bot_opt_home_lower — n=20 / +51.9% ROI / +27.0% CLV last 30d, last fire
--     2026-05-31. Reason was "Live +73% on 15 bets was variance. Starved by
--     May 17 retrain." Bot recovered after subsequent retrains; reason no
--     longer applies.

-- Retire the two bleeders
UPDATE bots
SET is_active = false, retired_at = NOW()
WHERE name = 'bot_draw_specialist' AND is_active = true;

UPDATE bots
SET is_active = false, retired_at = NOW()
WHERE name = 'inplay_f' AND is_active = true;

-- Clear stale reasons on the two recovered bots
UPDATE bots
SET retired_reason = NULL
WHERE name = 'bot_conservative'
  AND is_active = true
  AND retired_at IS NULL;

UPDATE bots
SET retired_reason = NULL
WHERE name = 'bot_opt_home_lower'
  AND is_active = true
  AND retired_at IS NULL;
