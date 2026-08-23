-- USER-PICK-MARKS-STATE: tri-state review workflow for shadow-bot picks.
--
-- Old schema: presence-only (row = "bet placed"). New schema adds a `state`
-- column so the operator can track three stages per pick:
--   1 = reviewed (looked at the pick, maybe waiting for better odds)
--   2 = bet placed (manually placed at a bookmaker)
--
-- Existing rows are all "bet placed" so DEFAULT 2 keeps them correct.
ALTER TABLE user_pick_marks
  ADD COLUMN IF NOT EXISTS state SMALLINT NOT NULL DEFAULT 2;
