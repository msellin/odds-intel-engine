-- CS2 flat €10 stake backfill (2026-06-25).
--
-- CS2-FLAT-STAKE: switch CS2 bots from a confused mixed-staking system
-- (Kelly fraction for match_winner, BASE_STAKE flat for atleast1map —
-- producing €0.20 to €10 stakes on the same fixture) to flat €10 across
-- all picks, matching soccer's daily_pipeline_v2.STAKE.
--
-- Why this migration: existing 22 settled rows have inconsistent stakes
-- (sub-€1 Kelly stakes mixed with €10 flat stakes for atleast1map), so
-- ROI was uninterpretable. Flatten retroactively so the next 30d of
-- new picks can be compared apples-to-apples against historical ones.
--
-- Steps:
--   1. UPDATE every cs2_simulated_bets row to stake=1.0u, stake_eur=10.00.
--   2. Recompute pnl + pnl_eur for SETTLED rows from odds_at_pick.
--   3. Recompute bots.current_bankroll for bot_cs2_* bots from the
--      recomputed pnl_eur sums.
--
-- Idempotent: re-running the migration yields the same values
-- (UPDATEs set fixed amounts derived from odds_at_pick, not the
--  prior stake row).

BEGIN;

-- 1. Flat stake across every row, regardless of historical sizing.
UPDATE cs2_simulated_bets
   SET stake = 1.0,
       stake_eur = 10.00;

-- 2. Recompute pnl on settled rows.
--    won  → pnl = 1.0 * (odds-1),  pnl_eur = 10.00 * (odds-1)
--    lost → pnl = -1.0,            pnl_eur = -10.00
--    void → pnl = 0,               pnl_eur = 0
UPDATE cs2_simulated_bets
   SET pnl     = ROUND(1.0 * (odds_at_pick - 1)::numeric, 4),
       pnl_eur = ROUND(10.00 * (odds_at_pick - 1)::numeric, 2)
 WHERE result = 'won';

UPDATE cs2_simulated_bets
   SET pnl     = -1.0,
       pnl_eur = -10.00
 WHERE result = 'lost';

UPDATE cs2_simulated_bets
   SET pnl     = 0,
       pnl_eur = 0
 WHERE result = 'voided';

-- 3. Recompute bots.current_bankroll for cs2_* bots from settled pnl_eur.
--    starting_bankroll is the seed (typically €1000); cumulative settled
--    pnl_eur is the live adjustment. Bots with zero settled bets keep their
--    starting_bankroll. updated_at refreshed so /admin/cs2 shows movement.
UPDATE bots b
   SET current_bankroll = b.starting_bankroll + COALESCE((
         SELECT SUM(pnl_eur)
           FROM cs2_simulated_bets sb
          WHERE sb.bot_name = b.name
            AND sb.result IN ('won', 'lost', 'voided')
       ), 0),
       updated_at = NOW()
 WHERE b.name LIKE 'bot_cs2_%';

COMMIT;
