-- 383 — BOOK FAIR PROBABILITIES ([[#101]] Tonybet sweeper, 2026-09-23)
--
-- Tonybet's API ships, on every outcome, the odds supplier's own margin-free
-- probability (Sportradar UOF `probabilities`; the two sides of a line sum to 1.000).
-- AF gives nothing like it: it is a professional fair price per market — including
-- markets Pinnacle does not price — and it makes the book's own margin exact
-- (odds × fair_prob − 1) with no de-vig assumption.
--
-- LATEST VALUE, NOT A HISTORY. One row per (match, book, market, selection, line),
-- upserted each sweep. Because sweeps stop at kickoff, the surviving value IS the
-- fair close — the number an anchor test needs — without adding another
-- odds_snapshots-sized append stream (that table is already the largest in the DB).
--
-- It is the SUPPLIER's line, not a sharp market. Test it like any anchor
-- (docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md) before any gate or pick reads it.

CREATE TABLE IF NOT EXISTS book_fair_probs (
    match_id           uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker          text        NOT NULL,
    market             text        NOT NULL,
    selection          text        NOT NULL,
    handicap_line      numeric,
    fair_prob          numeric     NOT NULL CHECK (fair_prob > 0 AND fair_prob < 1),
    odds               numeric,                 -- the book's price at the same instant
    minutes_to_kickoff integer,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- NULL handicap_line must not create duplicates, so key on a coalesced value.
CREATE UNIQUE INDEX IF NOT EXISTS book_fair_probs_key
    ON book_fair_probs (match_id, bookmaker, market, selection, COALESCE(handicap_line, -9999));

CREATE INDEX IF NOT EXISTS book_fair_probs_updated ON book_fair_probs (updated_at);
