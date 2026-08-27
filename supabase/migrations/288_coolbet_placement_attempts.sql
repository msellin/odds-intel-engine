-- COOLBET-UI-PLACER-2026-08-27 — full audit trail for UI-driven placement.
--
-- real_bets only records bets that succeeded. That is the wrong shape for
-- auditing an automated placer: the interesting cases are the ones that did
-- NOT place — the pick we could not find on Coolbet, the market that had no
-- price, the stake the keypad silently dropped, the odds that drifted below
-- our floor. Without those rows a placer that quietly places nothing looks
-- identical to one with no picks to place ([[feedback_silent_failures]] — the
-- InplayBot UUID bug hid behind "0 bets" looking normal for 11 days).
--
-- So: one row per ATTEMPT, always, whatever the outcome. `stage` says how far
-- it got and `reason` says why it stopped. `coolbet_odds` is NULL exactly when
-- we never got a price, and `reason` explains that rather than leaving a hole.

CREATE TABLE IF NOT EXISTS coolbet_placement_attempts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- what we were trying to place
    bot_id            UUID REFERENCES bots(id),
    bot_name          TEXT,
    shadow_bet_id     UUID,
    simulated_bet_id  UUID,
    match_id          UUID,
    home_team         TEXT,
    away_team         TEXT,
    kickoff           TIMESTAMPTZ,
    market            TEXT NOT NULL,
    selection         TEXT NOT NULL,

    -- prices: captured is what the pick carried, coolbet_odds is what we
    -- actually saw on the page. NULL coolbet_odds = we never got that far.
    captured_odds     NUMERIC(8,3),
    coolbet_odds      NUMERIC(8,3),
    odds_drift_pct    NUMERIC(8,3),

    -- stake_applied is read BACK from the field, never assumed
    stake_requested   NUMERIC(10,2),
    stake_applied     NUMERIC(10,2),

    -- Coolbet-side identifiers, for replaying an attempt by hand
    coolbet_match_id  TEXT,
    coolbet_market_id TEXT,

    -- how it ended
    outcome           TEXT NOT NULL CHECK (outcome IN ('placed','staged','rejected','error')),
    stage             TEXT,   -- login|search|match|open|price|outcome|drift|stake|slip|place
    reason            TEXT,   -- required for anything that is not 'placed'
    slip_text         TEXT,
    ticket_id         TEXT,
    real_bet_id       UUID REFERENCES real_bets(id),
    execute_mode      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_cpa_attempted_at ON coolbet_placement_attempts (attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_cpa_bot          ON coolbet_placement_attempts (bot_id, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_cpa_outcome      ON coolbet_placement_attempts (outcome, stage);
CREATE INDEX IF NOT EXISTS idx_cpa_match        ON coolbet_placement_attempts (match_id);

COMMENT ON TABLE coolbet_placement_attempts IS
  'One row per UI placement attempt, successful or not. stage+reason explain '
  'every non-placement so a silently-placing-nothing placer is visible.';
COMMENT ON COLUMN coolbet_placement_attempts.coolbet_odds IS
  'Price actually seen on the Coolbet page. NULL means we never reached a '
  'price — reason says why (not found, no market, page error).';
COMMENT ON COLUMN coolbet_placement_attempts.stake_applied IS
  'Stake read BACK from the field. The stake input is React-controlled behind '
  'an on-screen keypad and can silently drop a fill().';
