-- Migration 067: AF data expansion — indexes + ops tracking for sidelined, transfers, H1 signals
-- Supports: batch enrichment (tasks 1), half-time signals (task 2),
--           player sidelined (task 3), team transfers (task 4)

-- ── Indexes for new signal queries ──────────────────────────────────────────

-- player_sidelined: speed up the COUNT(*) GROUP BY player_id query used in
-- injury_recurrence signal computation (batch_write_morning_signals block 12)
CREATE INDEX IF NOT EXISTS idx_player_sidelined_count
    ON player_sidelined (player_id);

-- team_transfers: speed up the 60-day arrivals count per team
-- (batch_write_morning_signals block 14, squad_disruption signal)
CREATE INDEX IF NOT EXISTS idx_team_transfers_date_team
    ON team_transfers (transfer_date DESC, to_team_api_id);

-- match_stats: speed up the H1 tendency signal join
-- (batch_write_morning_signals block 13)
CREATE INDEX IF NOT EXISTS idx_match_stats_ht_present
    ON match_stats (match_id)
    WHERE shots_home_ht IS NOT NULL;

-- ── Ops snapshot columns for new data coverage tracking ──────────────────────

ALTER TABLE ops_snapshots
    ADD COLUMN IF NOT EXISTS sidelined_players_fetched  integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS transfers_teams_fetched    integer DEFAULT 0;

COMMENT ON COLUMN ops_snapshots.sidelined_players_fetched
    IS 'Count of players whose sidelined history was fetched today';
COMMENT ON COLUMN ops_snapshots.transfers_teams_fetched
    IS 'Count of teams whose transfer history was fetched today';
