-- 435 — shadow_bets_unique carries odds_at_pick_available; in-play legs carry none ([[#159]], 2026-09-25)
--
-- Migration 433 added shadow_bets.odds_at_pick_available (the PUBLIC pick-time price: best
-- latest quote at/before pick_time across every publishable book). A view freezes its column
-- list at creation, so shadow_bets_unique — the deduped read every shadow consumer is told to
-- use (ANALYSIS_GOTCHAS #5) — silently lacked it (smoke SHADOW-VIEW-COLUMN-DRIFT). APPENDED at
-- the end, so CREATE OR REPLACE keeps every grant and dependent.
--
-- IN-PLAY legs: the first #159 producer run priced them at "the latest quote at or before
-- pick_time" — for a pick at minute 35 that is the PRE-MATCH board, a different market
-- (ANALYSIS_GOTCHAS §14); the in-play shadow bots read +86% "all books" ROI against −4% at
-- their own recorded price. The producer now skips in-play legs (workers/utils/pick_price.py
-- _PREMATCH); the values it wrote are cleared here, so their public price falls back to the
-- price the in-play writer recorded (odds_at_pick_live / odds_at_pick).
UPDATE public.shadow_bets SET odds_at_pick_available = NULL
 WHERE odds_at_pick_available IS NOT NULL AND inplay_minute IS NOT NULL;
UPDATE public.simulated_bets s SET odds_at_pick_available = NULL
 WHERE s.odds_at_pick_available IS NOT NULL
   AND (s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL
        OR EXISTS (SELECT 1 FROM bots b WHERE b.id = s.bot_id AND b.name LIKE 'inplay\_%'));

CREATE OR REPLACE VIEW public.shadow_bets_unique AS
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
    sb.odds_at_pick_available
   FROM shadow_bets sb
     LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;
