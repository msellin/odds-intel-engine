-- Extend cs2_model_coefficients to support multi-feature stacking models
-- (v7+). The old (a, b) Platt format stays the default for legacy
-- model_versions; v7+ uses (intercept, coefs JSONB) instead.

ALTER TABLE cs2_model_coefficients
    ADD COLUMN IF NOT EXISTS intercept    FLOAT,
    ADD COLUMN IF NOT EXISTS coefs        JSONB,
    ADD COLUMN IF NOT EXISTS feature_keys TEXT[],
    ADD COLUMN IF NOT EXISTS auc          FLOAT,
    ADD COLUMN IF NOT EXISTS trained_at   TIMESTAMPTZ;

-- a, b are still NOT NULL on the existing rows. For v7 we'll write a=1, b=0
-- (identity Platt) since the stacking model is already calibrated.
ALTER TABLE cs2_model_coefficients ALTER COLUMN a DROP NOT NULL;
ALTER TABLE cs2_model_coefficients ALTER COLUMN b DROP NOT NULL;
