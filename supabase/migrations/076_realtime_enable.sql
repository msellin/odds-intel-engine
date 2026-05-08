-- Enable Supabase Realtime for live match data.
-- live_match_snapshots: INSERT events push score/minute to connected browsers.
-- matches: UPDATE events push status transitions (scheduled→live→finished).
--          REPLICA IDENTITY FULL records the old row so clients can compare
--          previous vs new status without a round-trip.

ALTER TABLE matches REPLICA IDENTITY FULL;

ALTER PUBLICATION supabase_realtime ADD TABLE live_match_snapshots;
ALTER PUBLICATION supabase_realtime ADD TABLE matches;
