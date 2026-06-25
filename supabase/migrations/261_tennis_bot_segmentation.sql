-- TENNIS-PAPER-BETS Phase 2 (2026-06-25) — segment tennis_value_bets by bot persona.
--
-- Each scanner observation now routes through scripts/tennis/bots_config.py to
-- produce one row per (bot, fixture, bookmaker, selection, scan_date) tuple,
-- mirroring soccer's BOTS_CONFIG pattern. This makes per-bot ROI / hit-rate /
-- CLV queryable for free in Phase 3.
--
-- Existing rows (written before this migration) get bot_id='legacy_unsegmented'
-- so they don't pollute Phase-2-onward analytics but stay queryable separately.

ALTER TABLE tennis_value_bets
    ADD COLUMN IF NOT EXISTS bot_id text;

ALTER TABLE tennis_value_bets
    ADD COLUMN IF NOT EXISTS strategy_profile text;

UPDATE tennis_value_bets
   SET bot_id = 'legacy_unsegmented'
 WHERE bot_id IS NULL;

-- Drop the old unique index (fixture_id, bookmaker, selection, scan_date) and
-- recreate widened to include bot_id so the same (fixture, book, selection)
-- can land in multiple bot lanes per scan.
DROP INDEX IF EXISTS tennis_value_bets_unique;

CREATE UNIQUE INDEX IF NOT EXISTS tennis_value_bets_unique
    ON tennis_value_bets (fixture_id, bookmaker, selection, scan_date, bot_id);

CREATE INDEX IF NOT EXISTS tennis_value_bets_bot_id
    ON tennis_value_bets (bot_id);
