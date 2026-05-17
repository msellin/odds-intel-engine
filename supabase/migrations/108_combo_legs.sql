-- COMBO-RESEARCH-PHASE-D (2026-05-17): support multi-leg accumulator bets.
--
-- Background: Phase A audit confirmed Coolbet does NOT compound margin on
-- accumulators (3-leg combo odds = exact product of leg odds). That means a
-- combo of N confirmed +5% EV singles preserves +EV multiplicatively. The
-- paper acca bot 'bot_acca_value' takes top-edge independent singles each
-- day and combines them into a 3-5 leg combo.
--
-- One row per combo bet in simulated_bets, with:
--   combo_legs   — JSONB array describing each leg
--   combo_size   — leg count (NULL for single bets, 2+ for combos)
--   market       — 'combo'
--   selection    — '3-leg', '4-leg', etc. (display label)
--   odds_at_pick — product of leg odds
--   match_id     — the first leg's match_id (kept for the existing NOT NULL
--                  constraint; combo doesn't logically belong to one match)
--
-- Settlement logic uses combo_legs to look up each leg's outcome and aggregate
-- (all-win → won, any-lose → lost, all-undecided → pending).

ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS combo_legs JSONB;
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS combo_size INTEGER;

CREATE INDEX IF NOT EXISTS idx_simulated_bets_combo
  ON simulated_bets (combo_size) WHERE combo_size IS NOT NULL;

-- Register the new bot. starting_bankroll = 1000 to match the rest of the
-- portfolio per PERF-V2-BANKROLL-1K (so portfolio headline ROI weighting
-- stays balanced).
INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
VALUES ('bot_acca_value', 'Cross-match accumulator on top-edge singles', 1000.00, 1000.00, true)
ON CONFLICT (name) DO NOTHING;
