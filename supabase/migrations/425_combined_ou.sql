-- 425 — #152: storage for the COMBINED O/U model in production (2026-09-25).
-- workers/model/combined_ou.py: ratings (Poisson on the walk-forward goal rates) + power-de-vigged
-- multi-book consensus + Pinnacle, one logit per availability group, per line 1.5/2.5/3.5.
-- Beat the served O/U ensemble on every line (#149 round O1); the SERVED probability is Pinnacle
-- where it prices the line, the combined model elsewhere.
--   combiner_ou_params     one row per fit (fitted twice a day by the rating job)
--   ou_model_predictions   one row per (match, line, version) for upcoming fixtures; refreshed
--                          every 30 min at current prices
-- ACCESS: service_role only (#072).

CREATE TABLE IF NOT EXISTS combiner_ou_params (
    model_version  text NOT NULL,
    fitted_at      timestamptz NOT NULL DEFAULT now(),
    params         jsonb NOT NULL,
    n_train        integer NOT NULL,
    PRIMARY KEY (model_version, fitted_at)
);
ALTER TABLE combiner_ou_params ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON combiner_ou_params FROM anon, authenticated;
GRANT ALL ON combiner_ou_params TO service_role;

CREATE TABLE IF NOT EXISTS ou_model_predictions (
    match_id       uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market         text NOT NULL,            -- over_under_15 / _25 / _35
    model_version  text NOT NULL,
    p_over         numeric NOT NULL,         -- SERVED: Pinnacle where priced, else combined
    p_comb         numeric NOT NULL,         -- the combined model's own probability
    p_pin          numeric,                  -- Pinnacle power-de-vigged, when priced
    grp            text NOT NULL,            -- 'P&C' | 'P' | 'C' | 'none'
    n_books        integer,
    lam_total      numeric,                  -- rating goal rate (dp_lh + dp_la) used by the refresh
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, market, model_version)
);
ALTER TABLE ou_model_predictions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ou_model_predictions FROM anon, authenticated;
GRANT ALL ON ou_model_predictions TO service_role;
