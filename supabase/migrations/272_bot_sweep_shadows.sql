-- CONFIG-SWEEP-2026-08-19 Phase D — deploy three shadow bots derived from
-- the walk-forward parameter sweep. All three fired positive ROI + CLV in
-- ALL THREE test windows (W1: May-Jun, W2: Jun-Jul, W3: Aug). The signal
-- is: the model has real edge specifically in tier 2-3 leagues on
-- home wins, draws, and BTTS-yes. Full analysis in
-- dev/active/config-sweep-2026-08-19-report.md.
--
-- Deployment discipline (same as bot_no_pin_shadow_v1):
--   • writes only to shadow_bets — never simulated_bets, never bankroll
--   • maturity_label='experimental'
--   • observe for 4-6 weeks on FRESH data
--   • promote to paper beta only if positive ROI + CLV sustain at n≥50
--   • auto-retire if ROI ≤ -8% at n≥50
--
-- Promotion/retirement decision target: MODEL-EVIDENCE-CHECKPOINT-2026-11-01.

INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
    -- Best CLV (+10.16%), Pinnacle-required, most-conservative odds range
    ('bot_sweep_1x2_home_v1',
     'Sweep-derived shadow: 1X2 home wins, tier 2-3 leagues, edge ≥ 10%, odds 2.0-5.0, Pinnacle required.',
     'sweep_1x2_home_tier23',
     'Config discovered by CONFIG-SWEEP-2026-08-19 walk-forward backtest. Historical: 501 bets, +9.34% ROI, +10.16% CLV, positive in all three test windows.',
     TRUE, 'experimental', 1.00, 1.00),

    -- Biggest volume (714 bets), lower edge threshold works with Pinnacle gate
    ('bot_sweep_1x2_draw_v1',
     'Sweep-derived shadow: 1X2 draws, tier 2-3 leagues, edge ≥ 5%, odds 1.3-3.5, Pinnacle required.',
     'sweep_1x2_draw_tier23',
     'Config discovered by CONFIG-SWEEP-2026-08-19. Historical: 714 bets, +7.33% ROI, +2.74% CLV. Draws are an under-exploited market — existing bots rarely pick them.',
     TRUE, 'experimental', 1.00, 1.00),

    -- Smallest but most-recent-window strong (+10.2% in W3)
    ('bot_sweep_btts_yes_v1',
     'Sweep-derived shadow: BTTS yes, tier 2-3 leagues, edge ≥ 5%, odds 2.0-2.5.',
     'sweep_btts_yes_tier23',
     'Config discovered by CONFIG-SWEEP-2026-08-19. Historical: 318 bets, +5.44% ROI, +6.15% CLV. W3 (most recent) strongest at +10.21%.',
     TRUE, 'experimental', 1.00, 1.00)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE,
    maturity_label = 'experimental',
    description = EXCLUDED.description,
    strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL,
    retired_reason = NULL,
    updated_at = NOW();
