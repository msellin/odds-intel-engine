-- ANON-AUTH PHASE 4: add anon-user tracking columns to ops_snapshots.
--
-- The existing total_users / new_signups_today columns conflate anonymous
-- and real users since both have a profile row. Phase 1 added anon-auth;
-- without these new columns, the ops dashboard reads "47 users today"
-- when the truth might be "38 real users + 9 anonymous fly-bys". This
-- migration adds anon-specific columns so the dashboard can show both.
--
-- write_ops_snapshot will populate the new columns and ALSO fix the
-- existing total_users query to count only profiles with email IS NOT NULL.

ALTER TABLE ops_snapshots
    ADD COLUMN IF NOT EXISTS anon_users_total       INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS anon_users_today       INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS anon_users_engaged_7d  INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS anon_upgrades_7d       INT DEFAULT 0;

COMMENT ON COLUMN ops_snapshots.anon_users_total      IS 'Total auth.users rows where is_anonymous=TRUE (incl. stale).';
COMMENT ON COLUMN ops_snapshots.anon_users_today      IS 'Anon users created today.';
COMMENT ON COLUMN ops_snapshots.anon_users_engaged_7d IS 'Anon users with ≥1 favorite or pick saved in the last 7 days.';
COMMENT ON COLUMN ops_snapshots.anon_upgrades_7d      IS 'Profiles whose email landed in the last 7 days against a user.id older than that — proxy for anon→real conversions.';
