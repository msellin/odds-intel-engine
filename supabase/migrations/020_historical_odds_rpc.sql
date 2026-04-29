-- Migration 020: get_historical_match_odds RPC
--
-- Returns best and worst bookmaker odds for a set of finished match IDs,
-- used by the track record page to build the layered simulation
-- (showing what difference best vs worst odds makes on real historical picks).
--
-- No time window filter — works on all historical data in odds_snapshots.
-- Returns 1 row per (match_id, selection) — max 3N rows for N matches.
--
-- bookmaker_count: how many distinct bookmakers had odds for this match/selection
-- best_odds:  MAX(odds) across all bookmakers and timestamps
-- worst_odds: MIN(odds) across all bookmakers and timestamps

CREATE OR REPLACE FUNCTION get_historical_match_odds(p_match_ids uuid[])
RETURNS TABLE(
  match_id        uuid,
  selection       text,
  best_odds       numeric,
  worst_odds      numeric,
  bookmaker_count bigint
)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT
    o.match_id,
    o.selection,
    MAX(o.odds)::numeric     AS best_odds,
    MIN(o.odds)::numeric     AS worst_odds,
    COUNT(DISTINCT o.bookmaker)::bigint AS bookmaker_count
  FROM odds_snapshots o
  WHERE o.match_id = ANY(p_match_ids)
    AND o.market IN ('1x2', '1X2')
  GROUP BY o.match_id, o.selection;
$$;

GRANT EXECUTE ON FUNCTION get_historical_match_odds(uuid[]) TO anon, authenticated;
