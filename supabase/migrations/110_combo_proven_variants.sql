-- COMBO-PROVEN-VARIANTS (2026-05-18): register two new acca/system variants
-- that only combine legs from bots with confirmed +EV (either historical
-- backtest or live ROI + CLV). Mirrors bot_acca_value / bot_combo_system
-- but adds a per-variant bot_whitelist filter to the leg picker.
--
-- Why: combo backtest on the full raw pool (249 days, 18 bots) showed
-- -38% to -60% ROI. Restricting to high-quality bot legs (4 bots, 19 days)
-- showed +6% to +301% ROI. Whitelisted variants test whether the
-- restricted-to-good-legs combo strategy holds up live.

INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
VALUES (
    'bot_acca_proven',
    'Straight acca — legs only from proven +EV bots (ou15_defensive, ou35_attacking, v10_all, ou25_global, ah_away_dog, btts_all)',
    1000.00, 1000.00, true
),
(
    'bot_combo_proven_system',
    'No-singles system bet — same proven-bots whitelist as bot_acca_proven, stake spread across all 2-to-N sub-combos',
    1000.00, 1000.00, true
)
ON CONFLICT (name) DO NOTHING;
