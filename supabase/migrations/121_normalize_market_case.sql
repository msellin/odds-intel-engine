-- Normalize market column to lowercase in both bet tables
UPDATE bets SET market = LOWER(market) WHERE market != LOWER(market);
UPDATE live_bets SET market = LOWER(market) WHERE market != LOWER(market);
