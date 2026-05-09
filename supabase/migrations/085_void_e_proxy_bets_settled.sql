-- Follow-up to 079. Migration 079 voided proxy bets WHERE result = 'pending',
-- but it failed first time (used non-existent 'voided' enum) and by the time the
-- corrected version applied, the settlement job had already marked the 182 bad
-- bets as won/lost. The 'pending' filter then matched zero rows and they slipped
-- through as legitimate-looking results.
--
-- Same scope as 079: shot_proxy era of inplay_e (90W/92L, +83.51/-92.00, net
-- −8.49 pnl, −4.7% ROI). Voiding here so the performance chart, leaderboard,
-- and any aggregate ROI calc reflect a clean baseline from the real-xG era.
UPDATE simulated_bets
SET result = 'void'
WHERE xg_source = 'shot_proxy'
  AND bot_id = (SELECT id FROM bots WHERE name = 'inplay_e')
  AND result IN ('won', 'lost');
