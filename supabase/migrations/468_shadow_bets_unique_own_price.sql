-- 468 — [[#179]] (fix to migration 467, 2026-09-26): shadow_bets_unique exposes shadow_bets.odds_own_pick_time and
-- own_price_checked_at.
--
-- WHY. 467 added the two columns to shadow_bets, but a view freezes its column list at creation, so
-- shadow_bets_unique (the read path every shadow consumer uses — ANALYSIS_GOTCHAS #5) could not see them.
-- Caught by smoke SHADOW-VIEW-COLUMN-DRIFT in CI — the same miss 458 fixed for 453. CREATE OR REPLACE may
-- APPEND columns, so this is the live definition (pg_get_viewdef) with the two columns added last; dependent
-- views and every GRANT are untouched.
-- depends on: 467_shadow_own_price_pick_time.sql
SET lock_timeout = '3s';

CREATE OR REPLACE VIEW shadow_bets_unique AS
 SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection) sb.id,
    sb.shadow_run_id,
    sb.shadow_cohort,
    sb.bot_id,
    sb.match_id,
    sb.market,
    sb.selection,
    sb.odds_at_pick,
    sb.pick_time,
    sb.stake,
    sb.model_probability,
    sb.calibrated_prob,
    sb.edge_percent,
    sb.recommended_bookmaker,
    sb.kelly_fraction,
    sb.timing_cohort,
    sb.model_version,
    sb.closing_odds,
    sb.clv,
    sb.result,
    sb.pnl,
    sb.created_at,
    sb.meta_clv_score,
    sb.strategy_profile,
    sb.void_reason,
    sb.clv_pinnacle,
    sb.closing_bookmaker,
    sb.pair_gap_hours,
    sb.odds_at_pick_live,
    sb.clv_live,
    sb.clv_pinnacle_live,
    b.retired_at AS bot_retired_at,
    b.is_active AS bot_is_active,
    b.name AS bot_name,
    sb.closing_margin,
    sb.clv_margin_corrected,
    sb.decision_quote_age_min,
    sb.inplay_minute,
    sb.inplay_score_home,
    sb.inplay_score_away,
    sb.closing_minutes_before_ko,
    sb.closing_minutes_before_ko IS NOT NULL AND sb.closing_minutes_before_ko <= 60 AS closing_fresh,
    sb.odds_at_pick_available,
    sb.rule_version,
    sb.odds_own_pick_time,
    sb.own_price_checked_at
   FROM shadow_bets sb
     LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
