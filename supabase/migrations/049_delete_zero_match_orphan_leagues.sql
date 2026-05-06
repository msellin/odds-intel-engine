-- Migration 049: Delete all zero-match orphan leagues
-- These are league records with api_football_id IS NULL and no associated matches.
-- 159 "Unknown" leagues: created by Kambi for countries where the league name wasn't
--   parsed (placeholder records). Zero data impact.
-- 5 named leagues with no matches: leftover from early Kambi scraping before it was removed.
-- All are safe to delete — no foreign key references exist (matches.league_id is NULL
-- for all of these because they have no matches).

BEGIN;

-- Teams referenced by matches (home_team_id or away_team_id) must never be deleted
-- Leagues whose teams appear in matches must also be kept
-- The safe set to delete: Unknown orphan leagues where NEITHER the league NOR any
-- of its teams appear anywhere in the matches table.

-- Step 1: Delete teams in orphan leagues that are not referenced in any match
DELETE FROM teams
WHERE league_id IN (
    SELECT id FROM leagues
    WHERE api_football_id IS NULL
      AND name = 'Unknown'
      AND id NOT IN (SELECT DISTINCT league_id FROM matches WHERE league_id IS NOT NULL)
)
AND id NOT IN (
    SELECT home_team_id FROM matches WHERE home_team_id IS NOT NULL
    UNION
    SELECT away_team_id FROM matches WHERE away_team_id IS NOT NULL
);

-- Step 2: Delete orphan Unknown leagues that have no match references AND no
-- remaining team references (teams linked to matches were excluded above)
DELETE FROM leagues
WHERE api_football_id IS NULL
  AND name = 'Unknown'
  AND id NOT IN (SELECT DISTINCT league_id FROM matches WHERE league_id IS NOT NULL)
  AND id NOT IN (
    SELECT DISTINCT t.league_id FROM teams t
    WHERE t.league_id IS NOT NULL
      AND t.id IN (
          SELECT home_team_id FROM matches WHERE home_team_id IS NOT NULL
          UNION
          SELECT away_team_id FROM matches WHERE away_team_id IS NOT NULL
      )
  );

-- Delete the 5 named zero-match orphan leagues
DELETE FROM leagues
WHERE id IN (
    'f95666bc-443a-47ad-b1e9-8aa0731146aa',  -- Argentina / Primera B Nacional
    '3b852e7e-b86e-449a-aa99-1a702507fcab',  -- Spain / Tercera RFEF 3
    'a495f163-7ea5-47cd-b140-5517e39640f3',  -- Spain / Tercera RFEF 8
    '007d0730-397d-47fd-9e3f-5a37ac9f5eba',  -- Sweden / Division 2 NG
    'dbc8ee9b-fa99-4f3d-96af-5dff7dd8b734'   -- Turkey / Türkiye kupasi
);

COMMIT;
