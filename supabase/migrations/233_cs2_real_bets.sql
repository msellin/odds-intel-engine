-- CS2 real bets — bridges cs2_simulated_bets (bot picks) → Coolbet placement.
-- Mirrors soccer's real_bets but CS2-native columns (bo3gg_id, bot_name).
--
-- v1 ships with paper=true only. Real-money execution is gated behind
-- explicit operator authorization (memory: feedback_coolbet_execute_safety).
-- When --execute lands later, ticket_id captures the Coolbet bet slip ID.

CREATE TABLE IF NOT EXISTS cs2_real_bets (
    id                      BIGSERIAL PRIMARY KEY,
    cs2_simulated_bet_id    BIGINT NOT NULL REFERENCES cs2_simulated_bets(id),
    bot_name                TEXT NOT NULL,
    bo3gg_id                BIGINT,
    team1                   TEXT,
    team2                   TEXT,
    market                  TEXT NOT NULL,        -- '1x2' for CS2 moneyline
    selection               TEXT NOT NULL,        -- 'team1' / 'team2'
    bookmaker               TEXT NOT NULL DEFAULT 'coolbet',

    -- Odds capture
    bot_odds_at_pick        NUMERIC(8,3),         -- snapshot from cs2_simulated_bets.odds_at_pick
    captured_odds           NUMERIC(8,3),         -- Coolbet odds at placement
    slippage_pct            NUMERIC(8,4),         -- (captured - bot) / bot
    edge_pct_taken          NUMERIC(8,4),         -- modeled edge at the captured odds

    -- Placement
    paper                   BOOLEAN NOT NULL DEFAULT TRUE,
    stake_eur               NUMERIC(8,2),
    placed_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticket_id               TEXT,                 -- Coolbet bet slip ID when paper=false

    -- Settlement (filled when match finishes)
    result                  TEXT,                 -- 'won' | 'lost' | 'void'
    pnl_eur                 NUMERIC(8,2),
    resolved_at             TIMESTAMPTZ,
    clv_pinnacle            NUMERIC(8,4),         -- post-kickoff Pinnacle close vs captured

    notes                   TEXT,

    UNIQUE (cs2_simulated_bet_id)                 -- one real_bet per sim
);

CREATE INDEX IF NOT EXISTS idx_cs2_real_bets_pending
    ON cs2_real_bets (placed_at DESC) WHERE result IS NULL;

CREATE INDEX IF NOT EXISTS idx_cs2_real_bets_bot_name
    ON cs2_real_bets (bot_name, placed_at DESC);

CREATE INDEX IF NOT EXISTS idx_cs2_real_bets_bo3gg
    ON cs2_real_bets (bo3gg_id);
