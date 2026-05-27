-- Enable RLS on all tables that were missing it.
-- Categorised into two groups:
--   Public data  — readable by anon/authenticated, write-protected (service_role only)
--   Internal     — no public policy; service_role bypasses RLS automatically

-- ─── Public data tables ───────────────────────────────────────────────────────

ALTER TABLE matches              ENABLE ROW LEVEL SECURITY;
ALTER TABLE leagues              ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams                ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE odds_snapshots       ENABLE ROW LEVEL SECURITY;
ALTER TABLE injuries             ENABLE ROW LEVEL SECURITY;
ALTER TABLE lineups              ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_stats          ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_weather        ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE players              ENABLE ROW LEVEL SECURITY;
ALTER TABLE referees             ENABLE ROW LEVEL SECURITY;
ALTER TABLE referee_matches      ENABLE ROW LEVEL SECURITY;
ALTER TABLE referee_stats        ENABLE ROW LEVEL SECURITY;
ALTER TABLE seasons              ENABLE ROW LEVEL SECURITY;
ALTER TABLE managers             ENABLE ROW LEVEL SECURITY;
ALTER TABLE manager_tenures      ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_coaches         ENABLE ROW LEVEL SECURITY;
ALTER TABLE venues               ENABLE ROW LEVEL SECURITY;
ALTER TABLE bots                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_match_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_signals        ENABLE ROW LEVEL SECURITY;
ALTER TABLE feature_importance   ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboard_cache      ENABLE ROW LEVEL SECURITY;

-- SELECT policies — drop-then-create so the migration is re-runnable
DROP POLICY IF EXISTS "public_read" ON matches;
DROP POLICY IF EXISTS "public_read" ON leagues;
DROP POLICY IF EXISTS "public_read" ON teams;
DROP POLICY IF EXISTS "public_read" ON predictions;
DROP POLICY IF EXISTS "public_read" ON odds_snapshots;
DROP POLICY IF EXISTS "public_read" ON injuries;
DROP POLICY IF EXISTS "public_read" ON lineups;
DROP POLICY IF EXISTS "public_read" ON match_stats;
DROP POLICY IF EXISTS "public_read" ON match_weather;
DROP POLICY IF EXISTS "public_read" ON match_events;
DROP POLICY IF EXISTS "public_read" ON news_events;
DROP POLICY IF EXISTS "public_read" ON players;
DROP POLICY IF EXISTS "public_read" ON referees;
DROP POLICY IF EXISTS "public_read" ON referee_matches;
DROP POLICY IF EXISTS "public_read" ON referee_stats;
DROP POLICY IF EXISTS "public_read" ON seasons;
DROP POLICY IF EXISTS "public_read" ON managers;
DROP POLICY IF EXISTS "public_read" ON manager_tenures;
DROP POLICY IF EXISTS "public_read" ON team_coaches;
DROP POLICY IF EXISTS "public_read" ON venues;
DROP POLICY IF EXISTS "public_read" ON bots;
DROP POLICY IF EXISTS "public_read" ON live_match_snapshots;
DROP POLICY IF EXISTS "public_read" ON match_signals;
DROP POLICY IF EXISTS "public_read" ON feature_importance;
DROP POLICY IF EXISTS "public_read" ON dashboard_cache;

CREATE POLICY "public_read" ON matches              FOR SELECT USING (true);
CREATE POLICY "public_read" ON leagues              FOR SELECT USING (true);
CREATE POLICY "public_read" ON teams                FOR SELECT USING (true);
CREATE POLICY "public_read" ON predictions          FOR SELECT USING (true);
CREATE POLICY "public_read" ON odds_snapshots       FOR SELECT USING (true);
CREATE POLICY "public_read" ON injuries             FOR SELECT USING (true);
CREATE POLICY "public_read" ON lineups              FOR SELECT USING (true);
CREATE POLICY "public_read" ON match_stats          FOR SELECT USING (true);
CREATE POLICY "public_read" ON match_weather        FOR SELECT USING (true);
CREATE POLICY "public_read" ON match_events         FOR SELECT USING (true);
CREATE POLICY "public_read" ON news_events          FOR SELECT USING (true);
CREATE POLICY "public_read" ON players              FOR SELECT USING (true);
CREATE POLICY "public_read" ON referees             FOR SELECT USING (true);
CREATE POLICY "public_read" ON referee_matches      FOR SELECT USING (true);
CREATE POLICY "public_read" ON referee_stats        FOR SELECT USING (true);
CREATE POLICY "public_read" ON seasons              FOR SELECT USING (true);
CREATE POLICY "public_read" ON managers             FOR SELECT USING (true);
CREATE POLICY "public_read" ON manager_tenures      FOR SELECT USING (true);
CREATE POLICY "public_read" ON team_coaches         FOR SELECT USING (true);
CREATE POLICY "public_read" ON venues               FOR SELECT USING (true);
CREATE POLICY "public_read" ON bots                 FOR SELECT USING (true);
CREATE POLICY "public_read" ON live_match_snapshots FOR SELECT USING (true);
CREATE POLICY "public_read" ON match_signals        FOR SELECT USING (true);
CREATE POLICY "public_read" ON feature_importance   FOR SELECT USING (true);
CREATE POLICY "public_read" ON dashboard_cache      FOR SELECT USING (true);

-- ─── Internal tables (no public policy — service_role only) ───────────────────

ALTER TABLE api_budget_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE coolbet_inplay_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_snapshots           ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_coaches_cache      ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_transfer_cache     ENABLE ROW LEVEL SECURITY;
ALTER TABLE value_bet_alert_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_evaluations       ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_feature_vectors   ENABLE ROW LEVEL SECURITY;
