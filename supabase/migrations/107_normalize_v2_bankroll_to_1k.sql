-- PERF-V2-BANKROLL-1K (2026-05-17): reset bot_aggressive_v2 to the same
-- €1000 starting bankroll as every other bot in the portfolio.
--
-- Background: bot_aggressive_v2 was created with starting_bankroll = 10000
-- (Supabase column default). Kelly stake sizing is fractional on the
-- bankroll (workers/model/improvements.py: stake = kelly × 0.15 × bankroll,
-- capped at 1% bankroll), so v2's stakes landed €36-99 vs every other bot's
-- €5-10. Portfolio headline ROI is `total_pnl / total_staked`, so v2 was
-- about to get ~10× the weight of every other bot in the aggregate.
--
-- Fix (chosen over the alternate "fix the chart to handle multiple starting
-- bankrolls" approach): bring v2 into line with the rest of the portfolio.
--   1. starting_bankroll: 10000 → 1000
--   2. Settled bets (won/lost): stake /= 10, pnl /= 10 (ROI % unchanged)
--   3. Pending bets: stake /= 10 (pnl is null, will settle at new scale)
--   4. bankroll_after: recompute running totals for settled rows only
--   5. current_bankroll: recompute from starting + sum(pnl)
--
-- New bets after this migration: pipeline reads current_bankroll for Kelly
-- sizing, so the next betting cycle will produce €5-10 stakes automatically.
-- No code change needed.

-- 1. Scale settled bets
UPDATE simulated_bets sb
SET stake = sb.stake / 10.0,
    pnl   = sb.pnl / 10.0
FROM bots b
WHERE sb.bot_id = b.id
  AND b.name = 'bot_aggressive_v2'
  AND sb.result IN ('won', 'lost');

-- 2. Scale pending bets (stake only — pnl is null until settlement)
UPDATE simulated_bets sb
SET stake = sb.stake / 10.0
FROM bots b
WHERE sb.bot_id = b.id
  AND b.name = 'bot_aggressive_v2'
  AND sb.result = 'pending';

-- 3. Reset starting_bankroll to 1000
UPDATE bots
SET starting_bankroll = 1000.00
WHERE name = 'bot_aggressive_v2';

-- 4. Recompute bankroll_after running totals for settled rows
WITH ranked AS (
  SELECT sb.id,
         SUM(COALESCE(sb.pnl, 0)) OVER (
           ORDER BY sb.created_at, sb.id
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS running_pnl
  FROM simulated_bets sb
  JOIN bots b ON b.id = sb.bot_id
  WHERE b.name = 'bot_aggressive_v2'
    AND sb.result IN ('won', 'lost')
)
UPDATE simulated_bets sb
SET bankroll_after = 1000.00 + ranked.running_pnl
FROM ranked
WHERE sb.id = ranked.id;

-- 5. Recompute current_bankroll = starting + sum(pnl)
UPDATE bots
SET current_bankroll = 1000.00 + COALESCE((
  SELECT SUM(pnl) FROM simulated_bets
  WHERE bot_id = bots.id AND pnl IS NOT NULL
), 0)
WHERE name = 'bot_aggressive_v2';
