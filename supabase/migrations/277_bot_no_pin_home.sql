-- BOT-NO-PIN-HOME-2026-08-21 — refined replacement for bot_no_pin_shadow_v1.
--
-- Audit of bot_no_pin_shadow_v1 (Aug 18 → Aug 21, 121 settled picks):
--   home  n=42  ROI +32.7%  (winning)
--   draw  n=52  ROI −24.8%  (losing)
--   away  n=41  ROI −18.4%  (losing)
--
-- Same pattern as the line-shopping simulation on Pinnacle-covered matches
-- (BOT-PIN-1X2-SHADOW-2026-08-21): home wins, draw + away lose. Ships a
-- home-only refined bot at the same 8% edge threshold. Expected volume:
-- ~20-25 picks/day (was ~45/day unfiltered).
--
-- Retires bot_no_pin_shadow_v1 in migration 276.
--
-- Deployment discipline: shadow, experimental. Promotion at n≥50 & ROI
-- ≥+5% (bumped from the standard +3% because the underlying signal was
-- backtested on the same data — CLV floor +3% required as well). Kill
-- at ROI ≤-8%.

INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
    ('bot_no_pin_home_v1',
     'Shadow: 1X2 home wins on matches without Pinnacle coverage. Home-only refinement of bot_no_pin_shadow_v1 after audit showed draw/away picks loss-making.',
     'no_pin_1x2_home_ensemble',
     'Fires 1X2 home picks on matches without Pinnacle coverage when ensemble prob × best_soft_book - 1 ≥ 8% AND ≥3 accessible books quote the same selection. Refined from bot_no_pin_shadow_v1 audit — home slice had n=42 ROI +32.7% while draw (−24.8%) and away (−18.4%) sank the unfiltered aggregate to −4.3%.',
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
