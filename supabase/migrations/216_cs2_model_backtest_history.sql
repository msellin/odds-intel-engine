-- Persist every sneak-peek / backtest run so we can track model evolution
-- over time. One row per (run_id, feature_set) — a single backtest typically
-- writes ~7 rows (coin, home, saved_model, ...).

CREATE TABLE IF NOT EXISTS cs2_model_backtest_history (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID        NOT NULL,             -- one UUID per script invocation
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    feature_set     TEXT        NOT NULL,             -- coin_flip / saved_model / +team-map wp / ...
    n_matches       INTEGER,                          -- size of the subset
    n_train         INTEGER,
    n_test          INTEGER,
    auc             NUMERIC,
    logloss         NUMERIC,
    brier           NUMERIC,
    accuracy        NUMERIC,
    since_date      DATE,                             -- --since filter used
    feature_keys    TEXT[],                           -- ordered list of features used
    coefs           JSONB,                            -- {"rank_diff": 0.05, ...}
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS cs2_model_backtest_history_run_idx
    ON cs2_model_backtest_history (run_at DESC, feature_set);

ALTER TABLE cs2_model_backtest_history ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public read" ON cs2_model_backtest_history FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
