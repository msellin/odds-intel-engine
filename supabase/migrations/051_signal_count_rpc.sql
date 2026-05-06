-- Migration 051: RPC to count distinct signals per match
-- Replaces a 60,000-row PostgREST query with a single aggregated DB call.
-- Used by the matches page signal summary (SUX-1 signal count badge).
CREATE OR REPLACE FUNCTION get_signal_counts(
  p_match_ids UUID[],
  p_since      TIMESTAMPTZ
)
RETURNS TABLE (match_id UUID, signal_count BIGINT)
LANGUAGE sql STABLE
AS $$
  SELECT match_id, COUNT(DISTINCT signal_name) AS signal_count
  FROM   match_signals
  WHERE  match_id = ANY(p_match_ids)
    AND  captured_at >= p_since
  GROUP  BY match_id;
$$;
