-- MATURITY-LABEL-CANONICAL (2026-09-14). An OWN paper instrument leaked onto
-- the customer-facing /performance leaderboard because of ONE CHARACTER.
--
-- The page drops non-public bots with `maturityLabel !== 'experimental'`
-- (odds-intel-web performance/page.tsx). Migration 347 created
-- bot_trigger_1x2_sharp_tight_v1 with maturity_label = 'experiment' -- singular.
-- Eleven bots use 'experimental' and are correctly hidden; that one did not
-- match the filter and rendered on the public leaderboard as though it were a
-- customer-facing strategy. It is a PAPER instrument for the operator's OWN
-- betting, explicitly never placeable, and it has no business on that page.
--
-- Nobody typed it wrong twice; the value is free text with no constraint, so the
-- first typo was also the last check. Canonicalise the value, then constrain the
-- column so the next one fails loudly at write time instead of silently on a
-- customer surface.
--
-- 'experiment' is the only offender (1 row). The canonical set is exactly what
-- the codebase and the frontend already agree on.

UPDATE bots SET maturity_label = 'experimental'
 WHERE maturity_label = 'experiment';

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_maturity_label_check;
ALTER TABLE bots ADD CONSTRAINT bots_maturity_label_check
  CHECK (maturity_label IS NULL
         OR maturity_label IN ('experimental', 'beta', 'calibrated', 'retired'));

COMMENT ON COLUMN bots.maturity_label IS
  'Public-surface gate. /performance shows only non-experimental, non-retired '
  'bots; anything experimental is operator-facing (shadow-bots) only. '
  'CHECK-constrained since MATURITY-LABEL-CANONICAL-2026-09-14 because the '
  'singular "experiment" slipped an OWN paper instrument onto the customer '
  'leaderboard.';
