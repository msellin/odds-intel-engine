-- 423 — #149: the O/U sharp-outlier bots (2026-09-25).
-- A soft book's O/U 1.5/2.5/3.5 quote beating Pinnacle's power-de-vigged fair price by EV 5-15%.
-- EARLY = quote >= 12 h before kickoff (backtest O3 T3); 2ANCHOR = also beats the leave-one-out
-- consensus of the other books (O3 T2). dev/active/market2-model-plan.md; docs/SYSTEM_MAP.md.
-- experimental: simulated_bets, hidden from /performance and /picks, no placement path.
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES
  ('bot_ou_sharp_early_v1', 'O/U EARLY',
   'O/U 1.5/2.5/3.5: soft-book quote beats Pinnacle fair price by EV 5-15%, >= 12 h before kickoff. #149.',
   1000.00, 1000.00, true, 'experimental', false),
  ('bot_ou_sharp_2anchor_v1', 'O/U TWO-ANCHOR',
   'O/U 1.5/2.5/3.5: soft-book quote beats Pinnacle fair price by EV 5-15% AND the other books'' consensus by >= 2%. #149.',
   1000.00, 1000.00, true, 'experimental', false)
ON CONFLICT (name) DO NOTHING;
