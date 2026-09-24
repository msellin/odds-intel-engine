-- 415 — #141 round 3b: storage for the COMBINED 1X2 model (2026-09-24).
--
-- The combined model (workers/model/combined_1x2.py — rating model + de-vigged multi-book
-- consensus + Pinnacle, + API-Football only where no book prices the match) was adopted on a
-- pre-registered test: log-loss 0.9763 vs 1.0711 for the shipped XGBoost head on 12,640
-- matches from 2026-08-31 (dev/active/1x2-model-rebuild-plan.md, "ROUND 3b").
--
--   combiner_1x2_params         one row per fit: the per-availability-group logit
--                               coefficients as JSON. Fitted twice a day by the rating job,
--                               re-applied every 30 min to current prices by the refresh job.
--   rating_1x2_predictions      + sources: which inputs a row used ('P&C' | 'P' | 'C' | 'none'
--                               for model_version r1x2_comb_v1; NULL for the rating-only model).
--
-- ACCESS: service_role only (#072).

CREATE TABLE IF NOT EXISTS combiner_1x2_params (
    model_version  text NOT NULL,
    fitted_at      timestamptz NOT NULL DEFAULT now(),
    params         jsonb NOT NULL,
    n_train        integer NOT NULL,
    PRIMARY KEY (model_version, fitted_at)
);
ALTER TABLE combiner_1x2_params ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON combiner_1x2_params FROM anon, authenticated;
GRANT ALL ON combiner_1x2_params TO service_role;

ALTER TABLE rating_1x2_predictions ADD COLUMN IF NOT EXISTS sources text;
