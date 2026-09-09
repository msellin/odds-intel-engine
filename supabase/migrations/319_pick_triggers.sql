-- BOOK-AGNOSTIC-EDGE-ENGINE Stage A (2026-09-09) — the fair-value / trigger-window
-- table. See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.
--
-- One row per (upcoming fixture × market × selection × strategy): the model's
-- CALIBRATED probability and the price band at which ANY book's offer becomes a
-- bet. Book-INDEPENDENT — computed once from the model, then each book's sweep
-- (Stage B) matches its own odds against min_odds..max_odds. This is a standing
-- limit order: the model sets the trigger price, the sweep fills it.
--
--   min_odds = max( 1/(cal_prob − edge_floor), odds_floor )   (enough edge + odds floor)
--   max_odds = min_odds × outlier_mult                        (cap stale/erroneous prices)
CREATE TABLE IF NOT EXISTS pick_triggers (
    id            BIGSERIAL PRIMARY KEY,
    match_id      UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market        TEXT NOT NULL,               -- placer vocab: '1x2', 'over_under_25'
    selection     TEXT NOT NULL,               -- 'home'/'draw'/'away' | 'over'/'under'
    strategy      TEXT NOT NULL,               -- book-agnostic gate-set: 'model_1x2', 'model_ou25'
    cal_prob      DOUBLE PRECISION NOT NULL,   -- calibrated model probability (fixed at prediction time)
    edge_floor    DOUBLE PRECISION NOT NULL,   -- required edge (probability points)
    odds_floor    DOUBLE PRECISION NOT NULL,   -- per-market raw odds floor
    min_odds      DOUBLE PRECISION NOT NULL,   -- the trigger: a book price >= this qualifies
    max_odds      DOUBLE PRECISION,            -- outlier cap: a book price above this is likely stale
    model_version TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    kickoff_at    TIMESTAMPTZ,                 -- = matches.date, for expiry/cleanup
    UNIQUE (match_id, market, selection, strategy)
);

-- Stage B matches the latest book price per (match, market, selection) against the
-- window; index the join keys + the freshness horizon.
CREATE INDEX IF NOT EXISTS idx_pick_triggers_match_market
    ON pick_triggers (match_id, market, selection);
CREATE INDEX IF NOT EXISTS idx_pick_triggers_kickoff
    ON pick_triggers (kickoff_at);

COMMENT ON TABLE pick_triggers IS 'BOOK-AGNOSTIC-EDGE-ENGINE Stage A: per-fixture calibrated fair value + [min_odds,max_odds] trigger window. Each book''s sweep (Stage B) matches its own odds against this. docs/BOOK_AGNOSTIC_EDGE_ENGINE.md';
