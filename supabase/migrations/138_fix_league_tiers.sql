-- Fix league tier misclassifications. Many top-division and second-division
-- leagues were stored as tier=0, blocking bots with tier_filter=[1,2] or [2,3,4].
--
-- Root cause: API-Football doesn't consistently classify league depth, so our
-- ingestion left many leagues at tier=0 (the AF default). Bot tier_filters were
-- written assuming tier=1 = top division, tier=2 = second division, etc.
--
-- Impact: AH/DNB/optimizer bots were silently skipping Japan J1, Argentina Liga
-- Profesional, Netherlands Eredivisie, England Championship, and 10+ others.
-- Uses name+country WHERE clauses (not UUIDs) — portable across environments.

-- ── Top divisions → tier=1 ──────────────────────────────────────────────────

UPDATE leagues SET tier = 1
WHERE tier = 0
  AND (
      (name ILIKE '%liga profesional%'          AND country = 'Argentina')
   OR (name ILIKE '%primera a%'                 AND country = 'Colombia')
   OR (name ILIKE '%liga pro%'                  AND country = 'Ecuador')
   OR (name ILIKE '%veikkausliiga%'             AND country = 'Finland')
   OR (name ILIKE '%super league%'              AND country = 'Greece')
   OR (name ILIKE '%indian super league%'       AND country = 'India')
   OR (name ILIKE '%liga 1%'                    AND country = 'Indonesia')
   OR (name ILIKE '%ligat ha%al%'               AND country = 'Israel')
   OR (name ILIKE '%j1 league%'                 AND country = 'Japan')
   OR (name ILIKE '%botola pro%'                AND country = 'Morocco')
   OR (name ILIKE '%eredivisie%'                AND country = 'Netherlands')
   OR (name ILIKE '%primera división%'          AND country = 'Peru')
   OR (name ILIKE '%pro league%'                AND country = 'Saudi Arabia')
   OR (name ILIKE '%premier soccer league%'     AND country = 'South Africa')
   OR (name ILIKE '%a-league%'                  AND country = 'Australia')
   OR (name ILIKE '%allsvenskan%'               AND country = 'Sweden')
   OR (name ILIKE '%süper lig%'                 AND country = 'Turkey')
   OR (name ILIKE '%primeira liga%'             AND country = 'Portugal')
   OR (name ILIKE '%ekstraklasa%'               AND country = 'Poland')
   OR (name ILIKE '%czech liga%'                AND country = 'Czech-Republic')
   OR (name ILIKE '%fortuna liga%'              AND country = 'Czech-Republic')
   OR (name ILIKE '%nemzeti bajnokság%'         AND country = 'Hungary')
   OR (name ILIKE '%liga i%'                    AND country = 'Romania')
   OR (name ILIKE '%premijer liga%'             AND country = 'Bosnia')
  );

-- ── Second divisions → tier=2 ────────────────────────────────────────────────

UPDATE leagues SET tier = 2
WHERE tier = 0
  AND (
      (name ILIKE '%championship%'              AND country = 'England')
   OR (name ILIKE '%eerste divisie%'            AND country = 'Netherlands')
   OR (name ILIKE '%liga portugal 2%'           AND country = 'Portugal')
   OR (name ILIKE '%segunda liga%'              AND country = 'Portugal')
   OR (name ILIKE '%superettan%'                AND country = 'Sweden')
   OR (name ILIKE '%usl championship%'          AND country = 'USA')
   OR (name ILIKE '%segunda división%'          AND country = 'Argentina')
   OR (name ILIKE '%primera b%'                 AND country = 'Argentina')
   OR (name ILIKE '%serie b%'                   AND country = 'Brazil')
   OR (name ILIKE '%j2 league%'                 AND country = 'Japan')
   OR (name ILIKE '%2\. bundesliga%'            AND country = 'Germany')
   OR (name ILIKE '%2. bundesliga%'             AND country = 'Germany')
   OR (name ILIKE '%ligue 2%'                   AND country = 'France')
   OR (name ILIKE '%serie b%'                   AND country = 'Italy')
   OR (name ILIKE '%segunda división%'          AND country = 'Spain')
   OR (name ILIKE '%segunda%'                   AND country = 'Spain')
  );
