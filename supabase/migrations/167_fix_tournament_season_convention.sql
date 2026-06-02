-- SEASON-CONVENTION-FIX (2026-06-02): renormalise season values for
-- summer-tournament matches.
--
-- `bulk_store_matches` and `store_match` historically computed season from
-- match_date using the football-season convention (Jan-Jun = previous year),
-- ignoring AF's `league.season` field. For club leagues this is benign
-- because AF's value matches the convention. For summer tournaments
-- (WC, Euro, Copa America, AFCON, Asian Cup, Gold Cup) the convention
-- inverts the year — WC 2026 fixtures (June 2026) ended up as season=2025;
-- WC 2018 ended up split between season=2017 (June-30) and season=2018
-- (July+); Euro 2024 (June-July 2024) ended up partially as season=2023.
--
-- The code is now fixed (supabase_client.py prefers md["season"] when AF
-- provides it). This migration corrects the existing rows. Strategy: for
-- the listed tournament leagues, set `season = EXTRACT(YEAR FROM date)` —
-- which gives the correct edition year regardless of month, because these
-- tournaments are calendar-year identified (WC 2026, Euro 2024, etc.).
--
-- Qualifiers (UEFA Nations League, WC qualifiers, AFCON qualifiers etc.)
-- are NOT touched — they run multi-year and the football-season convention
-- works correctly there.

UPDATE matches m
SET season = EXTRACT(YEAR FROM m.date)::int,
    updated_at = NOW()
FROM leagues l
WHERE m.league_id = l.id
  AND l.country = 'World'
  AND l.api_football_id IN (
      1,    -- FIFA World Cup
      4,    -- UEFA European Championship
      6,    -- Africa Cup of Nations
      7,    -- AFC Asian Cup
      9,    -- CONMEBOL Copa America
      22    -- CONCACAF Gold Cup
  )
  AND m.season != EXTRACT(YEAR FROM m.date)::int;

-- Confidence check after migration (no-op SELECT, just for the log):
-- SELECT l.name, l.api_football_id, m.season, COUNT(*)
-- FROM matches m JOIN leagues l ON l.id = m.league_id
-- WHERE l.api_football_id IN (1,4,6,7,9,22) AND l.country = 'World'
-- GROUP BY 1,2,3 ORDER BY 2,3;
