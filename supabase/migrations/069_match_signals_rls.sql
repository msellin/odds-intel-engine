-- match_signals has RLS enabled but no SELECT policy, so the anon/public
-- key returns 0 rows. Frontend getMatchSignals() silently gets [] which
-- causes hasSignals=false and hides the entire signal accordion + summary.

CREATE POLICY "Public read access"
    ON match_signals FOR SELECT
    USING (true);
