-- Normalize market column to lowercase in both bet tables
UPDATE simulated_bets SET market = LOWER(market) WHERE market != LOWER(market);
UPDATE shadow_bets SET market = LOWER(market) WHERE market != LOWER(market);
