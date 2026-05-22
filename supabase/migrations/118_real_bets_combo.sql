-- COMBO-RESTRUCTURE (2026-05-22)
-- Add combo support to real_bets so manually placed combos can be recorded
-- and settled. Mirrors the simulated_bets schema for these fields.
--
-- combo_legs JSONB: array of {match_id, market, selection, odds, prob, bot_source}
--                  same shape as simulated_bets.combo_legs
-- system_type TEXT: 'straight' | 'fours_up' | 'no_singles' | NULL (= straight single)
--                   drives settlement logic in settlement.py:settle_combo_bet()
--
-- match_id stays NOT NULL — for combo bets it holds the first leg's match_id
-- (same placeholder convention as simulated_bets). Settlement reads combo_legs.

ALTER TABLE real_bets
  ADD COLUMN IF NOT EXISTS combo_legs  JSONB,
  ADD COLUMN IF NOT EXISTS system_type TEXT;

COMMENT ON COLUMN real_bets.combo_legs  IS
  'COMBO-RESTRUCTURE: legs for combo real bets. Same shape as simulated_bets.combo_legs. NULL for singles.';
COMMENT ON COLUMN real_bets.system_type IS
  'COMBO-RESTRUCTURE: straight / fours_up / no_singles. Drives settle_combo_bet() dispatch. NULL = straight single.';

-- Bot description updates for restructured variants
UPDATE bots SET description = '[COMBO-RESTRUCTURE 2026-05-22] Straight 5-fold acca. Requires OU15/over in leg pool. Fires only when 5 legs qualify at ≥8% edge.'
  WHERE name = 'bot_acca_value';

UPDATE bots SET description = '[COMBO-RESTRUCTURE 2026-05-22] fours_up system: 5-fold + five 4-folds (6 tickets). Tolerates one leg failure. Requires OU15/over.'
  WHERE name = 'bot_combo_system';

UPDATE bots SET description = '[COMBO-RESTRUCTURE 2026-05-22] Straight 5-fold, proven markets (ou25/ou35/btts) + OU15/over required.'
  WHERE name = 'bot_acca_proven';

UPDATE bots SET description = '[COMBO-RESTRUCTURE 2026-05-22] fours_up system, proven markets + OU15/over required.'
  WHERE name = 'bot_combo_proven_system';
