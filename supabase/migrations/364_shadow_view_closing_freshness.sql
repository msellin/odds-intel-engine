-- DIRECT-BOOK-CLV-SHADOW-BACKFILL — expose the freshness column on the view.
--
-- Migration 363 added shadow_bets.closing_minutes_before_ko. The smoke test
-- SHADOW-VIEW-COLUMN-DRIFT caught that shadow_bets_unique was not updated to
-- match, which is the test doing exactly its job: a view that silently lags the
-- base table is how a reader ends up unable to see a column that exists.
--
-- Exposing it matters here specifically. The shadow settle path has no
-- DIRECT_CLOSE_MAX_MIN bound (real_bets does, migration 332), so ~40% of
-- historical closes sit more than three hours before kickoff and ~75% of those
-- give clv = 0 by construction rather than by measurement. Without the age on
-- the view, a reader of shadow_bets_unique cannot tell a real closing-line
-- result from a self-comparison.
--
-- closing_fresh is the convenience flag: 60 minutes mirrors real_bets'
-- DIRECT_CLOSE_MAX_MIN, so "fresh" means the same thing in both ledgers.
CREATE OR REPLACE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection)
    sb.id, sb.shadow_run_id, sb.shadow_cohort, sb.bot_id, sb.match_id,
    sb.market, sb.selection, sb.odds_at_pick, sb.pick_time, sb.stake,
    sb.model_probability, sb.calibrated_prob, sb.edge_percent,
    sb.recommended_bookmaker, sb.kelly_fraction, sb.timing_cohort,
    sb.model_version, sb.closing_odds, sb.clv, sb.result, sb.pnl,
    sb.created_at, sb.meta_clv_score, sb.strategy_profile, sb.void_reason,
    sb.clv_pinnacle, sb.closing_bookmaker, sb.pair_gap_hours,
    sb.odds_at_pick_live, sb.clv_live, sb.clv_pinnacle_live,
    b.retired_at AS bot_retired_at,
    b.is_active AS bot_is_active,
    b.name AS bot_name,
    sb.closing_margin, sb.clv_margin_corrected, sb.decision_quote_age_min,
    sb.inplay_minute, sb.inplay_score_home, sb.inplay_score_away,
    sb.closing_minutes_before_ko,
    (sb.closing_minutes_before_ko IS NOT NULL
     AND sb.closing_minutes_before_ko <= 60) AS closing_fresh
  FROM shadow_bets sb
  LEFT JOIN bots b ON b.id = sb.bot_id
 ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
