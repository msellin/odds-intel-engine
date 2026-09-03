-- SHADOW-RETIRED-BOTS-INVISIBLE-2026-09-03
--
-- 89.3% of shadow_bets (128,243 of 143,602 rows) is produced by RETIRED bots,
-- and 20 of them are still writing -- bot_dc_specialist has 17,032 rows in the
-- last 14 days and was retired on 2026-06-01.
--
-- That is deliberate and must stay. SHADOW-RETIRED-OK (2026-05-20,
-- daily_pipeline_v2.py) keeps retired bots producing shadow rows so the
-- retirement-note recovery criterion -- ">=30 bets at >=3% ROI in shadow_bets"
-- -- remains measurable. Stopping the writes would remove the only evidence
-- that could ever un-retire a strategy. An earlier draft of this work proposed
-- exactly that; it would have destroyed a real capability.
--
-- The actual defect is that retirement is INVISIBLE at the point of use.
-- shadow_bets_unique is the canonical read path (ANALYSIS_GOTCHAS #5), and a
-- row in it carries no hint that its bot has been dead since May. Answering
-- "is this bot retired" requires knowing to join `bots` -- so the default
-- behaviour of the documented view is to silently blend 89% dead weight into
-- any aggregate. On 2026-09-03 that turned a retired market's -4.60% into a
-- filed claim that the model itself was a significant loser.
--
-- So: expose it. Two added columns, no filtering -- filtering here would break
-- the recovery analysis the writes exist for. The caller chooses, but can no
-- longer choose by accident.
--
-- Views freeze their column list at creation, so this is CREATE OR REPLACE with
-- the full list restated (migration 295 did the same for the same reason).

CREATE OR REPLACE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection)
    sb.id,
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
    -- The two new columns. bot_retired_at is NULL for a live bot, so
    -- `WHERE bot_retired_at IS NULL` is the performance-analysis default and
    -- `WHERE bot_retired_at IS NOT NULL` is the recovery query.
    b.retired_at  AS bot_retired_at,
    b.is_active   AS bot_is_active,
    b.name        AS bot_name
   FROM shadow_bets sb
   LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time;

COMMENT ON VIEW shadow_bets_unique IS
  'Canonical deduped shadow ledger (ANALYSIS_GOTCHAS #5). Carries bot_retired_at / '
  'bot_is_active because ~89% of rows come from retired bots that keep writing on '
  'purpose (SHADOW-RETIRED-OK). Performance analysis: WHERE bot_retired_at IS NULL. '
  'Alpha-recovery analysis: WHERE bot_retired_at IS NOT NULL. An unqualified '
  'aggregate over this view is ~89% dead strategies.';
