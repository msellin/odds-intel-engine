-- CS2 Total Maps O/U 2.5 market + 3 market-specialist bots (2026-06-25).
--
-- Today's audit: 12 fires across 3 bots, ALL on match_winner. The atleast1map
-- and clean_sweep markets are configured per bot but rarely fire — Coolbet's
-- map markets are scoped to ~11% of CS2 fixtures (the bo3.gg-sourced subset
-- with Coolbet coverage), so when match_winner data is available the bot
-- picks that and the map markets get skipped. New market-specialist bots
-- carve out atleast1map / clean_sweep / total_maps from the all-markets
-- aggressive bots so they generate dedicated dry-power for those markets.
--
-- Total Maps O/U 2.5 is a Coolbet market on BO3 fixtures:
--   Over 2.5 = decider played (final score 2-1 or 1-2)
--   Under 2.5 = clean sweep (2-0 or 0-2)
-- Model probability derived on-the-fly from match-winner prob:
--   P(over 2.5 | BO3) = 2 * p1 * (1 - p1)   (i.i.d. proxy)
--   P(under 2.5 | BO3) = 1 - 2 * p1 * (1 - p1)
-- Orthogonal-ish to match_winner — peaks at p=0.5 (close matches → decider
-- more likely), troughs at p=0/1 (lopsided → sweep more likely).

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS coolbet_odds_total_o25 NUMERIC,
    ADD COLUMN IF NOT EXISTS coolbet_odds_total_u25 NUMERIC;

COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_total_o25 IS
    'Coolbet odds for total maps OVER 2.5 (BO3 decider played). NULL on '
    'BO1/BO5 — market only defined for BO3. Populated by cs2_coolbet_scanner '
    'since CS2-TOTAL-MAPS 2026-06-25.';
COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_total_u25 IS
    'Coolbet odds for total maps UNDER 2.5 (BO3 clean sweep). See _o25.';

-- Seed the 3 new market-specialist bots. Starting bankroll 1000.00 EUR
-- (same convention as the other cs2_* bots; CS2-FLAT-STAKE backfills any
-- subsequent settlements via the standard pnl_eur path).
INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES
  ('bot_cs2_a1m_specialist_v1',
   'CS2 atleast1map specialist — +1.5 map handicap only, 3% edge floor',
   'Fires only on atleast1map picks (team wins ≥1 map in BO3). Sources elo+pq_v1 + v8. Lower edge floor (3%) than canonical because Coolbet handicap markets are softer than 1X2 — operator can extract value at thinner margins.',
   1000.00, 1000.00, TRUE),
  ('bot_cs2_clean_sweep_v1',
   'CS2 clean-sweep specialist — -1.5 map handicap only, 4% edge floor',
   'Fires only on clean_sweep picks (team wins 2-0 / 3-0). Sources elo+pq_v1 + v8. Higher floor (4%) than a1m — clean-sweep is high-variance (model needs strong conviction on the favorite). Orthogonal to MW since the same MW prob can imply very different clean-sweep edge.',
   1000.00, 1000.00, TRUE),
  ('bot_cs2_total_maps_v1',
   'CS2 total maps O/U 2.5 specialist — BO3 decider-played market',
   'Fires on total_maps_o25 (BO3 only). Sources elo+pq_v1 + v8, 5% edge floor. P(over 2.5) = 2*p*(1-p) where p = win_prob1 — peaks at p=0.5 (close matches → decider likely), troughs at p=0/1 (lopsided → sweep). Genuinely orthogonal to match_winner.',
   1000.00, 1000.00, TRUE)
ON CONFLICT (name) DO NOTHING;
