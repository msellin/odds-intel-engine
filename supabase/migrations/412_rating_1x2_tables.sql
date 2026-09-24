-- 412 — #141 1X2-MODEL-REBUILD: storage for the walk-forward rating model (2026-09-24).
--
-- WHY. The 1X2 rating model (workers/model/ratings_1x2.py) beat the shipped XGBoost
-- head by ~0.06 log-loss on the 2026-08-31.. holdout (dev/active/1x2-model-rebuild-plan.md).
-- Most of the gain on newly tracked leagues comes from PRIOR-SEASON results: 97% of
-- the low-history test rows were in leagues first seen in 2026, and warming the ratings
-- with their earlier seasons cut log-loss on those rows from 1.044 to ~1.005.
--
--   rating_history_results  prior-season results fetched from API-Football
--                           (/fixtures?league&season) by scripts/fetch_1x2_history_cache.py.
--                           Deliberately NOT written into `matches`: that table feeds public
--                           coverage counts, the track record, MFV builders and settlement
--                           sweeps, none of which should suddenly see 180k+ old fixtures.
--                           These rows only update rating state.
--   rating_1x2_predictions  the model's forward predictions, written by the shadow job
--                           (workers/jobs/rating_1x2_shadow.py). Kept OUT of `predictions`
--                           on purpose: pick_generator, pick_triggers and health_alerts read
--                           `predictions` without a strict source filter, so a new source
--                           there could leak into live picks. Nothing reads this table
--                           except the forward evaluation until the owner promotes the model.
--
-- ACCESS: service_role only (#072 — a new table is private by default; no anon grant).

CREATE TABLE IF NOT EXISTS rating_history_results (
    af_fixture_id  integer PRIMARY KEY,
    af_league_id   integer NOT NULL,
    season         smallint NOT NULL,
    kickoff        timestamptz NOT NULL,
    home_af        integer NOT NULL,
    away_af        integer NOT NULL,
    home_name      text,
    away_name      text,
    gh             smallint NOT NULL,   -- 90-minute score: what 1X2 settles on
    ga             smallint NOT NULL,
    hh             smallint,
    ha             smallint,
    fetched_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rating_history_kickoff ON rating_history_results (kickoff);

CREATE TABLE IF NOT EXISTS rating_1x2_predictions (
    match_id       uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    model_version  text NOT NULL,
    p_home         numeric(6,5) NOT NULL,
    p_draw         numeric(6,5) NOT NULL,
    p_away         numeric(6,5) NOT NULL,
    n_home         integer NOT NULL,     -- prior rated matches (coverage gate input)
    n_away         integer NOT NULL,
    gated          boolean NOT NULL,     -- both teams >= MIN_HISTORY; ungated rows are stored, flagged
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, model_version),
    CONSTRAINT chk_rating_1x2_probs CHECK (
        p_home BETWEEN 0 AND 1 AND p_draw BETWEEN 0 AND 1 AND p_away BETWEEN 0 AND 1)
);

ALTER TABLE rating_history_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE rating_1x2_predictions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rating_history_results FROM anon, authenticated;
REVOKE ALL ON rating_1x2_predictions FROM anon, authenticated;
GRANT ALL ON rating_history_results TO service_role;
GRANT ALL ON rating_1x2_predictions TO service_role;
