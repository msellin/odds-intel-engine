-- Captures which team a player was playing for in this specific match.
-- Required to derive per-team avg-player-rating PIT features:
--   avg(rating) WHERE team_name = ? AND match_date < ?
-- The parser determines team via totalstats table-index parity (tbl_idx % 2).

ALTER TABLE cs2_hltv_player_match_stats
    ADD COLUMN IF NOT EXISTS team_name TEXT;

-- Existing rows have NULL team_name. They'll be repopulated when the queue
-- entries are re-fetched (queue rows reset below).
CREATE INDEX IF NOT EXISTS cs2_hltv_player_match_stats_team_idx
    ON cs2_hltv_player_match_stats (team_name, hltv_match_id);
