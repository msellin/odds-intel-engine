-- Update odds movement RPC to use 30-minute buckets instead of hourly
-- Matches the new 30-minute polling frequency for smoother charts

CREATE OR REPLACE FUNCTION get_odds_movement_bucketed(p_match_id uuid)
RETURNS TABLE (
  hour_bucket timestamptz,
  market text,
  selection text,
  best_odds numeric
)
LANGUAGE sql STABLE
AS $$
  SELECT
    -- 30-minute buckets: truncate to hour then add 30min if minute >= 30
    DATE_TRUNC('hour', timestamp)
      + INTERVAL '30 minutes' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 30)
      AS hour_bucket,
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
  GROUP BY
    DATE_TRUNC('hour', timestamp)
      + INTERVAL '30 minutes' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 30),
    CASE WHEN LOWER(market) = '1x2' THEN '1x2'
         WHEN LOWER(market) IN ('ou25', 'over_under_25') THEN 'over_under_25'
         ELSE LOWER(market)
    END,
    selection
  ORDER BY hour_bucket ASC;
$$;
