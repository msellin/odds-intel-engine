-- BTTS-RE-EVAL-JUNE8 (2026-06-07): activate bot_btts_v2 as experimental paper bot.
-- Narrow odds window 1.80-2.49 based on post-May-24 shadow bucket analysis
-- (1.80-2.09: +27.9% ROI n=19; 2.10-2.49: +74.4% ROI n=9).
-- btts_yes Platt fitted with n=154 on v20260607; coefficient unstable so
-- starting experimental to validate over 30 settled bets.
-- ensure_bots() auto-creates the row on first pipeline run; this migration
-- sets maturity_label so it never crosses the CHERRY-PICK-PLACER real-money gate.

INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active, maturity_label)
VALUES (
  'bot_btts_v2',
  'BTTS narrow odds window 1.80-2.49. BTTS-RE-EVAL-JUNE8 2026-06-07: post-May-24 shadow 1.80-2.49 bucket +27-74% ROI on n=28. Paper-only (experimental) until 30 settled bets confirm signal.',
  1000.0, 1000.0, true, 'experimental'
)
ON CONFLICT (name) DO UPDATE SET
  is_active      = true,
  maturity_label = 'experimental',
  retired_reason = NULL,
  retired_at     = NULL;
