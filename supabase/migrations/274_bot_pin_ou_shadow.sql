-- BOT-PIN-OU-SHADOW-2026-08-21 — two shadow bots for OU 2.5 + OU 3.5 markets
-- where Pinnacle has odds. Analog to bot_no_pin_shadow_v1 (which covers
-- 1X2 without Pinnacle), but for the INVERSE case: OU with Pinnacle but
-- without v10 model. Existing production bots require v10 output; v10
-- covers ~5-10% of matches. This bot skips the v10 dependency and uses
-- Pinnacle-implied directly.
--
-- Historical simulation (2026-05-04 → today, edge ≥ 8%, all tiers 1-4):
--   OU 2.5: 2,999 potential picks, blended flat ROI +13-25% per tier
--   OU 3.5: 1,948 potential picks, blended flat ROI +15-40% per tier
--   Actual current picks: 45 OU 2.5 total (98.6% missed)
--
-- Ships as shadow. Deployment discipline (same as bot_no_pin_shadow_v1
-- and bot_sweep_*_v1):
--   • writes only to shadow_bets — never simulated_bets, never bankroll
--   • maturity_label='experimental'
--   • observe for 4-6 weeks on FRESH data
--   • promote to paper beta at n≥50 AND ROI ≥ +3%
--   • auto-retire if ROI ≤ -8% at n≥50
--
-- Promotion decision target: 2026-10-01.

INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
    ('bot_sweep_ou25_v1',
     'Shadow: OU 2.5 line-shopping vs Pinnacle. Fires when best soft-book odds × Pinnacle-implied - 1 ≥ 8%. No v10 model dependency.',
     'pin_ou25_line_shop',
     'Pure Pinnacle-vs-soft-book edge on OU 2.5. Historical simulation on 3,308 tier-1-4 matches with Pinnacle OU 2.5 coverage: 2,999 pass 8% edge threshold at +7-25% ROI per tier. Currently only 45 of 3,308 are picked because existing bots require v10 model coverage which is thin.',
     TRUE, 'experimental', 1.00, 1.00),

    ('bot_sweep_ou35_v1',
     'Shadow: OU 3.5 line-shopping vs Pinnacle. Fires when best soft-book odds × Pinnacle-implied - 1 ≥ 8%. No v10 model dependency.',
     'pin_ou35_line_shop',
     'Pure Pinnacle-vs-soft-book edge on OU 3.5. Historical simulation: 1,948 potential picks at +15-40% ROI per tier. OU 3.5 is the strongest OU line in the audit — every tier positive with best tier-2 signal (+39.5%).',
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
