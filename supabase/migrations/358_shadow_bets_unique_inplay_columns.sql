-- 358_shadow_bets_unique_inplay_columns.sql
-- OWN Phase 1b follow-up (2026-09-15): migration 357 added
-- shadow_bets.inplay_minute / inplay_score_home / inplay_score_away but did not
-- re-create `shadow_bets_unique`, so the deduped view no longer exposed every
-- base-table column (smoke SHADOW-VIEW-COLUMN-DRIFT). Append the three columns.
-- CREATE OR REPLACE VIEW may only add trailing columns; the column order up to
-- decision_quote_age_min is identical to migration 355.
-- Re-appliable.

CREATE OR REPLACE VIEW shadow_bets_unique AS
 SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection) sb.id,
    sb.shadow_run_id, sb.shadow_cohort, sb.bot_id, sb.match_id, sb.market, sb.selection,
    sb.odds_at_pick, sb.pick_time, sb.stake, sb.model_probability, sb.calibrated_prob,
    sb.edge_percent, sb.recommended_bookmaker, sb.kelly_fraction, sb.timing_cohort,
    sb.model_version, sb.closing_odds, sb.clv, sb.result, sb.pnl, sb.created_at,
    sb.meta_clv_score, sb.strategy_profile, sb.void_reason, sb.clv_pinnacle,
    sb.closing_bookmaker, sb.pair_gap_hours, sb.odds_at_pick_live, sb.clv_live,
    sb.clv_pinnacle_live,
    b.retired_at AS bot_retired_at, b.is_active AS bot_is_active, b.name AS bot_name,
    sb.closing_margin, sb.clv_margin_corrected, sb.decision_quote_age_min,
    sb.inplay_minute, sb.inplay_score_home, sb.inplay_score_away
   FROM shadow_bets sb
     LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
