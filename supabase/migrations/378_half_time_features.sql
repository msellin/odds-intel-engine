-- 378_half_time_features.sql
--
-- HALF-TIME LAYER ([[#084]] step 2), 2026-09-23.
--
-- WHY THESE COLUMNS EXIST
-- =======================
-- `matches.ht_score_home/away` and `h2_score_home/away` are filled on **172,439
-- of 175,450 finished matches (98.28%)** -- the highest-coverage non-trivial
-- column set in this database -- and until today NOTHING derived a feature from
-- them. `h2_score_*` had literally zero SELECTs anywhere in the codebase.
--
-- THE SHAPE IS THE POINT
-- ======================
-- `dev/active/per-market-feature-sets-design.md` establishes, with published
-- backing, that 1x2 lives on the DIFFERENCE of scoring rates while totals live
-- on the SUM. Karlis & Ntzoufras model the difference via the Skellam
-- distribution -- the difference of two Poissons -- in which the sum is
-- integrated out and DISCARDED, so a difference-shaped feature set is by
-- construction silent about totals.
--
-- Our existing 52-feature vector contains an explicit `elo_diff` and **no sum
-- term whatsoever**. These columns are the first sum-shaped features this
-- project has ever had, and they arrive with their difference-shaped twins so
-- each head can take the form it needs:
--
--     SUM        ht_expected_total, h2_expected_total, ht_share_expected
--     DIFFERENCE ht_expected_diff, h2_expected_diff
--
-- HOW THEY ARE COMPUTED
-- =====================
-- `scripts/build_half_time_ratings.py` -- opponent-adjusted attack/defence
-- multipliers per team for first-half and second-half goals, fitted by iterative
-- proportional fitting on exponentially-decayed history. Half-life defaults to
-- **300 days**, not the 30-90 used for match outcome: Wheatcroft & Sienkiewicz
-- (53,447 O/U forecasts) find totals want far longer memory. We currently share
-- one decay across every head.
--
-- ⚠️ WHAT THE PROBE ALREADY SAID, recorded so nobody reads these columns as a
-- promise. Scored STANDALONE through the residual harness, the half-time model
-- FAILS its deciding arm: alpha 0.0050 on de-vigged Pinnacle, blend +0.002%,
-- residual AUC 0.4086 -- i.e. where it disagrees with the market, the market is
-- right ~59% of the time. The Shin robustness arm passed (alpha 0.0600) and that
-- pass is NOT promoted: the improvement is 0.038% of log-loss and the two arms
-- disagree on identical data, which places the effect below the uncertainty of
-- the de-vig method itself.
--
-- These columns exist so the A/B can ask the DIFFERENT question the probe cannot:
-- does a half-time feature help as ONE INPUT AMONG MANY? A standalone model
-- failing does not answer that, and assuming it does nearly produced a wrong
-- conclusion in [[#077]].
--
-- All columns NULLable with no default. Population is a separate, re-runnable
-- script, because computing 172k rows is not a migration's job.

ALTER TABLE match_feature_vectors
    -- SUM-shaped: for the over/under and goals heads.
    ADD COLUMN IF NOT EXISTS ht_expected_total  numeric,
    ADD COLUMN IF NOT EXISTS h2_expected_total  numeric,
    -- WHEN goals are expected, not how many: ht/(ht+h2). A team pair expected to
    -- score early is a different bet from one expected to score late, at the
    -- same total -- and it is the only feature here with no full-match analogue.
    ADD COLUMN IF NOT EXISTS ht_share_expected  numeric,
    -- DIFFERENCE-shaped: for the 1x2 head.
    ADD COLUMN IF NOT EXISTS ht_expected_diff   numeric,
    ADD COLUMN IF NOT EXISTS h2_expected_diff   numeric;

COMMENT ON COLUMN match_feature_vectors.ht_expected_total IS
  'Expected FIRST-half goals, both teams summed (lambda_ht_home + lambda_ht_away), '
  'from opponent-adjusted half-time ratings (scripts/build_half_time_ratings.py, '
  '300-day half-life). The first SUM-shaped feature in this table -- see '
  'migration 378''s header for why that distinction is load-bearing.';

COMMENT ON COLUMN match_feature_vectors.ht_share_expected IS
  'ht_expected_total / (ht_expected_total + h2_expected_total) -- the expected '
  'SHARE of goals arriving before half-time. Answers WHEN rather than HOW MANY, '
  'and is the only feature here with no full-match equivalent.';
