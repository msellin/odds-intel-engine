-- 385 — BOOK LIVE STATS + BOOK MATCH RESULTS ([[#101]] Tonybet phase 2, 2026-09-23)
--
-- Owner: "parse everything we can and even more than AF … start creating our own
-- data sources instead of relying on paid subscriptions."
--
-- API-Football has fixture statistics for only 28% of the matches our Estonian books
-- price (docs/BOOK_DATA_FOR_MODELLING_2026_09_23.md). Tonybet's live feed carries
-- score, clock, status, corners and cards for ~87% of live football, from Sportradar,
-- and its results carry FT + per-period scores. Two constraints shape the tables:
--   * Tonybet PURGES results ~1–2 days after kickoff, and clears post-match
--     statistics at full time — so live snapshots are the only durable record of
--     final corners/cards, and results must be captured within 24 h.
--   * We keep EVERY live match, matched to our fixtures or not. An unmatched match is
--     still data we own; `match_id` is filled when book_event_map knows it.

-- One row per poll per live event (append). ~45 live events × 720 polls/day.
CREATE TABLE IF NOT EXISTS book_live_stats (
    id               bigserial   PRIMARY KEY,
    bookmaker        text        NOT NULL,
    book_event_id    text        NOT NULL,
    sr_match_id      text,                    -- Sportradar id, cross-book key
    match_id         uuid        REFERENCES matches(id) ON DELETE SET NULL,
    captured_at      timestamptz NOT NULL DEFAULT now(),
    match_status_id  integer,                 -- Sportradar status (6 = 1st half, 7 = 2nd, 31 = HT, 100 = ended)
    clock            text,                    -- "29:06"
    score_home       smallint,
    score_away       smallint,
    corners_home     smallint,
    corners_away     smallint,
    yellows_home     smallint,
    yellows_away     smallint,
    reds_home        smallint,
    reds_away        smallint,
    yellow_reds_home smallint,
    yellow_reds_away smallint,
    coverage_source  text                     -- 'venue' / 'tv' (additionalInfo)
);
CREATE INDEX IF NOT EXISTS book_live_stats_event ON book_live_stats (bookmaker, book_event_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS book_live_stats_match ON book_live_stats (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS book_live_stats_captured ON book_live_stats (captured_at);

-- One row per finished event (upsert). Final corners/cards fall back to the last
-- live snapshot when the results feed has already cleared them.
CREATE TABLE IF NOT EXISTS book_match_results (
    bookmaker        text        NOT NULL,
    book_event_id    text        NOT NULL,
    sr_match_id      text,
    match_id         uuid        REFERENCES matches(id) ON DELETE SET NULL,
    kickoff          timestamptz,
    match_status_id  integer,                 -- 100 ended, 110 AET, 120 AP, 60 postponed, 70 cancelled, 90 abandoned
    ft_home          smallint,
    ft_away          smallint,
    ht_home          smallint,
    ht_away          smallint,
    h2_home          smallint,
    h2_away          smallint,
    corners_home     smallint,
    corners_away     smallint,
    yellows_home     smallint,
    yellows_away     smallint,
    reds_home        smallint,
    reds_away        smallint,
    stats_source     text,                    -- 'results_feed' | 'last_live_snapshot' | NULL
    periods          jsonb,                   -- raw per-period scores, incl. extra time
    captured_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bookmaker, book_event_id)
);
CREATE INDEX IF NOT EXISTS book_match_results_match ON book_match_results (match_id) WHERE match_id IS NOT NULL;
