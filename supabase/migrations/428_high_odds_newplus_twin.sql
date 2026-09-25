-- 428 — #152: High-odds match result's new-model twin, on /performance (2026-09-25, owner).
-- Same rules as bot_high_roi_global_v2; only the probability is NEW+ (r1x2_comb_v1).
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks, show_on_performance)
VALUES ('bot_high_roi_global_v2_newplus_v1', 'High-odds match result — new model',
        'Twin of bot_high_roi_global_v2 (Spain/Australia/Iceland home/away 1.50-5.50) on the NEW+ 1X2 model. #152.',
        1000.00, 1000.00, true, 'testing', false, true)
ON CONFLICT (name) DO NOTHING;
