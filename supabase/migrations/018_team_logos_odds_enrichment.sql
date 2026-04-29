-- Migration 018: Team logos + enriched odds RPC
--
-- 1. Add logo_url to teams table so AF fixture logos can be stored.
--    The fixtures pipeline will populate this on next run (04:00 UTC).
--    Until then, the frontend shows an initials fallback circle.
--
-- 2. Replace get_best_match_odds RPC with an enriched version that also
--    returns bookmaker_count (ML-8) and prev_best_odds (ML-7 movement arrows).
--    Must DROP first because return type is changing.

ALTER TABLE teams ADD COLUMN IF NOT EXISTS logo_url text;

-- Drop old version (return type change requires DROP + CREATE)
DROP FUNCTION IF EXISTS get_best_match_odds(uuid[], timestamptz);

-- New enriched version:
--   best_odds      = MAX(odds) over the p_since window  (same as before)
--   bookmaker_count = distinct bookmakers (ML-8 badge)
--   prev_best_odds  = MAX(odds) from >20h ago within window (ML-7 movement arrows)
--                     NULL if no snapshot exists from that period
CREATE FUNCTION get_best_match_odds(
  p_match_ids uuid[],
  p_since timestamptz DEFAULT now() - interval '48 hours'
)
RETURNS TABLE(
  match_id       uuid,
  selection      text,
  best_odds      numeric,
  bookmaker_count int,
  prev_best_odds  numeric
)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT
    o.match_id,
    o.selection,
    MAX(o.odds)::numeric                                               AS best_odds,
    COUNT(DISTINCT o.bookmaker)::int                                   AS bookmaker_count,
    MAX(CASE WHEN o.timestamp < now() - interval '20 hours'
             THEN o.odds END)::numeric                                 AS prev_best_odds
  FROM odds_snapshots o
  WHERE o.match_id = ANY(p_match_ids)
    AND o.market IN ('1x2', '1X2')
    AND o.timestamp >= p_since
  GROUP BY o.match_id, o.selection;
$$;

GRANT EXECUTE ON FUNCTION get_best_match_odds(uuid[], timestamptz) TO anon, authenticated;
