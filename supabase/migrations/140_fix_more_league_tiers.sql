-- Fix additional tier misclassifications missed by migration 138:
--
--  1. Saudi-Arabia / South-Africa / United-Arab-Emirates use dashes in the DB;
--     migration 138 used spaces so those leagues were skipped.
--
--  2. Additional top divisions not in migration 138: Egypt, Bolivia, Algeria,
--     Tunisia, Kosovo, Malaysia, UAE.
--
--  3. Belgium Challenger Pro League is Belgium's second division (tier=2).
--
--  4. Argentina Primera Nacional is Argentina's second division (tier=2).
--
--  5. Japan J2/J3 League combined entry belongs at tier=2.
--
-- All guards use WHERE tier = 0 so already-correct rows are not changed.

-- ── Missing tier=1 fixes from migration 138 (country name had wrong case/dash) ──

UPDATE leagues SET tier = 1
WHERE tier = 0
  AND (
      (name ILIKE '%pro league%'            AND country = 'Saudi-Arabia')
   OR (name ILIKE '%premier soccer league%' AND country = 'South-Africa')
   OR (name ILIKE '%pro league%'            AND country = 'United-Arab-Emirates')
   OR (name ILIKE '%pro league%'            AND country = 'United Arab Emirates')
  );

-- ── Additional top divisions not covered by migration 138 ────────────────────

UPDATE leagues SET tier = 1
WHERE tier = 0
  AND (
      (name ILIKE '%premier league%'   AND country = 'Egypt')
   OR (name ILIKE '%primera división%' AND country = 'Bolivia')
   OR (name ILIKE '%ligue 1%'          AND country = 'Algeria')
   OR (name ILIKE '%ligue 1%'          AND country = 'Tunisia')
   OR (name ILIKE '%superliga%'        AND country = 'Kosovo')
   OR (name ILIKE '%super league%'     AND country = 'Malaysia')
   OR (name ILIKE '%premier league%'   AND country = 'Iraq')
   OR (name ILIKE '%premier league%'   AND country = 'Uganda')
   OR (name ILIKE '%liga de expansión%' AND country = 'Mexico')
   OR (name ILIKE '%primera division%' AND country = 'Nicaragua')
   OR (name ILIKE '%super league%'     AND country = 'Zambia')
  );

-- ── Second divisions misclassified at tier=0 ─────────────────────────────────

UPDATE leagues SET tier = 2
WHERE tier = 0
  AND (
      -- Belgium second division
      (name ILIKE '%challenger pro league%' AND country = 'Belgium')
      -- Argentina second division (formerly Nacional B)
   OR (name ILIKE '%primera nacional%'      AND country = 'Argentina')
      -- Japan J2 (sometimes stored as J2/J3 combined entry)
   OR (name ILIKE '%j2%'                    AND country = 'Japan')
      -- Tunisia second division
   OR (name ILIKE '%ligue 2%'              AND country = 'Tunisia')
      -- Uruguay second division
   OR (name ILIKE '%segunda división%'     AND country = 'Uruguay')
      -- Chile second division
   OR (name ILIKE '%segunda división%'     AND country = 'Chile')
  );

-- ── English league pyramid below Championship ─────────────────────────────────
-- England League One = tier 3 (third division); League Two = tier 4; National League = tier 5.
-- These were getting confused in bots expecting tier 1-2 matches only.

UPDATE leagues SET tier = 3
WHERE tier = 1   -- was wrongly at tier=1
  AND name ILIKE '%league one%'
  AND country = 'England';

UPDATE leagues SET tier = 4
WHERE tier = 1   -- was wrongly at tier=1
  AND name ILIKE '%league two%'
  AND country = 'England';

UPDATE leagues SET tier = 5
WHERE tier = 0
  AND name ILIKE '%national league%'
  AND country = 'England'
  AND name NOT ILIKE '%north%'
  AND name NOT ILIKE '%south%'
  AND name NOT ILIKE '%u18%';
