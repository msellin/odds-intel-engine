-- 354_picks_board_watchlist.sql
-- PICKS-BOARD-WATCHLIST (2026-09-15)
--
-- WHY. The pre-registered rule publishes only legs clearing a 3% expected-ROI
-- floor. On 2026-09-15 that was ZERO of 31 candidate legs, and the owner's
-- requirement is "offer at least something to users every day". The honest way
-- to do that is NOT to lower the floor -- it is to show the board and the price
-- each leg would need, and let the reader shop.
--
-- The arithmetic is exact and claims nothing: edge = p_sharp * odds - 1, so the
-- price needed for any target edge is (1 + target) / p_sharp. "Worth taking at
-- 2.18 or better" is a statement about the sharp line, not a prediction.
--
-- ⚠️ THIS TABLE IS NOT THE LEDGER, AND MUST NEVER BECOME IT.
-- `picks_forward_test` is a pre-registered test: its rows are the published set,
-- they are graded, and its n feeds stopping rules at 200/400/800. A watchlist
-- row is a leg that did NOT qualify. Pooling the two would inflate n with bets
-- nobody was told to take -- the precise discipline failure the pre-registration
-- exists to prevent. Hence a separate table, a separate view, and no `arm`
-- column to tempt anyone into a UNION.
--
-- Rows are REPLACED each publisher run (every 30 min), keyed on the leg, so the
-- table holds "the board as it stands now" and never accumulates history. It is
-- a display surface, not a record. Nothing is settled here and nothing is
-- counted here.

CREATE TABLE IF NOT EXISTS picks_board (
    match_id      UUID        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market        TEXT        NOT NULL,
    selection     TEXT        NOT NULL,
    odds          NUMERIC     NOT NULL,
    bookmaker     TEXT        NOT NULL,
    p_sharp       NUMERIC     NOT NULL,
    edge          NUMERIC     NOT NULL,
    -- Price needed to reach each grade. Stored rather than computed in the page
    -- so one definition serves the site, the API and any future surface.
    odds_breakeven NUMERIC    NOT NULL,
    odds_grade_b  NUMERIC     NOT NULL,
    odds_grade_a  NUMERIC     NOT NULL,
    anchor_overround NUMERIC,
    kickoff_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (match_id, market, selection)
);

CREATE INDEX IF NOT EXISTS idx_picks_board_kickoff ON picks_board (kickoff_at);

COMMENT ON TABLE picks_board IS
    'Live candidate board for /picks, replaced every publisher run. NOT the '
    'pre-registered ledger -- see picks_forward_test. Never pool the two: a row '
    'here did not qualify, and counting it would inflate the test''s n with bets '
    'nobody was told to take.';

CREATE OR REPLACE VIEW picks_board_public AS
SELECT b.match_id, b.market, b.selection, b.odds, b.bookmaker, b.p_sharp,
       b.edge, b.odds_breakeven, b.odds_grade_b, b.odds_grade_a,
       b.anchor_overround, b.updated_at,
       m.date  AS kickoff_utc,
       l.name  AS league,
       l.country AS country,
       ht.name AS home_team,
       at.name AS away_team
  FROM picks_board b
  JOIN matches m    ON m.id  = b.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id
 WHERE m.date > NOW();

GRANT SELECT ON picks_board_public TO anon;
GRANT SELECT ON picks_board_public TO authenticated;

-- POSTGREST-SCHEMA-RELOAD: PostgREST caches the schema, so a new view 404s
-- until a reload is signalled. This has bitten twice (migrations 278, 310).
NOTIFY pgrst, 'reload schema';
