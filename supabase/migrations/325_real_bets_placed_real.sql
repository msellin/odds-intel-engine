-- COOLBET-PICK-TABLE-AUDIT Stage 2 (2026-09-09): make real_bets a trustworthy
-- money ledger. Today real_bets mixes three kinds of row:
--   * REAL   — the UI placer's balance-confirmed placements + manually-reconciled
--              bets from the operator's actual Coolbet account (real money moved),
--   * PAPER  — the paper daemon (coolbet_placer place_all_bets record=True,
--              execute=False) writes a row per qualified pick without placing,
--   * LEGACY — rows written before this column exists (unverifiable now).
-- The public /performance headline reads simulated_bets, NOT real_bets, so it is
-- unaffected; this tag makes the ADMIN real-money overlays honest and lets the
-- real placer's dedup ignore paper rows (a paper row must never block a real bet).
--
-- Owner decision 2026-09-09: TAG, do NOT delete (deleting money records is
-- irreversible; the same call was made for the retired combo history). See
-- docs/BETTING_ARCHITECTURE.md §8 Stage 2.

ALTER TABLE real_bets ADD COLUMN IF NOT EXISTS placed_real BOOLEAN;

COMMENT ON COLUMN real_bets.placed_real IS
  'TRUE = real money confirmed moved (UI placer balance-delta, or a manual bet '
  'reconciled from the real Coolbet account). FALSE = paper (paper daemon '
  'record=True/execute=False) — never a real stake. NULL = legacy row from before '
  'this column, unverified. Set at write time going forward. '
  'COOLBET-PICK-TABLE-AUDIT Stage 2, docs/BETTING_ARCHITECTURE.md.';

-- Backfill the only class we can prove retroactively: a placement with a matching
-- coolbet_placement_attempts row whose outcome='placed' is a confirmed real stake.
-- Everything else stays NULL (unverified) rather than being falsely marked paper —
-- some NULL rows are genuine MANUAL real bets that never went through the UI placer,
-- and we must not misclassify a real bet as paper. The owner can reconcile the NULL
-- tail from the account; going forward every row is tagged at write time so the
-- NULL legacy tail is fixed and shrinking in relative terms.
UPDATE real_bets rb
   SET placed_real = TRUE
 WHERE rb.placed_real IS NULL
   AND EXISTS (
     SELECT 1 FROM coolbet_placement_attempts a
      WHERE a.match_id  = rb.match_id
        AND a.market     = rb.market
        AND a.selection  = rb.selection
        AND a.outcome    = 'placed'
   );
