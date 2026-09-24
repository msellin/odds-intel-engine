-- 418 — #141 B4: "1x2 NEW+ EV5" and "1x2 NEW+ EV8" (2026-09-24, owner: "continue with B4").
--
-- NEW+ as a consensus-outlier bettor with its natural edge unit: EV = p x odds - 1 >= 5% / 8%,
-- flat across tiers, Pinnacle price required, no min_prob, odds 1.30-6.00, one pick per match.
-- Pre-registered in dev/active/1x2-model-rebuild-plan.md "B4"; backtest B2 (same window) CLV
-- +2.0% / +3.1%. See docs/SYSTEM_MAP.md. experimental: simulated_bets, hidden from /performance
-- and /picks, no placement path. Owner reviews at 20 / 50 / 100 settled picks on /admin/bots.

INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES
  ('bot_combined_1x2_ev5_v1', '1x2 NEW+ EV5',
   'COMBINED 1X2 model r1x2_comb_v1, edge = p*odds-1 >= 5% flat, Pinnacle price required, '
   'odds 1.30-6.00, one pick per match. Added 2026-09-24 (#141 B4).',
   1000.00, 1000.00, true, 'experimental', false),
  ('bot_combined_1x2_ev8_v1', '1x2 NEW+ EV8',
   'COMBINED 1X2 model r1x2_comb_v1, edge = p*odds-1 >= 8% flat, Pinnacle price required, '
   'odds 1.30-6.00, one pick per match. Added 2026-09-24 (#141 B4).',
   1000.00, 1000.00, true, 'experimental', false)
ON CONFLICT (name) DO NOTHING;
