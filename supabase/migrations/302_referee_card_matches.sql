-- 302_referee_card_matches.sql
-- REFEREE-CARDS-DENOMINATOR-2026-09-06
--
-- `referee_stats.cards_per_game` divided a PARTIAL numerator by a FULL
-- denominator. build_referee_stats summed yellows/reds only over the matches
-- that returned a `match_stats` row, then divided by `matches_total` — every
-- match the referee officiated. Card stats are sparse, so the result was a
-- systematic undercount:
--
--   mean cards_per_game        2.24    (a real figure is ~4.2)
--   referees reading 0.00     2,078 of 5,673
--   referees reading < 1.0    2,394 of 5,673
--
-- The zeros are referees none of whose matches carried a stats row: the
-- numerator was 0 and the denominator was their full match count, so the table
-- asserted "this referee shows no cards" where the truth was "we have no card
-- data for this referee". Those two are not the same claim, and the model could
-- not tell them apart because both arrived as 0.00.
--
-- This is live, not merely historical: `cards_per_game` feeds
-- get_referee_cards_avg() and therefore the model signal `referee_cards_avg`.
--
-- `card_matches` records how many matches the numerator was actually built
-- from, so the ratio has an honest denominator AND its coverage is visible to
-- anything reading the row. build_referee_stats now writes NULL for
-- cards_per_game when card_matches < 3, so "no data" stops masquerading as
-- "no cards".
--
-- Existing rows are left in place; the next build_referee_stats run overwrites
-- them. Backfilling here would duplicate that logic in SQL, which is how a
-- number comes to have two implementations.

ALTER TABLE referee_stats
    ADD COLUMN IF NOT EXISTS card_matches INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN referee_stats.card_matches IS
    'Matches contributing to yellow_total/red_total (i.e. having a match_stats '
    'row). The denominator for cards_per_game — NOT matches_total.';

COMMENT ON COLUMN referee_stats.cards_per_game IS
    'Mean cards over card_matches, not matches_total. NULL when card_matches < 3: '
    'absent card data must not be published as 0.00.';
