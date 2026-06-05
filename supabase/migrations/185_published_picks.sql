-- GROWTH-ACCURACY-PICKS-LOG (2026-06-05) — published_picks table.
--
-- Append-only, kickoff-timestamped log of model picks. The data layer
-- behind GROWTH-ACCURACY-PAGE (the future /accuracy marketing surface).
--
-- CRITICAL: this is NOT real-money tracking. Different table from
-- `simulated_bets` (which is bankroll-aware with EV/edge math). Here,
-- even 1.01-odds heavy-favourite picks count as hits if the outcome
-- materialised. Pure outcome-accuracy, matching how competitor sites
-- frame "X% accuracy" claims without odds context.
--
-- The picked_at column is the credibility anchor — it timestamps when
-- we made the pick (BEFORE kickoff) so the public-facing claim "we
-- called these picks ahead of time" is auditable.
--
-- is_backfilled distinguishes live-published rows (immutably logged
-- by the daily cron) from historical backfilled rows (reconstructed
-- from predictions × matches data). The accuracy page must label
-- backfilled rows differently — credibility-load-bearing distinction.

CREATE TABLE IF NOT EXISTS published_picks (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id          uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,

  -- One of: '1x2', 'over_under_15', 'over_under_25', 'btts'
  market            text NOT NULL,
  -- One of:
  --   for 1x2:           'home' | 'draw' | 'away'
  --   for over_under_*:  'over' | 'under'
  --   for btts:          'yes'  | 'no'
  selection         text NOT NULL,

  -- The model's probability for this selection at pick-time (0..1)
  model_probability numeric(5, 4) NOT NULL CHECK (model_probability >= 0 AND model_probability <= 1),

  -- Which model produced this pick (e.g. 'v20260524_market').
  -- Allows segmenting accuracy by model era when the model gets
  -- retrained.
  model_version     text NOT NULL,

  -- IMMUTABLE timestamp of when the pick was logged. For live-published
  -- rows this is the cron-run time; for backfilled rows it's an
  -- approximation (kickoff_at − 6h, the typical pre-match window).
  -- Set ONCE and never modified — the credibility anchor.
  picked_at         timestamptz NOT NULL,

  -- Match kickoff time, copied at pick-time so the row is
  -- self-contained for the public page query.
  kickoff_at        timestamptz NOT NULL,

  -- Filled post-match by the settlement hook:
  --   'hit'   — the picked selection occurred
  --   'miss'  — the picked selection did not occur
  --   'void'  — match cancelled/postponed; doesn't count for/against
  -- NULL while pending (kickoff has not been completed)
  outcome           text,
  settled_at        timestamptz,

  -- TRUE for picks reconstructed from historical predictions data
  -- (backfilled in a single batch). FALSE for live-published rows
  -- from the daily cron. The public accuracy page MUST label these
  -- differently — "Live picks since X" vs "Backfilled history".
  is_backfilled     boolean NOT NULL DEFAULT false,

  created_at        timestamptz NOT NULL DEFAULT NOW(),

  -- One pick per (match, market, model). Re-running the publisher
  -- is idempotent and never duplicates. Cross-model picks on the
  -- same match-market are intentional (lets us track model versions
  -- side-by-side).
  CONSTRAINT published_picks_unique_per_market_model
    UNIQUE (match_id, market, model_version),

  CONSTRAINT published_picks_market_chk
    CHECK (market IN ('1x2', 'over_under_15', 'over_under_25', 'btts')),

  CONSTRAINT published_picks_selection_chk
    CHECK (selection IN ('home', 'draw', 'away', 'over', 'under', 'yes', 'no')),

  CONSTRAINT published_picks_outcome_chk
    CHECK (outcome IS NULL OR outcome IN ('hit', 'miss', 'void'))
);

-- Public read access — the /accuracy page renders these rows publicly.
ALTER TABLE published_picks ENABLE ROW LEVEL SECURITY;

-- Idempotent CREATE POLICY (2026-06-06): PostgreSQL doesn't support
-- IF NOT EXISTS on CREATE POLICY, so we DROP first. Migration 185 was
-- previously applied in a state where the table+policy landed on the
-- remote DB but the migration record wasn't tracked, causing every
-- CI re-push to fail at "policy already exists". This pattern keeps
-- the migration safely re-runnable. See dev/active/density-copy-research
-- doc + GROWTH-COPY-DENSITY Day 1 commit chain (2026-06-06).
DROP POLICY IF EXISTS "published_picks_public_read" ON published_picks;
CREATE POLICY "published_picks_public_read"
  ON published_picks
  FOR SELECT
  USING (true);

-- Indexes for the public page queries
CREATE INDEX IF NOT EXISTS published_picks_picked_at_idx
  ON published_picks (picked_at DESC);

-- Filters to settled rows in the accuracy-rate aggregation
CREATE INDEX IF NOT EXISTS published_picks_settled_outcome_idx
  ON published_picks (outcome, kickoff_at DESC)
  WHERE outcome IS NOT NULL;

-- For model-era segmentation
CREATE INDEX IF NOT EXISTS published_picks_model_idx
  ON published_picks (model_version, picked_at DESC);

-- For per-match lookups (match-detail page rendering its own picks)
CREATE INDEX IF NOT EXISTS published_picks_match_idx
  ON published_picks (match_id);
