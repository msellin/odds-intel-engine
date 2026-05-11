-- OU-MARKET-FEATURES Phase B: add 4 market-signal columns to match_feature_vectors.
-- pinnacle_implied_over25/under25 — Pinnacle pre-KO OU 2.5 implied probs (overround-guarded).
-- ou25_bookmaker_disagreement    — max-min implied_over25 across distinct books (blacklist-filtered).
-- market_implied_btts_yes        — avg 1/yes_odds across distinct bookmakers for BTTS.
ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS pinnacle_implied_over25       numeric(5,4),
    ADD COLUMN IF NOT EXISTS pinnacle_implied_under25      numeric(5,4),
    ADD COLUMN IF NOT EXISTS ou25_bookmaker_disagreement   numeric(5,4),
    ADD COLUMN IF NOT EXISTS market_implied_btts_yes       numeric(5,4);
