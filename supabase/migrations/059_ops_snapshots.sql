-- Migration 059: ops_snapshots table for operational health dashboard
-- Append-only: one row per snapshot, written by each pipeline job + hourly fallback cron.
-- Dashboard reads: WHERE snapshot_date = CURRENT_DATE ORDER BY created_at DESC LIMIT 1

CREATE TABLE IF NOT EXISTS ops_snapshots (
  id            SERIAL PRIMARY KEY,
  snapshot_date DATE        NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- ① Fixtures & coverage
  matches_today            INT,
  matches_with_odds        INT,
  matches_with_pinnacle    INT,
  matches_with_predictions INT,
  matches_with_signals     INT,
  matches_with_fvectors    INT,
  matches_missing_grade    INT,
  matches_postponed_today  INT,

  -- ② Odds pipeline
  odds_snapshots_today     INT,
  distinct_bookmakers      INT,
  matches_without_pinnacle INT,

  -- ③ Betting & bots
  bets_placed_today  INT,
  bets_pending       INT,
  bets_settled_today INT,
  pnl_today          NUMERIC(10,2),
  bets_inplay_today  INT,
  active_bots        INT,
  silent_bots        INT,
  duplicate_bets     INT,

  -- ④ Live / in-play
  live_snapshots_today     INT,
  snapshots_with_xg        INT,
  snapshots_with_live_odds INT,

  -- ⑤ Post-match / settlement
  matches_finished_today INT,
  post_mortem_ran_today  BOOL,
  feature_vectors_today  INT,
  elo_updates_today      INT,

  -- ⑥ Enrichment quality
  matches_with_h2h      INT,
  matches_with_injuries INT,
  matches_with_lineups  INT,

  -- ⑦ Email & alerts
  digests_sent_today        INT,
  value_bet_alerts_today    INT,
  previews_generated_today  INT,
  news_checker_errors_today INT,
  watchlist_alerts_today    INT,

  -- ⑧ Backfill
  backfill_total_done INT,
  backfill_last_run   TIMESTAMPTZ,

  -- ⑨ API budget (NULL until Phase 3 persists BudgetTracker)
  af_calls_today      INT,
  af_budget_remaining INT,

  -- ⑩ Users
  total_users       INT,
  pro_users         INT,
  elite_users       INT,
  new_signups_today INT
);

CREATE INDEX idx_ops_snapshots_date ON ops_snapshots (snapshot_date, created_at DESC);
