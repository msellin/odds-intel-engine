-- 406 — #136 COVERAGE-COUNTS-RPC-5-MINUTES (2026-09-24).
--
-- WHY. `rpc/get_coverage_counts` (the public track-record stats: "bookmakers covered",
-- "leagues covered") averaged 335 s per call, max 544 s, over 1,480 calls
-- (pg_stat_statements). It did COUNT(DISTINCT bookmaker) over the last 24 h of
-- odds_snapshots — ~2.6M of 51M rows — through the timestamp-only index: millions of
-- random heap reads, holding a connection for minutes and competing with the pipeline,
-- for a number that has ~20 possible values.
--
-- FIX. A (bookmaker, timestamp) index lets the question be asked the cheap way: walk the
-- distinct bookmakers with a recursive skip-scan (~20 index probes), then ask each one
-- "any row since X?" (one index probe each). Same answer, index-only.
--
-- The index itself was built BY HAND with CREATE INDEX CONCURRENTLY on 2026-09-24 (no
-- write lock on the hottest table; migrate.yml caps a statement at 10 min and wraps
-- nothing in a way that allows CONCURRENTLY). The IF NOT EXISTS below is a no-op on the
-- live DB and documents the index for a replay. It also makes the bookmaker-only index
-- redundant (the composite serves every bookmaker-only lookup), so that one is dropped —
-- net index count on odds_snapshots is unchanged.
--
-- CREATE OR REPLACE keeps the function's grants (anon EXECUTE, granted in 404).

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_bookmaker_ts
    ON public.odds_snapshots (bookmaker, "timestamp");

CREATE OR REPLACE FUNCTION public.get_coverage_counts(
    p_odds_since_hours integer DEFAULT 24,
    p_matches_since_days integer DEFAULT 7)
RETURNS TABLE(bookmaker_count bigint, league_count bigint)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public
AS $function$
  WITH RECURSIVE books AS (
      SELECT min(bookmaker) AS bm FROM odds_snapshots
      UNION ALL
      SELECT (SELECT min(o.bookmaker) FROM odds_snapshots o WHERE o.bookmaker > books.bm)
        FROM books WHERE books.bm IS NOT NULL
  )
  SELECT
    (SELECT count(*) FROM books b
      WHERE b.bm IS NOT NULL
        AND EXISTS (SELECT 1 FROM odds_snapshots o
                     WHERE o.bookmaker = b.bm
                       AND o."timestamp" >= now() - make_interval(hours => p_odds_since_hours))
    ) AS bookmaker_count,
    (SELECT count(DISTINCT league_id) FROM matches
      WHERE date >= now() - make_interval(days => p_matches_since_days)
    ) AS league_count;
$function$;

DROP INDEX IF EXISTS public.idx_odds_snapshots_bookmaker;
