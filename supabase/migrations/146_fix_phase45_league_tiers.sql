-- Fix tier misclassifications for phase 4+5 extended leagues added 2026-05-28.
-- All phase 4+5 leagues were inserted with default tier values (0 or 1).
-- Many are second, third, or fourth divisions; wrong tiers cause:
--   - bot_opt_home_lower (tier_filter=[2,3,4]) silently misses valid candidates
--   - bot_btts_conservative (tier_filter=[1,2]) fires on 3rd/4th tier leagues it shouldn't
-- Same guard pattern as migrations 138 and 140.

-- ── tier=0 → tier=1 (top divisions stored as unknowns) ──────────────────────

UPDATE leagues SET tier = 1 WHERE tier = 0 AND (
    (name ILIKE '%premier league%'  AND country = 'Ethiopia')
 OR (name ILIKE '%iraqi league%'    AND country = 'Iraq')
);

-- ── tier=0 → tier=2 ──────────────────────────────────────────────────────────

UPDATE leagues SET tier = 2 WHERE tier = 0 AND (
    (name ILIKE '%first league%'    AND country = 'Armenia')
 OR (name ILIKE '%birinci%'         AND country = 'Azerbaijan')
 OR (name ILIKE '%liga leumit%'     AND country = 'Israel')
 OR (name ILIKE '%prva liga%'       AND country = 'Serbia')
 OR (name ILIKE '%2. snl%'          AND country = 'Slovenia')
);

-- ── tier=0 → tier=3 ──────────────────────────────────────────────────────────

UPDATE leagues SET tier = 3 WHERE tier = 0 AND (
    (name ILIKE '%league two%'      AND country = 'China')
 OR (name ILIKE '%3. liga%'         AND country = 'Czech-Republic')
 OR (name ILIKE '%liga 3%'          AND country = 'Georgia')
 OR (name ILIKE '%iii liga%'        AND country = 'Poland')
);

-- ── tier=0 → tier=4 ──────────────────────────────────────────────────────────

UPDATE leagues SET tier = 4 WHERE tier = 0 AND (
    (name ILIKE '%primera c%'       AND country = 'Argentina')
 OR (name ILIKE '%federal a%'       AND country = 'Argentina')
);

-- ── tier=1 → tier=2 (second divisions wrongly at top-division slot) ──────────

UPDATE leagues SET tier = 2 WHERE tier = 1 AND (
    (name ILIKE '%second league%'        AND country = 'Bulgaria')
 OR (name ILIKE '%esiliiga a%'           AND country = 'Estonia')
 OR (name ILIKE '%ykk%nen%'              AND country = 'Finland')
 OR (name ILIKE '%erovnuli liga 2%'      AND country = 'Georgia')
 OR (name ILIKE '%nb ii%'                AND country = 'Hungary')
 OR (name ILIKE '%1. deild%'             AND country = 'Iceland')
 OR (name ILIKE '%azadegan%'             AND country = 'Iran')
 OR (name ILIKE '%1. division%'          AND country = 'Kazakhstan')
 OR (name ILIKE '%1 lyga%'               AND country = 'Lithuania')
 OR (name ILIKE '%intermedia%'           AND country = 'Paraguay')
 OR (name ILIKE '%segunda%'              AND country = 'Peru')
 OR (name ILIKE '%first league%'         AND country = 'Russia')
 OR (name ILIKE '%2. liga%'              AND country = 'Slovakia')
 OR (name ILIKE '%persha liga%'          AND country = 'Ukraine')
 OR (name ILIKE '%mls next pro%'         AND country = 'USA')
);

-- ── tier=1 → tier=3 ──────────────────────────────────────────────────────────

UPDATE leagues SET tier = 3 WHERE tier = 1 AND (
    (name ILIKE '%3. liga%'              AND country = 'Czech-Republic')
 OR (name ILIKE '%kakkonen%'             AND country = 'Finland')
 OR (name ILIKE '%rfef%'                 AND country = 'Spain')
 OR (name ILIKE '%ettan%'                AND country = 'Sweden')
 OR (name ILIKE '%primera b metropolitana%' AND country = 'Argentina')
 OR (name ILIKE '%usl league one%'       AND country = 'USA')
);

-- ── tier=1 → tier=4 ──────────────────────────────────────────────────────────

UPDATE leagues SET tier = 4 WHERE tier = 1 AND (
    (name ILIKE '%serie d%'              AND country = 'Brazil')
 OR (name ILIKE '%iii liga%'             AND country = 'Poland')
 OR (name ILIKE '%usl league two%'       AND country = 'USA')
);
