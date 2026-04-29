-- Migration 017: get_best_match_odds RPC
--
-- Replaces the frontend's batched odds_snapshots query which was hitting
-- Supabase PostgREST's 1000-row max_rows cap.
--
-- The odds_snapshots table accumulates ~38 rows per match per pipeline run
-- (3 selections × 13 bookmakers). After several runs a batch of 80 matches
-- has 3,000+ rows — PostgREST returns only 1000, so most matches appear
-- to have no odds on the frontend.
--
-- This function returns MAX(odds) GROUP BY (match_id, selection) for any
-- set of match UUIDs within a given time window.
-- 80 matches × 3 rows = 240 rows — well within any limit.

CREATE OR REPLACE FUNCTION get_best_match_odds(
  p_match_ids uuid[],
  p_since timestamptz DEFAULT now() - interval '48 hours'
)
RETURNS TABLE(match_id uuid, selection text, best_odds numeric)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT
    o.match_id,
    o.selection,
    MAX(o.odds)::numeric AS best_odds
  FROM odds_snapshots o
  WHERE o.match_id = ANY(p_match_ids)
    AND o.market IN ('1x2', '1X2')
    AND o.timestamp >= p_since
  GROUP BY o.match_id, o.selection;
$$;

-- Allow anon and authenticated users to call this function
GRANT EXECUTE ON FUNCTION get_best_match_odds(uuid[], timestamptz) TO anon, authenticated;
