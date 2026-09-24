-- 405 — #072 follow-up (2026-09-24, found by the independent review of 404).
--
-- 404 removed anon from the default privileges for TABLES and SEQUENCES, but new FUNCTIONS
-- were still executable by anon (explicit default `anon=X`) and by PUBLIC (Postgres' built-in
-- default). Harmless for SECURITY INVOKER functions — they run as the caller, and anon can no
-- longer read the tables — but a future SECURITY DEFINER function would have been callable from
-- the public API the moment it was created, with the owner's rights. Close that default.
-- Existing functions are untouched (404 already handled the definer ones). The engine connects
-- as oddsintel_owner (owner, always allowed); service_role / authenticated keep their explicit
-- default EXECUTE.
ALTER DEFAULT PRIVILEGES FOR ROLE oddsintel_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE oddsintel_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
