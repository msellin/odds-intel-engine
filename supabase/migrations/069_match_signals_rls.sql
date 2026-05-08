-- match_signals has RLS enabled but no SELECT policy, so the anon/public
-- key returns 0 rows. Frontend getMatchSignals() silently gets [] which
-- causes hasSignals=false and hides the entire signal accordion + summary.
--
-- Idempotent: drop-then-create so re-runs on environments where the policy
-- was applied manually (e.g. via dashboard before this migration existed)
-- don't fail with SQLSTATE 42710 ("policy already exists").

DROP POLICY IF EXISTS "Public read access" ON match_signals;

CREATE POLICY "Public read access"
    ON match_signals FOR SELECT
    USING (true);
