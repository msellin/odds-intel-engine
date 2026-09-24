-- 416 — #141 round 3b: "1x2 market NEW+" (2026-09-24, owner request).
--
-- The second twin of bot_v10_1x2 (the first is bot_rating_1x2_v1, migration 414): identical
-- thresholds, odds range, cohort, veto and staking; its 1X2 probability comes from the COMBINED
-- model (rating_1x2_predictions, model_version r1x2_comb_v1 — migration 415; ratings +
-- bookmaker consensus + Pinnacle). See docs/SYSTEM_MAP.md and MODEL_WHITEPAPER §4.4b.
-- experimental: simulated_bets, hidden from /performance and /picks, no placement path.

INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES ('bot_combined_1x2_v1', '1x2 market NEW+',
        'Twin of bot_v10_1x2 priced by the COMBINED 1X2 model r1x2_comb_v1 (ratings + de-vigged '
        'bookmaker consensus + Pinnacle). Holdout log-loss 0.9763 vs 1.0711. Added 2026-09-24 (#141).',
        1000.00, 1000.00, true, 'experimental', false)
ON CONFLICT (name) DO NOTHING;
