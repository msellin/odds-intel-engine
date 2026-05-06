-- Migration 048: Delete zero-match orphan leagues with no data impact
-- These are Kambi-created league records that have no associated matches.
-- Migration 047 only cleaned up leagues that had matches; these slipped through.
-- Safe to delete directly — no foreign key references to clean up.

BEGIN;

-- Brazil / Paulista A4 (0 matches) — duplicate of 'Paulista - A4' (af_id=1062)
DELETE FROM leagues WHERE id = 'e980cac1-b5a2-4a10-90c4-c55e5a9be2a1'
  AND api_football_id IS NULL AND name = 'Paulista A4';

COMMIT;
