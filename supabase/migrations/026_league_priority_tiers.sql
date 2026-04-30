-- ============================================================
-- Migration 026: Refine League Priority Tiers
-- ============================================================
-- Problem: All priority=10 leagues (UEFA CL, CONCACAF, EPL, etc.) sort
-- alphabetically, so CONCACAF/CONMEBOL appear before UEFA Champions League.
-- Fix: Use the tier system as originally designed in migration 025:
--   1 = UEFA continental cups (CL, EL, ECL)
--   5 = Other continental cups (CONCACAF CL, Libertadores, Sudamericana, CAF CL)
--  10 = Top domestic leagues (unchanged)
--  20 = Major secondary leagues (unchanged)
--  30 = Other notable leagues (unchanged)

BEGIN;

-- UEFA continental cups → priority 1
UPDATE leagues SET priority = 1 WHERE name IN (
  'UEFA Champions League',
  'UEFA Europa League',
  'UEFA Europa Conference League'
) AND country = 'World';

-- Other continental cups → priority 5
UPDATE leagues SET priority = 5 WHERE name IN (
  'CONCACAF Champions League',
  'CONMEBOL Libertadores',
  'CONMEBOL Sudamericana',
  'CAF Champions League'
) AND country = 'World';

COMMIT;
