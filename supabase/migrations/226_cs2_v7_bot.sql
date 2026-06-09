-- Register bot_cs2_v7 in bots table for bankroll tracking.
INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES ('bot_cs2_v7',
    'CS2 v7 stacking (hltv_v1 + form + h2h + tier + pistol + tm + rest)',
    'Half-Kelly sized, fires when bookie >= threshold with >=2 books consensus and no roster change. AUC 0.694 vs hltv_v1 0.673 baseline.',
    1000.00, 1000.00, TRUE)
ON CONFLICT (name) DO NOTHING;
