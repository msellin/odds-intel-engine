-- INPLAY-METADATA-STALENESS (2026-06-03)
-- Surface the staleness of in-play picks: which minute they were placed at
-- and what the score was. Without these, the /value-bets list shows an
-- "In-play" pick with no signal of whether it was offered at minute 3 (still
-- close to prematch state) or minute 67 (highly path-dependent, less
-- reliable). The inplay bot already has these values in cand["minute"],
-- cand["score_home"], cand["score_away"] — and currently encodes them only
-- in the `reasoning` JSON. Promoting them to first-class columns lets the
-- UI render "In-play · 23' · 0-1" and unlocks per-minute ROI analysis.
--
-- All three columns are nullable: prematch bots leave them NULL (they don't
-- have a "match minute at pick time"). Historical inplay rows also stay NULL
-- until backfilled — separate task.

ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS match_minute_at_pick INT,
  ADD COLUMN IF NOT EXISTS score_home_at_pick INT,
  ADD COLUMN IF NOT EXISTS score_away_at_pick INT;

COMMENT ON COLUMN simulated_bets.match_minute_at_pick IS
  'INPLAY-METADATA-STALENESS — match minute when the bet was offered. NULL for prematch bots.';
COMMENT ON COLUMN simulated_bets.score_home_at_pick IS
  'INPLAY-METADATA-STALENESS — home score when the bet was offered. NULL for prematch bots.';
COMMENT ON COLUMN simulated_bets.score_away_at_pick IS
  'INPLAY-METADATA-STALENESS — away score when the bet was offered. NULL for prematch bots.';
