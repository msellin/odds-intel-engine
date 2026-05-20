-- COOLBET-INPLAY-SNAPSHOTS (2026-05-20) — measures slippage between an
-- inplay bot's decision point and what Coolbet's live markets offer at that
-- moment. Driven by Postgres LISTEN/NOTIFY: every simulated_bets INSERT with
-- xg_source IS NOT NULL fires a notification, the coolbet_daemon listener
-- thread does ONE Coolbet GET (live markets+odds for that match) and writes
-- a snapshot row.
--
-- This is the analytical foundation for deciding whether inplay alpha
-- survives Coolbet odds collapse. Mode A (capture-only) writes only this
-- table; modes B (paper-trade) and C (real-money execute) additionally
-- create real_bets rows downstream.

CREATE TABLE IF NOT EXISTS coolbet_inplay_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulated_bet_id      UUID NOT NULL REFERENCES simulated_bets(id) ON DELETE CASCADE,
    decision_pick_time    TIMESTAMPTZ NOT NULL,         -- copy of simulated_bets.pick_time
    captured_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms            INTEGER NOT NULL,             -- gap between decision and our Coolbet GET
    model_odds            DECIMAL(8,3),                 -- simulated_bets.odds_at_pick at decision
    coolbet_odds          DECIMAL(8,3),                 -- live Coolbet odds at GET time (NULL if no_match/api_error)
    coolbet_match_id      BIGINT,
    coolbet_market_id     BIGINT,
    coolbet_outcome_id    BIGINT,
    capture_outcome       TEXT NOT NULL CHECK (capture_outcome IN (
                              'captured',           -- successful GET + outcome resolved
                              'no_match',           -- couldn't find the match on Coolbet
                              'no_market',          -- match found but market/selection not exposed live
                              'odds_drop_too_large',-- odds moved beyond tolerance during capture
                              'api_error'           -- Coolbet GET failed (4xx/5xx/timeout)
                          )),
    error                 TEXT,
    inplay_mode           TEXT NOT NULL DEFAULT 'capture' CHECK (inplay_mode IN ('capture', 'paper', 'execute')),
    -- Linked real_bets row for modes B/C (NULL in mode A); placeholder until those modes ship
    real_bet_id           UUID REFERENCES real_bets(id),
    UNIQUE (simulated_bet_id)
);

CREATE INDEX IF NOT EXISTS idx_coolbet_inplay_snapshots_captured_at
    ON coolbet_inplay_snapshots (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_coolbet_inplay_snapshots_outcome
    ON coolbet_inplay_snapshots (capture_outcome, captured_at DESC);

COMMENT ON TABLE coolbet_inplay_snapshots IS
    'One row per inplay-bot decision. Captures Coolbet odds at the moment of '
    'the decision so we can measure inplay alpha vs slippage. Driven by '
    'LISTEN inplay_bet_fired in coolbet_daemon.';
COMMENT ON COLUMN coolbet_inplay_snapshots.latency_ms IS
    'Wall-clock ms from simulated_bets.pick_time to captured_at. Dominated '
    'by Coolbet GET response time (~300-2000ms) plus our trigger+listener '
    'overhead (~50ms).';
COMMENT ON COLUMN coolbet_inplay_snapshots.capture_outcome IS
    'captured = GET succeeded AND we resolved the outcome_id; no_match = no '
    'event found on Coolbet live; no_market = event found but the bot''s '
    'market/selection is not exposed in live markets; api_error = GET '
    'failed (auth, network, or 4xx/5xx).';
COMMENT ON COLUMN coolbet_inplay_snapshots.inplay_mode IS
    'Which mode the daemon was running when this snapshot was captured. '
    'capture (mode A) = snapshot only; paper (mode B) = snapshot + '
    'real_bets row with notes=''inplay paper''; execute (mode C) = '
    'snapshot + real_bets row + actual POST to /s/bets/bets.';

-- ── Postgres LISTEN/NOTIFY trigger ────────────────────────────────────────
-- Fires on every simulated_bets INSERT where xg_source IS NOT NULL (= the
-- bot is an inplay strategy). The coolbet_daemon's bg listener thread
-- picks up the payload and does the Coolbet GET. No polling on either
-- side: pure event-driven.

CREATE OR REPLACE FUNCTION notify_inplay_bet_fired() RETURNS TRIGGER AS $$
BEGIN
    -- Only inplay decisions trigger this. Prematch bots (xg_source IS NULL)
    -- are unaffected — they use the normal coolbet daemon placement loop.
    IF NEW.xg_source IS NOT NULL THEN
        PERFORM pg_notify(
            'inplay_bet_fired',
            json_build_object(
                'bet_id',       NEW.id::text,
                'match_id',     NEW.match_id::text,
                'market',       NEW.market,
                'selection',    NEW.selection,
                'odds_at_pick', NEW.odds_at_pick,
                'pick_time',    NEW.pick_time
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_notify_inplay_bet_fired ON simulated_bets;
CREATE TRIGGER trigger_notify_inplay_bet_fired
AFTER INSERT ON simulated_bets
FOR EACH ROW EXECUTE FUNCTION notify_inplay_bet_fired();

COMMENT ON FUNCTION notify_inplay_bet_fired() IS
    'AFTER INSERT trigger on simulated_bets. NOTIFYs ''inplay_bet_fired'' '
    'with bet metadata when xg_source IS NOT NULL (inplay decision). '
    'coolbet_daemon LISTENs on this channel for event-driven snapshot capture.';
