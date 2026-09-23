-- 379 — CONSENSUS-ARM GRADING ([[#094]], 2026-09-23, owner-approved).
--
-- Every consensus_anchor pick is graded 'B' (standard) or 'C' (weak) at publish
-- time, with the machine reasons ('tier0', 'panel:<Book>', 'edge'). A LABEL,
-- not a gate: selection is unchanged and every pick still publishes. Stored per
-- row so the arm can later be split into two bots and either one retired on its
-- own record. NULL on the live and junk arms — they are not graded.
--
-- Evidence and definitions: docs/PUBLISHED_PICKS_GRADING_2026_09_23.md.
ALTER TABLE picks_forward_test
    ADD COLUMN IF NOT EXISTS grade text,
    ADD COLUMN IF NOT EXISTS grade_reasons text[];

ALTER TABLE picks_forward_test DROP CONSTRAINT IF EXISTS picks_forward_test_grade_check;
ALTER TABLE picks_forward_test
    ADD CONSTRAINT picks_forward_test_grade_check CHECK (grade IS NULL OR grade IN ('B', 'C'));
