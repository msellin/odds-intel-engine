-- 357_inplay_book_quotes_and_slowstate_bots.sql
-- OWN Phase 1b — the in-play slow-state rig (2026-09-15)
-- dev/active/own-implementation-plan.md §Phase 1b; workers/jobs/inplay_collector.py
--
-- WHY. `odds_snapshots` holds ZERO in-play rows for any book we can place at;
-- the only in-play history is API-Football's unattributed aggregate, a median
-- 40 s stale (INPLAY_BOOK_COMPARISON). Every in-play strategy tested on it came
-- back at or below the vig, and the one structural finding — the long side
-- carries ~14% relative margin against ~4% on the favourite — says a signal, if
-- one exists, can only survive through a SHORT price. Measuring that needs the
-- book's ON-SCREEN price at decision time and the final score. This table is the
-- board; the two bots are the measurement.
--
-- WHAT.
--   * `inplay_book_quotes` — one row per (fixture, instant, book), markets nested
--     as JSONB (a wide row per selection would be ~40x the volume). `af_age_s`
--     stored so no reader has to re-derive whether the state feed was fresh.
--     Pruned to 90 days by the VPS scheduler.
--   * `shadow_bets.inplay_minute / inplay_score_home / inplay_score_away` — the
--     game state at the pick, so the eval can split by window.
--   * Two EXPERIMENTAL paper bots: the live arm (Epicbet on-screen price) and the
--     control arm (same trigger, AF aggregate price at the same instant). Two
--     bots because `shadow_bets_unique` de-duplicates on (bot, match, market,
--     selection) and would otherwise collapse the arms. Neither is placeable.
--
-- Re-appliable.

CREATE TABLE IF NOT EXISTS inplay_book_quotes (
    id             BIGSERIAL PRIMARY KEY,
    captured_at    TIMESTAMPTZ NOT NULL,
    book           TEXT NOT NULL,
    book_event_id  TEXT,
    af_fixture_id  TEXT,
    match_id       UUID REFERENCES matches(id) ON DELETE SET NULL,
    league         TEXT,
    home_team      TEXT,
    away_team      TEXT,
    minute         INTEGER,
    seconds        INTEGER,
    score_home     INTEGER,
    score_away     INTEGER,
    af_age_s       NUMERIC,
    markets        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS inplay_book_quotes_captured_idx ON inplay_book_quotes (captured_at);
CREATE INDEX IF NOT EXISTS inplay_book_quotes_match_idx ON inplay_book_quotes (match_id, captured_at);
COMMENT ON TABLE inplay_book_quotes IS
    'In-play board of a placeable book, one row per (fixture, instant). markets = [{fam, line, sel:[{sel, odds, suspended}]}]. '
    'Written by workers/jobs/inplay_collector.py (Mac launchd KeepAlive). 90-day retention. OWN Phase 1b, 2026-09-15.';

ALTER TABLE shadow_bets
    ADD COLUMN IF NOT EXISTS inplay_minute     INTEGER,
    ADD COLUMN IF NOT EXISTS inplay_score_home INTEGER,
    ADD COLUMN IF NOT EXISTS inplay_score_away INTEGER;

INSERT INTO bots (name, description, strategy, is_active, maturity_label,
                  starting_bankroll, current_bankroll, created_at)
SELECT 'bot_inplay_slowstate_v1',
       'PAPER, never placeable (OWN Phase 1b, 2026-09-15). In-play slow-state rig at Epicbet: '
       'T1 = 0-0 at 35-54 min -> back UNDER 2.5 at <= 2.20; T2 = two-goal lead at 70-89 min -> '
       'back the leader (1x2) at <= 2.20. Priced at the book ON-SCREEN price at decision. '
       'Primary metric = realised hit-rate minus the book de-vigged implied prob on the '
       'selected set (CLV inadmissible in play). STOP at n=1000 if lift < 0; decide at n=3000.',
       'inplay_slowstate', TRUE, 'experimental', 1000, 1000, now()
WHERE NOT EXISTS (SELECT 1 FROM bots WHERE name = 'bot_inplay_slowstate_v1');

INSERT INTO bots (name, description, strategy, is_active, maturity_label,
                  starting_bankroll, current_bankroll, created_at)
SELECT 'bot_inplay_slowstate_afctl_v1',
       'PAPER CONTROL ARM for bot_inplay_slowstate_v1 (OWN Phase 1b, 2026-09-15): the same two '
       'triggers priced off API-Football live aggregate at the same instant. The gap between '
       'the arms is the value of the fresh board. Never a strategy on its own.',
       'inplay_slowstate_control', TRUE, 'experimental', 1000, 1000, now()
WHERE NOT EXISTS (SELECT 1 FROM bots WHERE name = 'bot_inplay_slowstate_afctl_v1');
