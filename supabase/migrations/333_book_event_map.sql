-- NEAR-KICKOFF-CAPTURE (2026-09-11) — persist which book event is which fixture.
--
-- WHY. Every Coolbet / Epicbet / Unibet-Site sweep walks the book's whole board,
-- fuzzy-matches each event to a DB fixture, uses the pairing once and throws it
-- away. So no per-fixture fetch was possible: asking a book about ONE match meant
-- redoing the entire walk. That is why our direct books almost never have a true
-- closing price — the sweeps run every 30 min (Coolbet's evening sweep takes
-- 60-75 min end to end), so the last price before kickoff was typically 1-5h old.
-- Measured over 3 days to 2026-09-11: is_closing rows covered 23 matches at
-- Coolbet, 82 at Epicbet, 139 at Unibet-Site — against 539 at Pinnacle.
--
-- With the pairing stored, `workers/jobs/near_kickoff_capture.py` fetches just
-- the fixtures kicking off in the next 15 minutes, straight by event id. That is
-- what real_bets.clv (migration 332) needs to measure a true own-book close.
--
-- One row per (match, book). Sweeps upsert it every pass, so `matched_at` is the
-- last time the pairing was confirmed and a re-listed event id overwrites the old.
-- `match_score` is the matcher's confidence when the matcher exposes one
-- (Coolbet does; Epicbet/Unibet's fuzzy_match_event returns only the event), so
-- a wrong pairing is diagnosable after the fact instead of re-derived.

CREATE TABLE IF NOT EXISTS book_event_map (
    match_id      uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker     text        NOT NULL,
    book_event_id text        NOT NULL,
    book_start    timestamptz,
    match_score   real,
    matched_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, bookmaker)
);

CREATE INDEX IF NOT EXISTS book_event_map_bookmaker_idx
    ON book_event_map (bookmaker, match_id);

COMMENT ON TABLE book_event_map IS
    'AF fixture <-> direct-book event id, written by the Coolbet/Epicbet/Unibet-Site sweeps (NEAR-KICKOFF-CAPTURE 2026-09-11). Read by near_kickoff_capture to fetch one fixture by id.';
