-- Add bot_cs2_value_v1 + bot_cs2_hltv_v1 to the bots table so CS2 picks
-- are bankroll-tracked the same way as soccer bots. €1000 starting bankroll
-- matches the soccer convention.

INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES
    ('bot_cs2_value_v1',
     'CS2 value (ELO+PQ + consensus + roster gate)',
     'Half-Kelly sized, fires when bookie ≥ threshold with ≥2 books consensus and no roster change',
     1000.00, 1000.00, TRUE),
    ('bot_cs2_hltv_v1',
     'CS2 HLTV-rank fallback',
     'Fires when ELO+PQ has no coverage but HLTV rank model finds an edge; half-Kelly sized',
     1000.00, 1000.00, TRUE)
ON CONFLICT (name) DO NOTHING;

-- Bankroll-aware stake in euros (separate from the existing "stake" column
-- which is in unit terms — keep both for analysis continuity).
ALTER TABLE cs2_simulated_bets
    ADD COLUMN IF NOT EXISTS stake_eur     NUMERIC,
    ADD COLUMN IF NOT EXISTS bankroll_at_pick NUMERIC,
    ADD COLUMN IF NOT EXISTS pnl_eur       NUMERIC,
    ADD COLUMN IF NOT EXISTS roster_change_gate_skipped BOOLEAN DEFAULT FALSE;
