-- AF-STALE-FIXTURE-DATES-2026-08-31, steps 2-4.
--
-- API-Football does not always follow a postponement. Atlético Grau v FBC
-- Melgar moved 31 Aug -> 1 Sept; Coolbet moved with it, AF fixture 1549469
-- kept returning 2026-08-31T20:00:00Z with status NS (checked live against the
-- API, not just our copy). The 04:00 fixtures job re-syncs from AF, so editing
-- `matches.date` by hand is overwritten within a day.
--
-- Consequence: we price a fixture that is not played, a bot raises a pick on
-- it, settlement finds no result, and CLV has no close to compare against.
--
-- MEASURED RATE (2026-09-02): the DATE MISMATCH warning fired 244 times in the
-- snapshot logs, but that is 13 unique fixtures re-logged every run, and only
-- 3 were genuine date discrepancies. The other 10 were candidates that could
-- never have been the fixture at all -- fixed separately by gating the warning
-- on the squad guard. So the real rate is ~3 per 10 days.
--
-- WHY SUPPRESS RATHER THAN CORRECT
-- --------------------------------
-- The ticket's step (2) proposed "book date wins over AF for scheduled
-- fixtures", with a `date_source` column so the correction survives the 04:00
-- re-sync. At 3 cases per 10 days that machinery buys very little and can be
-- WRONG in a way that is worse than doing nothing: if the fuzzy match is off,
-- writing the book's date moves a fixture we would otherwise have priced
-- correctly, and it would then be believed over AF indefinitely.
--
-- Not pricing a fixture whose date we do not trust costs us at most a handful
-- of picks a week and cannot invent a wrong one. So this records the DISPUTE
-- and suppresses, rather than picking a winner. `date_dispute_value` keeps the
-- book's date so a later job -- or a human -- can decide, and the suppression
-- lifts automatically once the two agree again.

ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS date_disputed_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS date_dispute_source TEXT,
  ADD COLUMN IF NOT EXISTS date_dispute_value  TIMESTAMPTZ;

COMMENT ON COLUMN matches.date_disputed_at IS
  'Set when a bookmaker offers this fixture at a materially different kickoff '
  'than matches.date. While non-NULL the fixture is NOT priced and no picks '
  'are raised on it (AF-STALE-FIXTURE-DATES). Cleared automatically once the '
  'book and AF agree again, or when the match leaves scheduled status.';

COMMENT ON COLUMN matches.date_dispute_value IS
  'The kickoff the bookmaker was advertising. Kept so the dispute can be '
  'resolved later; deliberately NOT written over matches.date, because a bad '
  'fuzzy match would then be believed over AF indefinitely.';

-- Partial index: the suppression check runs on every fixture-selection query
-- in the betting pipeline, and the disputed set is tiny.
CREATE INDEX IF NOT EXISTS idx_matches_date_disputed
  ON matches (date_disputed_at) WHERE date_disputed_at IS NOT NULL;

NOTIFY pgrst, 'reload schema';
