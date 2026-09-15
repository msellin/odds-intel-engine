-- 355_picks_board_tracking.sql
-- PICKS-BOARD-TRACKED (2026-09-15)
--
-- Migration 354 made `picks_board` a display table: replaced every run, no
-- history, nothing settled. The owner's design turns it into something better —
-- publish the leg AND the price it would need, then later split the record by
-- whether that price was ever available:
--
--     "picks whose target was met"  vs  "picks whose target was never met"
--
-- That is the first grading idea in this project that is FALSIFIABLE. Grading on
-- anchor overround was dropped because it predicts nothing measurable (ROI
-- non-monotone across bands, calibration flat at ECE 1.15-1.46%). A target is
-- different: either the price reached it or it did not, and both arms settle to
-- real outcomes. It claims nothing up front and can be checked afterwards.
--
-- ⚠️ THIS IS STILL NOT `picks_forward_test`, AND MUST NEVER BE POOLED WITH IT.
-- That table is a pre-registered test whose n drives stopping rules at
-- 200/400/800. A board leg did not clear the floor when it was published. Two
-- published sets with two different bars are fine as long as their records are
-- reported SEPARATELY and neither n contaminates the other.

ALTER TABLE picks_board
    -- when we first put this leg on the board (the "publication" moment)
    ADD COLUMN IF NOT EXISTS first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- the best price seen at ANY point while the leg was on the board, and where.
    -- This is what decides whether the target was reachable: a reader watching
    -- the board could have taken it at this price.
    ADD COLUMN IF NOT EXISTS best_odds_seen  NUMERIC,
    ADD COLUMN IF NOT EXISTS best_odds_book  TEXT,
    ADD COLUMN IF NOT EXISTS best_odds_at    TIMESTAMPTZ,
    -- stamped the first time best_odds_seen reaches odds_grade_b / _a. NULL means
    -- the price never got there, which is itself the finding for one of the arms.
    ADD COLUMN IF NOT EXISTS target_b_met_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS target_a_met_at TIMESTAMPTZ,
    -- settlement, so both arms can be compared on real outcomes
    ADD COLUMN IF NOT EXISTS outcome         TEXT,
    ADD COLUMN IF NOT EXISTS settled_at      TIMESTAMPTZ;

COMMENT ON COLUMN picks_board.best_odds_seen IS
    'Best price observed while the leg was on the board. Decides whether the '
    'target was REACHABLE — the record splits on target_b_met_at IS NOT NULL.';

COMMENT ON COLUMN picks_board.target_b_met_at IS
    'First moment best_odds_seen reached odds_grade_b. NULL = the price never '
    'got there; that arm is as much a result as the other one.';

CREATE INDEX IF NOT EXISTS idx_picks_board_settle
    ON picks_board (kickoff_at) WHERE outcome IS NULL;

-- Rebuilt to carry the tracking fields through to the public surface.
--
-- DROP first: `CREATE OR REPLACE VIEW` can only APPEND columns, never insert
-- them mid-list, and this adds the tracking fields before `kickoff_utc`.
-- Postgres rejects it with `cannot change name of view column "kickoff_utc" to
-- "first_seen_at"` — which is what the 2026-09-15 12:02 run failed on. Nothing
-- depends on this view but the picks page, so dropping it is safe; the GRANTs
-- below are re-applied because a DROP takes them with it.
DROP VIEW IF EXISTS picks_board_public;

CREATE VIEW picks_board_public AS
SELECT b.match_id, b.market, b.selection, b.odds, b.bookmaker, b.p_sharp,
       b.edge, b.odds_breakeven, b.odds_grade_b, b.odds_grade_a,
       b.anchor_overround, b.updated_at, b.first_seen_at,
       b.best_odds_seen, b.best_odds_book, b.best_odds_at,
       b.target_b_met_at, b.target_a_met_at, b.outcome, b.settled_at,
       m.date  AS kickoff_utc,
       l.name  AS league,
       l.country AS country,
       ht.name AS home_team,
       at.name AS away_team
  FROM picks_board b
  JOIN matches m    ON m.id  = b.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id;

GRANT SELECT ON picks_board_public TO anon;
GRANT SELECT ON picks_board_public TO authenticated;

NOTIFY pgrst, 'reload schema';
