-- DIRECT-BOOK-CLV-SHADOW-BACKFILL-2026-09-18 — prerequisite.
--
-- WHY THIS COMES BEFORE THE BACKFILL
-- ----------------------------------
-- The backfill recomputes `clv` against the pick's OWN book instead of whichever
-- book happened to close. That is strictly more honest. But the shadow settle
-- path has NO freshness bound — unlike real_bets, which caps the close at
-- DIRECT_CLOSE_MAX_MIN = 60 (migration 332) — so it will accept the newest
-- pre-kickoff snapshot at that book from ANY distance before kick.
--
-- Measured on the 106,726 affected rows before writing anything:
--
--     age of the resolved "close"   rows     recomputed CLV   lands on exactly 0
--     <= 20 min before KO          52,014           +2.57%              39.1%
--     21-60 min                     6,090           +4.52%              49.5%
--     1-3 h                         3,156           +3.59%              55.0%
--     3-12 h                       13,579           +1.29%              80.5%
--     > 12 h                       26,942           +1.64%              72.8%
--
-- 40,521 rows (40% of the recoverable set) resolve to a price more than three
-- hours before kickoff, and in roughly three of four of those the "close" IS the
-- pick's own snapshot — so CLV is 0 by construction, not by measurement.
--
-- Without this column the backfill would trade a KNOWN bias (+3.05pp from the
-- arbitrary-book substitution) for an UNMEASURABLE one (a number diluted toward
-- zero on 40% of rows, indistinguishable from a real result). Record the age so
-- a reader can filter instead of guessing.
ALTER TABLE shadow_bets ADD COLUMN IF NOT EXISTS closing_minutes_before_ko integer;

COMMENT ON COLUMN shadow_bets.closing_minutes_before_ko IS
  'DIRECT-BOOK-CLV-SHADOW-BACKFILL-2026-09-18. Minutes between the snapshot used '
  'as the closing price and kickoff. The shadow path has no DIRECT_CLOSE_MAX_MIN '
  'bound (real_bets does, migration 332), so ~40% of historical closes are >3h '
  'pre-kickoff and ~75% of those yield clv=0 by construction. Filter on this '
  'before reading clv as a closing-line number. NULL means the age was not '
  'recorded, not that the close was fresh.';

-- Same gap on the PUBLIC ledger. simulated_bets is the table behind /performance
-- and /api/v1/track-record, and 3,124 of its 3,124 CLV rows carry no
-- closing_bookmaker at all — 100% affected, against 66% on shadow_bets.
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS closing_minutes_before_ko integer;

COMMENT ON COLUMN simulated_bets.closing_minutes_before_ko IS
  'SIMULATED-CLV-OWN-BOOK-2026-09-14 / DIRECT-BOOK-CLV-SHADOW-BACKFILL. As on '
  'shadow_bets. This is the PUBLIC ledger: /api/v1/track-record emits per-bet clv '
  'from it, so an unlabelled stale close here is published, not just internal.';
