-- CAL-PLATT-UPGRADE: add platt_c column to model_calibration for 2-feature logistic.
-- When platt_c IS NULL, the row is a standard 1-feature Platt (backward-compatible).
-- When platt_c IS NOT NULL, the calibration is a 2-feature logistic:
--   calibrated = sigmoid(platt_a * shrunk_prob + platt_c * log(odds) + platt_b)
-- First market to use this: O/U (over_under_25_over / over_under_25_under) at 353 settled bets.

ALTER TABLE model_calibration
    ADD COLUMN IF NOT EXISTS platt_c double precision;

COMMENT ON COLUMN model_calibration.platt_c IS
    '2-feature logistic weight on log(odds). NULL = standard 1-feature Platt. Non-null = 2-feature logistic: sigmoid(platt_a*shrunk_prob + platt_c*log(odds) + platt_b).';
