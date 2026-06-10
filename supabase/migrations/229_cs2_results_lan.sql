-- LAN/online flag for cs2_results. PandaScore exposes match.tournament.is_lan
-- but we lose it on the join into cs2_results today. Adding the column +
-- backfilling from cs2_pandascore_matches by (team1, team2, kickoff date).
--
-- Why: LAN vs online has measurably different upset rates. Online has remote
-- play / region / lag factors that LAN doesn't. Useful as a v10 feature.

ALTER TABLE cs2_results
    ADD COLUMN IF NOT EXISTS is_lan BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_cs2_results_is_lan
    ON cs2_results (is_lan) WHERE is_lan IS NOT NULL;

-- Backfill: match on (team1_name, team2_name, kickoff_date)
UPDATE cs2_results r
SET    is_lan = p.is_lan
FROM   cs2_pandascore_matches p
WHERE  r.is_lan IS NULL
  AND  p.is_lan IS NOT NULL
  AND  (
        (r.team1 = p.team1_name AND r.team2 = p.team2_name)
     OR (r.team1 = p.team2_name AND r.team2 = p.team1_name)
       )
  AND  r.kickoff_time::date = p.begin_at::date;
