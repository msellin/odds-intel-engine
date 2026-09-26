-- 480 — [[#191]] retire seven OWN-track bots (owner "yes go with all 3", 2026-09-26, after the #191 audit —
-- dev/active/own-bot-lineup-synthesis.md). All 'experimental', none on /performance, /picks, Telegram or VIP
-- (verified); no PICKS bot is touched. Their picks stay in the ledger and the Retired section for research.
--  * FOLDED into the OWN 1X2 · SHARP bot bot_own_1x2_v1 (v2 anchor, #182 guards, last 3 h, best Estonian book):
--    bot_coolbet_trigger_sharp_1x2_v1 (indep. CLV +2.7% n 96), bot_unibet_trigger_sharp_1x2_v1 (+2.6% n 108),
--    bot_trigger_1x2_sharp_v1 (+1.1% n 62; > 3 h −5.7%) — 96–100% of the per-book picks were the book-agnostic
--    bot's anyway; all were Pinnacle-only with no #182 guards.
--  * FOLDED into a future OWN O/U · SHARP bot (built fresh, not inherited): bot_unibet_trigger_sharp_ou_v1 (+4.4% n 18),
--    bot_trigger_ou_sharp_v1 (−2.7% n 16).
--  * RETIRED: bot_coolbet_trigger_sharp_ou_v1 (100% duplicate, −3.6% n 24), bot_unified_gate_1x2_paper_v1
--    (indep. CLV −14.1% [−16.9, −11.0] n 241 — draws and aways lose; also closes its stray placement path).
-- Producers stop at once: pick_generator / pick_trigger_matcher look bots up WHERE retired_at IS NULL.
-- Not here: bot_coolbet_1x2_model_v1 / bot_coolbet_ou_model_v1 (the Coolbet real-money family — off and locked;
-- retired in a separate step because the placement code pins them).
UPDATE bots SET is_active = false, retired_at = now(),
       retired_reason = COALESCE(retired_reason || ' | ', '') ||
         '2026-09-26 #191 OWN-track clean-up (migration 480): ' ||
         CASE name
           WHEN 'bot_unified_gate_1x2_paper_v1' THEN 'retired — independent CLV −14.1% (n 241), question answered'
           WHEN 'bot_coolbet_trigger_sharp_ou_v1' THEN 'retired — 100% duplicate of bot_trigger_ou_sharp_v1, −3.6% (n 24)'
           WHEN 'bot_unibet_trigger_sharp_ou_v1' THEN 'folded into the future OWN O/U · SHARP bot'
           WHEN 'bot_trigger_ou_sharp_v1' THEN 'folded into the future OWN O/U · SHARP bot'
           ELSE 'folded into bot_own_1x2_v1 (OWN 1X2 · SHARP, v2 anchor, last 3 h)'
         END
 WHERE name IN ('bot_unified_gate_1x2_paper_v1','bot_coolbet_trigger_sharp_ou_v1','bot_coolbet_trigger_sharp_1x2_v1',
                'bot_unibet_trigger_sharp_1x2_v1','bot_trigger_1x2_sharp_v1','bot_unibet_trigger_sharp_ou_v1',
                'bot_trigger_ou_sharp_v1')
   AND retired_at IS NULL;
-- verify: NOT EXISTS (SELECT 1 FROM bots WHERE retired_at IS NULL AND name IN ('bot_unified_gate_1x2_paper_v1','bot_coolbet_trigger_sharp_ou_v1','bot_coolbet_trigger_sharp_1x2_v1','bot_unibet_trigger_sharp_1x2_v1','bot_trigger_1x2_sharp_v1','bot_unibet_trigger_sharp_ou_v1','bot_trigger_ou_sharp_v1'))
