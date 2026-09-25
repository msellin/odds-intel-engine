-- 458 — #162 (fix to migration 453, 2026-09-25): shadow_bets_unique exposes shadow_bets.rule_version.
--
-- WHY. Migration 453 added `rule_version` to shadow_bets, but a view freezes its column list at
-- creation, so shadow_bets_unique (the read path every shadow consumer uses — ANALYSIS_GOTCHAS #5)
-- could not see the tag. Caught by smoke SHADOW-VIEW-COLUMN-DRIFT. CREATE OR REPLACE may APPEND a
-- column, so the definition below is the live one (pg_get_viewdef) with `sb.rule_version` added
-- last — the dependent views (shadow_bets_own_book_clv, shadow_bot_scoreboard) and every GRANT
-- are untouched.

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
    sb.rule_version
   FROM shadow_bets sb
     LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
