-- BOT-PIN-1X2-SHADOW-2026-08-21 — two more shadow bots for 1X2 markets
-- where Pinnacle has odds. Mirror of the OU bots (274) but for 1X2.
-- Fills the last gap in the (market × model-availability × Pinnacle) grid.
--
-- Historical simulation (2026-05-04 → today, pure Pinnacle-implied ×
-- best_soft_book edge, no model):
--
--   Home wins:
--     Tier 1 12%+  n=1208  ROI +12.1%   ← ship
--     Tier 2 12%+  n= 138  ROI +31.3%   ← ship
--     Tier 3 12%+  n= 105  ROI -11.6%   ← skip
--     Tier 4 12%+  n=  99  ROI -13.8%   ← skip
--   Draws:
--     Tier 4  5%+  n= 348  ROI +6-18% across buckets  ← ship
--     Tier 1-3    mixed or negative                    ← skip
--   Away wins:
--     ALL tiers 8%+ ROI -3 to -20% ← DO NOT SHIP (soft-book away
--     lines are systematically stale/wrong at line-shopping edges)
--
-- Deployment discipline: shadow, experimental, promotion at n≥50 +
-- ROI ≥ +3% → beta. Kill at ROI ≤ -8%. Same pattern as bots 271-274.

INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
    ('bot_pin_1x2_home_v1',
     'Shadow: 1X2 home wins, tiers 1-2 only, edge ≥ 12%. Pure Pinnacle-vs-soft-book line-shopping, no model dependency.',
     'pin_1x2_home_line_shop_t12',
     'Home wins at 12%+ line-shopping edge on tier 1 (Big-5) + tier 2 leagues. Historical: tier 1 n=1208 at +12.1% ROI, tier 2 n=138 at +31.3%. Tiers 3-4 skipped (negative ROI at same threshold). Fills gap where existing bot_v10_all lacks v10 predictions for the match.',
     TRUE, 'experimental', 1.00, 1.00),

    ('bot_pin_1x2_draw_tier4_v1',
     'Shadow: 1X2 draws, tier 4 leagues only, edge ≥ 5%. Pure Pinnacle-vs-soft-book line-shopping.',
     'pin_1x2_draw_line_shop_t4',
     'Draws in tier 4 leagues show consistent +6-18% ROI across all edge buckets (5-8%, 8-12%, 12%+) — an unusually consistent signal. Historical: n=348 combined across buckets at tier 4. Tier 1-3 draws show mixed/negative results at line-shopping edges.',
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
