-- 460 — #162 W7.3 remainder (2026-09-26): drop two dead / duplicate RLS policies. No reader changes.
--
-- 1. bots carried TWO identical permissive policies, "Public read" and "public_read" — both
--    FOR SELECT TO public USING (true). Permissive policies OR together, so one is pure noise, and
--    two names for one rule is how a later edit changes one and not the other. Kept "public_read":
--    it is the one this repo created (139_rls_missing_tables.sql); "Public read" predates the
--    migrations folder (Supabase era) and exists in no migration. anon keeps SELECT on bots
--    (404_anon_least_privilege.sql grant list, smoke ANON-LEAST-PRIVILEGE) and still sees every row.
--
-- 2. shadow_bets_anon_read (101_shadow_bets.sql, FOR SELECT TO public USING (true)) is dead since
--    404: anon has NO grant on shadow_bets (verified 2026-09-26 in information_schema.role_table_grants
--    — only authenticated, service_role, oddsintel_owner), so the policy admits nobody it was written
--    for. Who else it touched, checked before dropping:
--      * oddsintel_owner (engine, psycopg2) owns the table and RLS is not FORCEd → unaffected.
--      * service_role (web server client, admin pages e.g. src/lib/admin-jobs.ts) has BYPASSRLS → unaffected.
--      * the views over it (shadow_bets_unique, bot_ledger, clv_sharp_legs) are owner-privileged
--        (no security_invoker) → unaffected.
--      * authenticated: holds a SELECT grant but nothing uses that role against the VPS PostgREST
--        (the web signs only anon + service keys). After this it reads zero shadow_bets rows,
--        which is the least-privilege intent of #072.
SET lock_timeout = '3s';

BEGIN;

DROP POLICY IF EXISTS "Public read" ON public.bots;
DROP POLICY IF EXISTS shadow_bets_anon_read ON public.shadow_bets;

COMMIT;
