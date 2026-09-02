-- STALE-ODDS-HISTORY-RESTATE-2026-09-02
--
-- STALE-BEST-ODDS established that `odds_at_pick` is a high-water mark rather
-- than a price that was on offer: the pipeline aggregated the entire snapshot
-- history for a fixture and took max(), so it recorded the best price ANY book
-- showed at ANY time. Fixed going forward, but not retroactively -- and `pnl`
-- is settled from `odds_at_pick`, so the whole historical record is inflated.
--
-- Measured on the cohort the landing publishes (692 settled bets):
--   as published                              +13.96%
--   at the best price LIVE at pick time       +10.27%   (528 repriceable, 76%)
--   47.9% of those bets record odds above anything live when raised
--
-- These columns hold the honest price ALONGSIDE the recorded one. Deliberately
-- additive: `odds_at_pick` and `pnl` are NOT overwritten.
--
--   * The audit trail is the product. "We quietly restated our history" is a
--     worse story than the bug, and this repo's whole public claim is that
--     its numbers can be checked.
--   * `bankroll_after` is a running total and bot promotions/retirements were
--     decided on these figures. Mutating in place silently rewrites why every
--     bot was promoted.
--
-- NULL means "not repriceable" -- no accessible book quoted that selection at
-- or before pick_time. Coverage must be published with any restated figure
-- (ANALYSIS_GOTCHAS #29); a recomputed number whose sample is unstated invites
-- exactly the dismissal this work exists to remove.

ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS odds_at_pick_live NUMERIC;

ALTER TABLE shadow_bets
  ADD COLUMN IF NOT EXISTS odds_at_pick_live NUMERIC;

COMMENT ON COLUMN simulated_bets.odds_at_pick_live IS
  'Best price across ACCESSIBLE_BOOKMAKERS that was actually live at pick_time '
  '(latest quote per book at or before pick_time, then max). NULL = not '
  'repriceable. Use for honest ROI; odds_at_pick is the historical record and '
  'is inflated by STALE-BEST-ODDS. Never overwrite odds_at_pick or pnl.';

COMMENT ON COLUMN shadow_bets.odds_at_pick_live IS
  'See simulated_bets.odds_at_pick_live. Shadow signals are followed with real '
  'money, so this matters here too.';

CREATE INDEX IF NOT EXISTS idx_simulated_bets_pick_time_result
  ON simulated_bets (pick_time) WHERE result IN ('won', 'lost');

NOTIFY pgrst, 'reload schema';
