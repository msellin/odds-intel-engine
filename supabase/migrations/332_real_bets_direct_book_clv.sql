-- DIRECT-BOOK-CLV (2026-09-11) — real-bet CLV measured at the book we bet at.
--
-- WHY. `real_bets.clv` was `actual_odds / closing_odds - 1`, where closing_odds
-- came from `get_closing_odds()` with NO bookmaker filter — i.e. whichever of
-- the ~13 API-Football books sorted last at kickoff. Every real bet is placed
-- at a direct book (Coolbet today; Unibet-Site soon), so that number answered
-- "did we beat some arbitrary book's close" rather than "did OUR price beat
-- where OUR book closed" — the only question that tells us whether we bet too
-- early. Measured 2026-09-11 on 130 settled real bets: stored clv averaged
-- +4% to +17% by lead-time bucket, while the same bets against Coolbet's own
-- later price had a median of 0.0%.
--
-- WHAT CHANGES.
--   clv                        -> now vs the close AT THE BET'S OWN BOOK, and
--                                 only when that close is fresh (see below);
--                                 NULL otherwise. Never an arbitrary book.
--   closing_odds               -> the price clv was computed against.
--   closing_bookmaker          -> which odds_snapshots feed supplied it.
--   closing_minutes_before_ko  -> how old that "close" was at kickoff. A
--                                 Coolbet price 5h before kickoff is not a
--                                 close; consumers can tighten the bound.
--   clv_pinnacle               -> de-vigged Pinnacle CLV (actual_odds x
--                                 P_pinnacle_fair - 1), the same definition as
--                                 simulated_bets / shadow_bets.clv_pinnacle, so
--                                 real and paper bets are judged on one scale.
--
-- Freshness bound lives in workers/jobs/settlement.py (DIRECT_CLOSE_MAX_MIN).
-- Historical rows are recomputed by scripts/backfill_real_bets_direct_clv.py.

ALTER TABLE real_bets
    ADD COLUMN IF NOT EXISTS closing_odds numeric,
    ADD COLUMN IF NOT EXISTS closing_bookmaker text,
    ADD COLUMN IF NOT EXISTS closing_minutes_before_ko integer,
    ADD COLUMN IF NOT EXISTS clv_pinnacle numeric;

COMMENT ON COLUMN real_bets.clv IS
    'actual_odds / closing_odds - 1, where closing_odds is the last pre-kickoff price AT THE BET''S OWN BOOK within DIRECT_CLOSE_MAX_MIN of kickoff (DIRECT-BOOK-CLV 2026-09-11). NULL when that book has no fresh close. Before 2026-09-11 this was vs an arbitrary AF book.';
COMMENT ON COLUMN real_bets.closing_bookmaker IS
    'odds_snapshots.bookmaker that supplied closing_odds (Coolbet / Unibet-Site / Epicbet).';
COMMENT ON COLUMN real_bets.closing_minutes_before_ko IS
    'Minutes between the closing snapshot and kickoff. Lower = a truer close.';
COMMENT ON COLUMN real_bets.clv_pinnacle IS
    'De-vigged Pinnacle CLV: actual_odds x P(Pinnacle close, margin removed) - 1. Same definition as shadow_bets.clv_pinnacle.';
