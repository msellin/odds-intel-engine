-- 404 — #072 ANON-ROLE-READS-EVERYTHING (2026-09-24): least privilege for the public API role.
--
-- WHY. PostgREST serves unauthenticated requests as `anon`, and the anon key ships in the
-- site's JavaScript, so anything `anon` can SELECT is readable by anyone on the internet.
-- `anon` held SELECT on all 134 public relations (granted by the default privileges below
-- on every new table), and RLS was the only control — off, or a `true` policy, on dozens of
-- them. Readable that way: real-money placement attempts, daemon commands, promo ledger,
-- the pick tables behind the product, shadow bets, query statistics.
--
-- WHAT THE SITE ACTUALLY READS AS ANON (odds-intel-web, 2026-09-24): only
-- `createSupabasePublic()` uses the anon key — src/lib/engine-data.ts,
-- src/lib/forward-test-picks.ts and src/app/api/track-page-view/route.ts. Every other
-- server route uses the service key and is unaffected. Their reads, including embedded
-- relations (`bot:bot_id(...)`, `match:match_id(home_team:..., league:...)`):
--   tables: simulated_bets, bots, matches, teams, leagues, dashboard_cache, match_page_views
--   views:  picks_forward_test_public, picks_forward_test_summary,
--           picks_forward_test_summary_by_market, picks_board_public, picks_public_all
--   rpc:    get_coverage_counts (SECURITY DEFINER)
-- The views are owned by oddsintel_owner and are NOT security_invoker, so they read their
-- base tables with the owner's rights: granting the view is enough, the base table
-- (picks_forward_test, picks_board, ...) is NOT re-granted. That is the design the
-- `*_public` views were built for.
--
-- NOT CHANGED: `match_page_views` — anon had SELECT only, never INSERT/UPDATE, so the
-- page-view upsert in track-page-view was already failing silently. Kept exactly as it was;
-- widening a write grant is a separate decision.
--
-- NOT DONE HERE, done out of band as superuser (see docs/RELIABILITY_LEDGER.md §25):
-- pg_stat_statements, pg_stat_statements_info, hypopg_list_indexes, hypopg_hidden_indexes
-- were granted to anon by `postgres` (extension install). This migration runs as
-- oddsintel_owner, which cannot revoke another grantor's privilege (it would be a silent
-- no-op), so those four were revoked with `sudo -u postgres psql -d oddsintel` on
-- 2026-09-24. The REVOKE lines are repeated below so a superuser replay is complete.

BEGIN;

-- 1. Nothing by default …
REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM anon;

-- 2. … then exactly what the public site reads.
GRANT SELECT ON
    public.simulated_bets, public.bots, public.matches, public.teams, public.leagues,
    public.dashboard_cache, public.match_page_views,
    public.picks_forward_test_public, public.picks_forward_test_summary,
    public.picks_forward_test_summary_by_market, public.picks_board_public,
    public.picks_public_all
TO anon;

-- 3. SECURITY DEFINER functions run with the owner's rights, so EXECUTE on them bypasses
--    the table revoke. Only get_coverage_counts is used by the site. EXECUTE is also
--    granted to PUBLIC by default for every function, so revoke from PUBLIC too; the
--    engine connects as the owner and service_role / authenticated keep explicit grants.
REVOKE EXECUTE ON FUNCTION public.get_best_match_odds(uuid[], timestamp with time zone) FROM anon, PUBLIC;
REVOKE EXECUTE ON FUNCTION public.get_bookmaker_count_for_match(uuid) FROM anon, PUBLIC;
REVOKE EXECUTE ON FUNCTION public.get_historical_match_odds(uuid[]) FROM anon, PUBLIC;
REVOKE EXECUTE ON FUNCTION public.get_latest_match_odds(uuid[]) FROM anon, PUBLIC;
REVOKE EXECUTE ON FUNCTION public.handle_new_user() FROM anon, PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_best_match_odds(uuid[], timestamp with time zone) TO service_role, authenticated;
GRANT EXECUTE ON FUNCTION public.get_bookmaker_count_for_match(uuid) TO service_role, authenticated;
GRANT EXECUTE ON FUNCTION public.get_historical_match_odds(uuid[]) TO service_role, authenticated;
GRANT EXECUTE ON FUNCTION public.get_latest_match_odds(uuid[]) TO service_role, authenticated;

-- 4. New tables / sequences are no longer readable by anon automatically. A future public
--    surface must GRANT explicitly (and should prefer a *_public view).
ALTER DEFAULT PRIVILEGES FOR ROLE oddsintel_owner IN SCHEMA public REVOKE SELECT ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE oddsintel_owner IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM anon;

COMMIT;

-- Superuser-only (no-op when run as oddsintel_owner; applied by hand 2026-09-24):
-- REVOKE SELECT ON public.pg_stat_statements, public.pg_stat_statements_info,
--                  public.hypopg_list_indexes, public.hypopg_hidden_indexes FROM anon;
