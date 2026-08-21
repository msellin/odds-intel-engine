-- SHADOW-BOT-CLEANUP-2026-08-21 — retire two shadow bots whose data-collection
-- phase served its purpose. Findings from the audit:
--
-- (1) bot_no_pin_shadow_v1 (Aug 18 launch, n=121 settled):
--     Overall ROI −4.3%, CLV +8.1%. Broken down by selection:
--       home  n=42  ROI +32.7%  (winning)
--       draw  n=52  ROI −24.8%  (losing)
--       away  n=41  ROI −18.4%  (losing)
--     The unfiltered "any selection" bot loses because 74% of picks are
--     draw/away (loss). Refined replacement bot_no_pin_home_v1 (migration
--     277, coming next) fires ONLY on home to capture the winning slice.
--     Retire this one — its purpose is complete.
--
-- (2) bot_acca_leg_shadow (settled n=532):
--     ROI −9.2%, CLV +4.3%. Hypothetical single-leg picks derived from
--     acca bot's chosen legs. All acca/combo bots are already retired
--     (2026-06-06 ACCA-RETIRED-LEAK-FIX class). This shadow bot was
--     collecting data on whether the individual legs would win as
--     singles — verdict: no, they don't. Below the kill gate (n=532,
--     ROI ≤ -8%) so retirement is mechanical.
--
-- Existing shadow_bets rows remain for historical reference. Just marks
-- the bot inactive and stamps retired_at.

UPDATE bots
SET is_active = FALSE,
    retired_at = NOW(),
    retired_reason = 'SHADOW-BOT-CLEANUP-2026-08-21 — unfiltered no-pin shadow loses on draw/away (see per-selection audit). Refined bot_no_pin_home_v1 replaces it. Historical: n=121 settled, ROI -4.3%, CLV +8.1%.',
    updated_at = NOW()
WHERE name = 'bot_no_pin_shadow_v1';

UPDATE bots
SET is_active = FALSE,
    retired_at = NOW(),
    retired_reason = 'SHADOW-BOT-CLEANUP-2026-08-21 — hypothetical acca-leg-as-single audit complete. n=532 settled, ROI -9.2%, CLV +4.3%. Below auto-kill threshold. All parent acca/combo bots already retired.',
    updated_at = NOW()
WHERE name = 'bot_acca_leg_shadow';
