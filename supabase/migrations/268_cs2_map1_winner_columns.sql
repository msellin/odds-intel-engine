-- CS2-MAP1-WINNER (2026-07-02): map 1 winner market columns on cs2_upcoming_matches.
-- Scanner writes coolbet odds; ELO enrichment writes fair odds + veto_map1.
ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS veto_map1          TEXT,
    ADD COLUMN IF NOT EXISTS fair_odds_m1w1     FLOAT,
    ADD COLUMN IF NOT EXISTS fair_odds_m1w2     FLOAT,
    ADD COLUMN IF NOT EXISTS coolbet_odds_m1w1  FLOAT,
    ADD COLUMN IF NOT EXISTS coolbet_odds_m1w2  FLOAT;

-- Specialist bot for Map 1 Winner market.
INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES (
  'bot_cs2_map1_winner_v1',
  'CS2 Map 1 Winner specialist — veto + map win-rate, 3% edge floor',
  'Fires only on map1_winner picks. Fair odds from 65% map-specific win% + 35% ELO blend. Sources elo+pq_v1 + v8. BO3+ only — requires veto_map1 to be resolved.',
  1000.00, 1000.00, TRUE
) ON CONFLICT (name) DO NOTHING;
