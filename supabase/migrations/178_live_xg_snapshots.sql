-- WC-D2 (2026-06-04): Live xG snapshots for WC2026 in-progress matches.
--
-- A new poller (workers.jobs.wc_live_xg_poller) writes one row per WC match
-- per 60s tick from /fixtures/statistics. The win-probability curve and
-- goal-probability widgets (Wave 3) consume this time-series.
--
-- Gated to the WC league (leagues.api_football_id = 1) AND
-- matches.status = 'live'. Outside that window the job no-ops.

CREATE TABLE IF NOT EXISTS live_xg_snapshots (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id             uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    captured_at          timestamptz NOT NULL DEFAULT NOW(),
    minute               int NOT NULL,
    home_xg              numeric,
    away_xg              numeric,
    home_shots           int,
    home_shots_on        int,
    away_shots           int,
    away_shots_on        int,
    home_possession_pct  int,
    away_possession_pct  int
);

CREATE INDEX IF NOT EXISTS idx_live_xg_snapshots_match_min
    ON live_xg_snapshots(match_id, minute);

-- Public-read RLS (anon-key frontend access pattern used across the engine)
ALTER TABLE live_xg_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON live_xg_snapshots;
CREATE POLICY "Public read" ON live_xg_snapshots FOR SELECT USING (true);

COMMENT ON TABLE live_xg_snapshots IS
    'WC-D2 (2026-06-04): per-match per-tick live xG/shots/possession capture '
    'for WC2026. Source: API-Football /fixtures/statistics?fixture=ID polled '
    'by workers.jobs.wc_live_xg_poller every 60s during live WC matches. '
    'Feeds Wave 3 win-probability curve + goal-probability widgets.';
