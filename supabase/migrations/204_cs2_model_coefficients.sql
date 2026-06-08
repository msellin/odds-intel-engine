-- Persisted Platt scaling coefficients per model_version.
-- Written by cs2_weekly_calibrate.py cron, read by the scanner at startup.
-- The DB is the source of truth — surviving Railway restarts and visible
-- to admin dashboards.

CREATE TABLE IF NOT EXISTS cs2_model_coefficients (
    model_version    TEXT        PRIMARY KEY,
    a                FLOAT       NOT NULL,
    b                FLOAT       NOT NULL,
    n                INTEGER,
    log_loss         FLOAT,
    accuracy         FLOAT,
    ece              FLOAT,
    seeded_from      TEXT,                                -- where these values came from
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cs2_model_coefficients ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_model_coefficients FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Seed live model with the backfill calibration so scanner has coefficients
-- on its first run instead of waiting for the first weekly cron.
INSERT INTO cs2_model_coefficients (model_version, a, b, n, log_loss, accuracy, ece, seeded_from)
VALUES
  ('elo_v1_backfill_v2', 0.8463104984229967, 0.10911215905110906, 9199, 0.666426531331831, 0.5891944776606153, 0.0303, NULL),
  ('elo+pq_v1_backfill', 0.8294081068419457, 0.09652619744202745, 7163, 0.670001653005872,  0.5839731955884406, 0.0324, NULL),
  ('elo+pq_v1',          0.8294081068419457, 0.09652619744202745, 7163, 0.670001653005872,  0.5839731955884406, 0.0324, 'elo+pq_v1_backfill')
ON CONFLICT (model_version) DO NOTHING;
