-- 414 — #141 RATING-1X2-BOT: "1x2 market NEW" (2026-09-24, owner request).
--
-- A twin of bot_v10_1x2 priced by the walk-forward 1X2 rating model
-- (rating_1x2_predictions, model_version r1x2_d8plus_v1 — migration 412). Same
-- thresholds, odds range, cohort, veto and staking as its twin; the ONLY difference
-- is where the 1X2 probability comes from (workers/jobs/daily_pipeline_v2.py,
-- prob_source = "rating_1x2"). See docs/SYSTEM_MAP.md and MODEL_WHITEPAPER §4.4.
--
-- experimental: writes simulated_bets like its twin, hidden from /performance and
-- /picks (show_on_picks false). simulated_bets bots have no real-money placement path
-- (migration 413), and no coolbet_placer_bots row is created here.

INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES ('bot_rating_1x2_v1', '1x2 market NEW',
        'Twin of bot_v10_1x2 priced by the walk-forward 1X2 rating model r1x2_d8plus_v1 '
        '(no Pinnacle shrinkage). Holdout log-loss 1.008 vs 1.071; alpha vs Pinnacle = 0. '
        'Added 2026-09-24 (#141).',
        1000.00, 1000.00, true, 'experimental', false)
ON CONFLICT (name) DO NOTHING;
