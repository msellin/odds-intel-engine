-- Extend cs2_hltv_player_match_stats to one row per (match, player, map).
-- Per-map stats are gold for player-form retraining: lets us slice rating by
-- map pool, see Mirage vs Inferno consistency, etc.

ALTER TABLE cs2_hltv_player_match_stats
    ADD COLUMN IF NOT EXISTS map_order   INTEGER,
    ADD COLUMN IF NOT EXISTS map_name    TEXT;

-- Drop the (match, player) unique that prevented multi-map rows.
ALTER TABLE cs2_hltv_player_match_stats
    DROP CONSTRAINT IF EXISTS cs2_hltv_player_match_stats_hltv_match_id_hltv_player_id_key;

-- New uniqueness allows per-map rows. NULL map_order = match-aggregate row.
CREATE UNIQUE INDEX IF NOT EXISTS cs2_hltv_player_match_stats_match_player_map_unq
    ON cs2_hltv_player_match_stats (hltv_match_id, hltv_player_id, COALESCE(map_order, 0));
