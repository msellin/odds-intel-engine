-- Enable RLS on model_calibration (was missed in 024).
-- Public read so the anon key can query calibration data if needed.
-- Writes go through service_role only (no INSERT/UPDATE policy for anon/authenticated).

ALTER TABLE model_calibration ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access"
    ON model_calibration FOR SELECT
    USING (true);
