-- Fix Supabase Security Advisor: cs2_pit_team_map is a SECURITY DEFINER view.
--
-- Postgres views default to running with the privileges of the view CREATOR
-- (usually the postgres role), which means they bypass the calling user's
-- RLS on the underlying tables. With anonymous auth enabled, this means
-- anonymous users could potentially read data the underlying tables would
-- otherwise hide from them via RLS.
--
-- Fix: switch to SECURITY INVOKER (Postgres 15+), which makes the view
-- respect the calling user's permissions on the underlying tables. The
-- view's underlying tables (cs2_hltv_matches, cs2_hltv_match_maps) have
-- their own RLS already, so making the view honour caller permissions
-- is the right behaviour.

ALTER VIEW cs2_pit_team_map SET (security_invoker = on);
