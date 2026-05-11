-- INPLAY-STATS-DB (2026-05-11)
-- Persists inplay bot strategy tried/fired counts per UTC-day.
-- Replaces Railway console logs (which rotate) with queryable DB rows.
-- Heartbeat in inplay_bot.py upserts current session totals every 10 cycles.

CREATE TABLE IF NOT EXISTS inplay_bot_stats (
  id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  stat_date   DATE         NOT NULL,
  strategy    TEXT         NOT NULL,
  tried       INTEGER      NOT NULL DEFAULT 0,
  fired       INTEGER      NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (stat_date, strategy)
);

CREATE INDEX IF NOT EXISTS idx_inplay_bot_stats_date ON inplay_bot_stats (stat_date DESC);

ALTER TABLE inplay_bot_stats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Superadmins read inplay_bot_stats" ON inplay_bot_stats;
CREATE POLICY "Superadmins read inplay_bot_stats"
  ON inplay_bot_stats FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM profiles
      WHERE profiles.id = auth.uid()
        AND COALESCE(profiles.is_superadmin, false) = true
    )
  );

COMMENT ON TABLE inplay_bot_stats IS
  'INPLAY-STATS-DB: per-strategy tried/fired counts per UTC day. Upserted from
   inplay_bot.py heartbeat. Replaces Railway console logs for performance analysis.';
