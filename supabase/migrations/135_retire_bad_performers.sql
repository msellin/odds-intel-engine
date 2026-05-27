-- Retire 4 bots that have consistently bad ROI and are no longer part of
-- the active strategy set. They move from the "underperforming" leaderboard
-- section to the "Retired Strategies" transparency section.
--
-- bot_aggressive_v2:   -31.8% ROI, 51 bets — superseded by bot_aggressive
-- bot_ou35_attacking:  -40.9% ROI, 33 bets — wrong market calibration
-- bot_high_roi_global: -60.3% ROI, 12 bets — threshold too aggressive
-- bot_proven_leagues:  -67.1% ROI, 14 bets — league selection model mismatch

UPDATE bots
SET retired_at = NOW(), is_active = false
WHERE name IN (
    'bot_aggressive_v2',
    'bot_ou35_attacking',
    'bot_high_roi_global',
    'bot_proven_leagues'
);
