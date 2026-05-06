-- Migration 053: Performance RPCs
-- Four functions that eliminate row-fetching anti-patterns in the frontend
-- and Python pipeline. Replacing JS-side aggregation with DB-side aggregation.

-- ── C3: get_latest_match_odds ────────────────────────────────────────────────
-- Used by getTodayOdds(). Replaces SELECT * (all historical snapshots) with
-- DISTINCT ON to return only the latest odds per (match, bookmaker, market,
-- selection). Reduces payload from ~18k rows to ~N_matches × N_combos rows.
CREATE OR REPLACE FUNCTION get_latest_match_odds(p_match_ids uuid[])
RETURNS TABLE(
  match_id  uuid,
  bookmaker text,
  market    text,
  selection text,
  odds      numeric,
  timestamp timestamptz
)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT DISTINCT ON (match_id, bookmaker, market, selection)
    match_id, bookmaker, market, selection, odds, timestamp
  FROM odds_snapshots
  WHERE match_id = ANY(p_match_ids)
  ORDER BY match_id, bookmaker, market, selection, timestamp DESC;
$$;

GRANT EXECUTE ON FUNCTION get_latest_match_odds(uuid[]) TO anon, authenticated;


-- ── C1: get_bookmaker_count_for_match ────────────────────────────────────────
-- Used by getPublicMatchBookmakerCount(). Replaces fetching all 1x2 rows and
-- counting in JS with a single COUNT(DISTINCT bookmaker) in the DB.
CREATE OR REPLACE FUNCTION get_bookmaker_count_for_match(p_match_id uuid)
RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT COUNT(DISTINCT bookmaker)
  FROM odds_snapshots
  WHERE match_id = p_match_id
    AND market IN ('1x2', '1X2');
$$;

GRANT EXECUTE ON FUNCTION get_bookmaker_count_for_match(uuid) TO anon, authenticated;


-- ── D1: get_coverage_counts ──────────────────────────────────────────────────
-- Used by getTrackRecordStats(). Replaces fetching 500 odds_snapshots rows +
-- 2000 matches rows and counting distinct values in JS. Returns two integers.
CREATE OR REPLACE FUNCTION get_coverage_counts(
  p_odds_since_hours  integer DEFAULT 24,
  p_matches_since_days integer DEFAULT 7
)
RETURNS TABLE(bookmaker_count bigint, league_count bigint)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT
    (SELECT COUNT(DISTINCT bookmaker)
       FROM odds_snapshots
      WHERE timestamp >= NOW() - (p_odds_since_hours || ' hours')::interval
    ) AS bookmaker_count,
    (SELECT COUNT(DISTINCT league_id)
       FROM matches
      WHERE date >= NOW() - (p_matches_since_days || ' days')::interval
    ) AS league_count;
$$;

GRANT EXECUTE ON FUNCTION get_coverage_counts(integer, integer) TO anon, authenticated;


-- ── C2: get_odds_movement_bucketed ───────────────────────────────────────────
-- Used by getOddsMovement(). Replaces fetching all snapshots and bucketing
-- by hour in JS with DATE_TRUNC + MAX GROUP BY in the DB.
-- Reduces payload from 100-1000 rows to ~20-50 rows (one per hour-bucket per
-- market/selection).
CREATE OR REPLACE FUNCTION get_odds_movement_bucketed(p_match_id uuid)
RETURNS TABLE(
  hour_bucket timestamptz,
  market      text,
  selection   text,
  best_odds   numeric
)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT
    DATE_TRUNC('hour', timestamp) AS hour_bucket,
    CASE WHEN LOWER(market) = '1x2' THEN '1x2'
         WHEN LOWER(market) IN ('ou25', 'over_under_25') THEN 'over_under_25'
         ELSE LOWER(market)
    END AS market,
    selection,
    MAX(odds)::numeric AS best_odds
  FROM odds_snapshots
  WHERE match_id = p_match_id
    AND LOWER(market) IN ('1x2', 'ou25', 'over_under_25')
    AND odds > 1.0
  GROUP BY DATE_TRUNC('hour', timestamp),
    CASE WHEN LOWER(market) = '1x2' THEN '1x2'
         WHEN LOWER(market) IN ('ou25', 'over_under_25') THEN 'over_under_25'
         ELSE LOWER(market)
    END,
    selection
  ORDER BY hour_bucket ASC;
$$;

GRANT EXECUTE ON FUNCTION get_odds_movement_bucketed(uuid) TO anon, authenticated;
