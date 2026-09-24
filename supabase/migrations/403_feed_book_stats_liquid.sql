-- 403_feed_book_stats_liquid.sql — Betfair-Exchange liquidity on /admin/feeds (2026-09-24)
--
-- The exchange stores EVERY listed market in exchange_quotes, thin placeholders included
-- (1.10 back / 110 lay, EUR 0 matched), and consumers decide what counts as a price
-- (betfair_exchange_feed.is_liquid: spread <= 5% on every runner AND >= EUR 1,000 matched).
-- The feeds card showed "65/91 fixtures priced" — i.e. LISTED — when 9 were usable. These
-- two columns carry the usable count; NULL for books where every stored price is a price.
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS liquid_today     integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS liquid_yesterday integer;
COMMENT ON COLUMN feed_book_stats.liquid_today IS
    'Exchange only: our fixtures today whose latest 1X2 is liquid (spread <= 5% every runner, >= EUR 1k matched).';
