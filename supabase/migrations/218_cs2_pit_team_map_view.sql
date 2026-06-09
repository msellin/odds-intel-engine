-- Point-in-time team-per-map win rates from OUR scraped match log.
-- For each (team, map, kickoff_time) it returns the team's career win rate
-- on that map UP TO but not including that match.
--
-- Once cs2_hltv_match_maps has ≥1000 rows this becomes the canonical
-- replacement for the snapshot-based cs2_hltv_team_map_stats, with proper
-- point-in-time correctness (no future leakage).
--
-- Usage:
--   SELECT * FROM cs2_pit_team_map
--   WHERE team_name = 'Vitality' AND map_name = 'Mirage'
--     AND kickoff_time < '2025-08-15'
--   ORDER BY kickoff_time DESC LIMIT 1
--
-- The result is the row where total_maps_played is highest and kickoff_time
-- is just before the query timestamp — i.e. Vitality's Mirage record up to
-- that date.

CREATE OR REPLACE VIEW cs2_pit_team_map AS
WITH team_map_results AS (
    -- Flatten cs2_hltv_match_maps into one row per (team, map_played, win/loss).
    -- Team can be team1 or team2 of the match.
    SELECT
        mh.hltv_match_id,
        mh.match_date AS kickoff_time,
        mh.team1_name AS team_name,
        mm.map_name,
        CASE WHEN mm.winner_name = mh.team1_name THEN 1 ELSE 0 END AS won
    FROM cs2_hltv_matches mh
    JOIN cs2_hltv_match_maps mm USING (hltv_match_id)
    WHERE mh.match_date IS NOT NULL
      AND mh.team1_name IS NOT NULL
      AND mh.team1_name != COALESCE(mh.team2_name, '')

    UNION ALL

    SELECT
        mh.hltv_match_id,
        mh.match_date AS kickoff_time,
        mh.team2_name AS team_name,
        mm.map_name,
        CASE WHEN mm.winner_name = mh.team2_name THEN 1 ELSE 0 END AS won
    FROM cs2_hltv_matches mh
    JOIN cs2_hltv_match_maps mm USING (hltv_match_id)
    WHERE mh.match_date IS NOT NULL
      AND mh.team2_name IS NOT NULL
      AND mh.team1_name != COALESCE(mh.team2_name, '')
)
SELECT
    team_name,
    map_name,
    kickoff_time,
    -- Running totals BEFORE this row (point-in-time)
    SUM(won) OVER w AS wins_before,
    COUNT(*) OVER w AS maps_played_before,
    -- Win rate up to (but not including) this match
    CASE
        WHEN COUNT(*) OVER w > 0
        THEN SUM(won) OVER w::numeric / COUNT(*) OVER w
        ELSE NULL
    END AS pit_win_rate
FROM team_map_results
WINDOW w AS (
    PARTITION BY team_name, map_name
    ORDER BY kickoff_time
    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
);

-- Allow public read via the underlying tables (already permissioned).
-- Views don't have their own RLS in Postgres; permission comes from base tables.
