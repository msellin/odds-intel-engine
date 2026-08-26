-- SHADOW-RETIRE-NEGATIVE-CLV-2026-08-26
--
-- Retire the two shadow bots whose de-vigged Pinnacle CLV is decisively
-- negative. Both are `experimental`, both still surface picks on
-- /admin/shadow-bots, and the operator places real money off that page.
--
--   bot_sweep_1x2_draw_v1   n=51  CLV -4.87%  t = -7.30
--   bot_sweep_1x2_home_v1   n=84  CLV -3.01%  t = -2.74
--
-- Both clear the retire side of the CLV gate (|t| >= 1.65) shipped in
-- CLV-FIRST-DEV-LOOP-2026-08-26. Negative CLV means the bot is systematically
-- taking prices BELOW fair value — not unlucky, mispriced.
--
-- bot_sweep_1x2_home_v1 is the instructive one: it shows **+11.65% ROI**, the
-- best raw return of any shadow bot, on n=84. That is exactly the false
-- positive the old ROI gate would have promoted. At n=84 an ROI t-stat is 0.69
-- — indistinguishable from noise — while its CLV t-stat is -2.74. The bot is
-- buying below fair and has been lucky.
--
-- Retiring stops them appearing in upcoming picks (the page filters
-- `retired_at IS NULL`). It does NOT stop shadow tracking: per SHADOW-RETIRED-OK
-- (2026-05-20) retired bots keep writing shadow_bets, so if the picture changes
-- the evidence keeps accruing and the decision is reversible with one UPDATE.
--
-- Deliberately NOT retired here:
--   bot_sweep_btts_yes_v1 — CLV is unavailable, not negative. Pinnacle quotes no
--   BTTS through API-Football (8 bet types, none of them BTTS), so this bot can
--   never be validated against a sharp line. That is an argument for treating
--   its picks with suspicion, but retiring on absence of evidence is a different
--   decision from retiring on evidence of absence, and it is the operator's.

UPDATE bots
   SET retired_at = NOW(),
       is_active  = FALSE
 WHERE name IN ('bot_sweep_1x2_draw_v1', 'bot_sweep_1x2_home_v1')
   AND retired_at IS NULL;

COMMENT ON TABLE bots IS
  'Bot roster. retired_at IS NULL = live. Retired bots keep writing shadow_bets '
  '(SHADOW-RETIRED-OK 2026-05-20) so re-enable decisions stay evidence-based. '
  'As of 2026-08-26 the graduation gate is a CLV t-statistic, not raw ROI — see '
  'CLV-FIRST-DEV-LOOP and docs/ANALYSIS_GOTCHAS.md §8.';
