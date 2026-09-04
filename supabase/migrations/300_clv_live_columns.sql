-- CLV-GATE-UNVALIDATED-2026-09-04
--
-- `clv` and `clv_pinnacle` are computed from `odds_at_pick`, which
-- STALE-BEST-ODDS proved is a high-water mark across the fixture's entire
-- snapshot history rather than a price on offer — it overstates by a mean
-- +0.2522 decimal points. So CLV measured partly the price and partly which
-- random peak the scraper happened to catch.
--
-- The ticket that prompted this suspected CLV was useless: correlation with
-- realised return of +0.0147, highest quintile losing money. That was measured
-- on a 717-pick subset. On all 10,542 settled shadow picks that carry a
-- de-vigged Pinnacle close, CLV is strongly predictive even as stored
-- (corr +0.0825, t=+8.50) — and repricing it on the quote that was actually
-- available sharpens it further:
--
--     as stored (odds_at_pick)        corr +0.0825   t = +8.50
--     repriced (odds_at_pick_live)    corr +0.0991   t = +10.23
--
--     repriced quintile:  Q1 -17.10%   Q2 -8.33%   Q3 -2.28%
--                         Q4  -0.20%   Q5 +11.15%
--
-- Monotonic across a 28pp spread. CLV predicts returns here; the price feeding
-- it was the problem. So the promotion gate's CLV route is kept, not dropped.
--
-- New columns rather than a rewrite, following migration 291: `clv` is shown on
-- the performance pages, and silently restating a published figure is the thing
-- we fault competitors for. The old values stay as the historical record.

ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS clv_live NUMERIC;
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS clv_pinnacle_live NUMERIC;
ALTER TABLE shadow_bets    ADD COLUMN IF NOT EXISTS clv_live NUMERIC;
ALTER TABLE shadow_bets    ADD COLUMN IF NOT EXISTS clv_pinnacle_live NUMERIC;

COMMENT ON COLUMN shadow_bets.clv_live IS
  'CLV priced at odds_at_pick_live (the quote actually on offer) rather than the '
  'odds_at_pick high-water mark. Use this for gating and analysis; `clv` is kept '
  'as the historical record. CLV-GATE-UNVALIDATED-2026-09-04.';
COMMENT ON COLUMN shadow_bets.clv_pinnacle_live IS
  'De-vigged Pinnacle CLV priced at odds_at_pick_live. This is the metric the '
  'promotion gate should read: corr +0.0991 with realised return (t=+10.23) '
  'against +0.0825 for the odds_at_pick version.';

-- shadow_bets_unique freezes its column list at creation (same trap as
-- migrations 295 and 298), so the view has to be restated to expose the new
-- columns — the promotion gate reads the VIEW, not the base table.
CREATE OR REPLACE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection)
    sb.id, sb.shadow_run_id, sb.shadow_cohort, sb.bot_id, sb.match_id,
    sb.market, sb.selection, sb.odds_at_pick, sb.pick_time, sb.stake,
    sb.model_probability, sb.calibrated_prob, sb.edge_percent,
    sb.recommended_bookmaker, sb.kelly_fraction, sb.timing_cohort,
    sb.model_version, sb.closing_odds, sb.clv, sb.result, sb.pnl,
    sb.created_at, sb.meta_clv_score, sb.strategy_profile, sb.void_reason,
    sb.clv_pinnacle, sb.closing_bookmaker, sb.pair_gap_hours,
    sb.odds_at_pick_live,
    sb.clv_live, sb.clv_pinnacle_live,
    b.retired_at AS bot_retired_at,
    b.is_active  AS bot_is_active,
    b.name       AS bot_name
   FROM shadow_bets sb
   LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
