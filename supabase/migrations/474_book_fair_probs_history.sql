-- 474 — [[#154]] idea 3 / [[#144]] (6): keep a small HISTORY of Tonybet's (Sportradar) fair probabilities.
-- Owner 2026-09-26: "yes turn on tonybet fair history". book_fair_probs (migration 383) keeps the LATEST value
-- only (= the fair close), so an anchor test cannot align Tonybet with Pinnacle / the exchange at the same
-- instant before kick-off. This table stores the FIRST value seen in each checkpoint bucket — 'open' (first
-- sighting), 't24h' (<= 24 h to kick-off), 't3h' (<= 3 h), 't1h' (<= 1 h) — at most 4 rows per line, not an
-- odds_snapshots-sized stream. minutes_to_kickoff records exactly when each was taken. The close stays in
-- book_fair_probs. Written by workers/automation/tonybet_feed.store_fair_probs. Private.
SET lock_timeout = '3s';
CREATE TABLE IF NOT EXISTS public.book_fair_probs_history (
    match_id           uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker          text        NOT NULL,
    market             text        NOT NULL,
    selection          text        NOT NULL,
    handicap_line      numeric,
    checkpoint         text        NOT NULL CHECK (checkpoint IN ('open', 't24h', 't3h', 't1h')),
    fair_prob          numeric     NOT NULL CHECK (fair_prob > 0 AND fair_prob < 1),
    odds               numeric,
    minutes_to_kickoff integer,
    captured_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS book_fair_probs_history_key
    ON public.book_fair_probs_history (match_id, bookmaker, market, selection, COALESCE(handicap_line, -9999), checkpoint);
REVOKE ALL ON public.book_fair_probs_history FROM anon, authenticated;
