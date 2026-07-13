-- SUPABASE-TO-VPS Phase 9 (SM-9.3) — accelerated 2026-07-13
--
-- Drops the frozen `public` schema on the OddsIntel Supabase project
-- (ref: jjdmmfpulofyykzwiuqr). Data was migrated to Hetzner VPS Postgres
-- 17 on 2026-07-09; safety-net window is being closed 30 days early
-- because we have (a) daily VPS backups to Hetzner Storage Box, (b) a
-- fresh pg_dump of this exact schema saved to the operator's Mac
-- (`/Users/margussellin/backups/supabase-2026-07-13/oddsintel-public.dump`).
--
-- Untouched: auth.* (Supabase Auth — 52 users), storage.* (models bucket).
--
-- Run from the Supabase SQL editor OR via psql with the service_role
-- connection. Wrap in an explicit transaction so it can be rolled back
-- if anything looks wrong at COMMIT time.

BEGIN;

-- Sanity: confirm the auth + storage schemas are still there before we
-- do anything destructive. If either is missing, ABORT — something is
-- already broken and we should investigate before dropping more.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
    RAISE EXCEPTION 'auth schema missing — refusing to drop public';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'storage') THEN
    RAISE EXCEPTION 'storage schema missing — refusing to drop public';
  END IF;
END $$;

-- Show what's about to go. Handy for the log when we COMMIT.
SELECT
  pg_size_pretty(pg_total_relation_size(format('%I.%I', schemaname, tablename)::regclass)) AS size,
  schemaname, tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(format('%I.%I', schemaname, tablename)::regclass) DESC
LIMIT 20;

SELECT count(*) AS public_table_count FROM pg_tables WHERE schemaname = 'public';

-- The drop. CASCADE because there are cross-table FKs + views.
DROP SCHEMA public CASCADE;

-- Recreate the empty schema so PostgREST + Supabase dashboard don't
-- error out expecting `public` to exist. This gives us the same
-- functional state as a brand-new Supabase project.
CREATE SCHEMA public;
GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL ON SCHEMA public TO postgres, service_role;

-- If everything looks good above, COMMIT. Otherwise ROLLBACK.
COMMIT;
