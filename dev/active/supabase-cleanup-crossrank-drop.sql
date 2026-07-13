-- CrossRank + BoxRank Supabase cleanup — 2026-07-13
--
-- Drops the frozen `public` + `box` schemas on the shared Supabase
-- project (ref: wvcnhlzzawvwoitkllid). Live data lives on Hetzner VPS
-- Postgres 17 (`crossrank` DB, same cluster as `oddsintel`), fronted by
-- `api.crossrank.ee` PostgREST.
--
-- Untouched: auth.* (Supabase Auth — Google OAuth for CrossRank v2),
-- storage.* (competition-submissions + box-whiteboards buckets — both
-- currently unused per BoxRank's move to VPS filesystem, but leaving
-- the storage schema intact so the empty buckets are still there and
-- we don't need to reconfigure anything if BoxRank rewires later).
--
-- Prereq before running: fresh pg_dump of both schemas saved to Mac
-- (`/Users/margussellin/backups/supabase-2026-07-13/crossrank-*.dump`).

BEGIN;

-- Sanity: auth + storage schemas must still exist.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
    RAISE EXCEPTION 'auth schema missing — refusing to drop';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'storage') THEN
    RAISE EXCEPTION 'storage schema missing — refusing to drop';
  END IF;
END $$;

-- Inventory before drop.
SELECT nspname, count(c.oid) AS relation_count
FROM pg_namespace n
LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r','v','m')
WHERE nspname IN ('public','box')
GROUP BY nspname;

SELECT
  pg_size_pretty(pg_total_relation_size(format('%I.%I', schemaname, tablename)::regclass)) AS size,
  schemaname, tablename
FROM pg_tables
WHERE schemaname IN ('public','box')
ORDER BY pg_total_relation_size(format('%I.%I', schemaname, tablename)::regclass) DESC
LIMIT 20;

DROP SCHEMA IF EXISTS box CASCADE;
DROP SCHEMA public CASCADE;

-- Recreate empty public so Supabase dashboard is happy.
CREATE SCHEMA public;
GRANT USAGE ON SCHEMA public TO postgres, anon, authenticated, service_role;
GRANT ALL ON SCHEMA public TO postgres, service_role;

COMMIT;
