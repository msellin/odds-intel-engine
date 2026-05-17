-- COMBO-RESEARCH-PHASE-D-SYSTEM (2026-05-17): support "no-singles" system bets
-- (Trixie / Yankee / Canadian / Heinz depending on leg count).
--
-- The new bot bot_combo_system uses the same daily picks as bot_acca_value
-- but spreads its stake across ALL sub-combos of size 2 to N instead of
-- one straight max-leg combo. That gives a smoother P&L curve at lower
-- per-€ ROI (variance reduction trade-off).
--
-- One row per system-bet ticket in simulated_bets:
--   combo_legs   — same JSON shape as straight (the N picks)
--   combo_size   — N (number of picks, not sub-bets)
--   system_type  — NULL for straight accas, 'no_singles' for the new mode
--   stake        — TOTAL stake across all sub-combos
--   odds_at_pick — average effective odds (informational; settlement uses
--                  per-sub-combo math)
--
-- Settlement: when system_type = 'no_singles', settle_combo_bet enumerates
-- all combinations of size 2 to len(combo_legs) and sums each sub-combo's
-- payout. Stake per sub-bet = total_stake / number_of_sub_combos.

ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS system_type TEXT;

-- Register the new bot, same €1000 starting bankroll as everything else
-- (per PERF-V2-BANKROLL-1K so headline weighting stays balanced)
INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
VALUES (
    'bot_combo_system',
    'System bet (no singles) — same picks as bot_acca_value, stake spread across all 2-to-N sub-combos',
    1000.00, 1000.00, true
)
ON CONFLICT (name) DO NOTHING;
