-- Void 182 shot_proxy bets placed by inplay_e between 2026-05-08 and 2026-05-09.
-- The proxy formula used 0.10 xG/shot (correct for SoT only) as the denominator
-- for all shots, inflating expected_shots and producing falsely-low pace_ratio.
-- Result: 90W/92L, −8.49 pnl, −4.7% ROI on 182 bets. Real-xG bets (11) are +0.55.
-- Proxy mode disabled in inplay_bot.py on 2026-05-09. Bad bets voided here so
-- the performance chart shows a clean baseline from the real-xG-only era.
UPDATE simulated_bets
SET result = 'void'
WHERE xg_source = 'shot_proxy'
  AND bot_id = (SELECT id FROM bots WHERE name = 'inplay_e')
  AND result = 'pending';
