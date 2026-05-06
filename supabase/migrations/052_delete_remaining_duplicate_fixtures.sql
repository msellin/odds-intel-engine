-- Migration 052: Delete remaining duplicate fixtures from Kambi overlap
-- Migration 047 cleaned May 3–4 duplicates. This cleans any remaining ones
-- in recent/upcoming matches (last 7 days) using the same detection logic
-- as the frontend dedup: same league + same kickoff hour + same home team
-- name prefix (4 chars) = same fixture.
--
-- All child tables reference matches(id) ON DELETE CASCADE, so deleting
-- the orphan match row is sufficient — no manual child cleanup needed.
--
-- Strategy: keep the record with the most associated data (odds + predictions
-- + signals). The AF-sourced record always wins because it has 13-bookmaker
-- odds coverage vs Kambi's 1–2.
--
-- Idempotent: if no duplicates exist, the DELETE affects 0 rows.

WITH match_scores AS (
  SELECT
    m.id,
    m.league_id,
    date_trunc('hour', m.date) AS kickoff_hour,
    left(lower(regexp_replace(t.name, '[^a-z0-9]', '', 'gi')), 4) AS home_prefix,
    (SELECT count(*) FROM odds_snapshots   WHERE match_id = m.id) +
    (SELECT count(*) FROM predictions      WHERE match_id = m.id) * 5 +
    (SELECT count(*) FROM match_signals    WHERE match_id = m.id) AS data_score
  FROM matches m
  JOIN teams t ON m.home_team_id = t.id
  WHERE m.date >= NOW() - INTERVAL '7 days'
),
dup_groups AS (
  -- Only consider (league, hour, home_prefix) buckets that have more than one match
  SELECT league_id, kickoff_hour, home_prefix
  FROM match_scores
  GROUP BY league_id, kickoff_hour, home_prefix
  HAVING count(*) > 1
),
ranked AS (
  SELECT
    ms.id,
    ROW_NUMBER() OVER (
      PARTITION BY ms.league_id, ms.kickoff_hour, ms.home_prefix
      ORDER BY ms.data_score DESC, ms.id  -- higher data score wins; UUID as stable tiebreaker
    ) AS rn
  FROM match_scores ms
  INNER JOIN dup_groups dg
    ON  ms.league_id    = dg.league_id
    AND ms.kickoff_hour = dg.kickoff_hour
    AND ms.home_prefix  = dg.home_prefix
)
DELETE FROM matches
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
