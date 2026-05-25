-- ACCA-LEG-SHADOW (2026-05-25): virtual bot for tracking acca legs as
-- hypothetical singles. Every leg an acca variant picks is also written to
-- shadow_bets attributed to this bot, settled by the existing shadow
-- settlement pass.
--
-- Purpose: answer the question "if the singles bots (ou25/ou35/btts) widened
-- their gates to include the matches the acca catches, would those singles be
-- +EV?". Acca picks use looser filters than the singles bots (no Platt
-- calibration, no Pinnacle-disagreement veto, no sharp-consensus gate), so
-- this is a controlled way to gather settled evidence before relaxing any
-- production singles config.
--
-- Revisit: PRIORITY_QUEUE entry ACCA-LEG-SHADOW-EVAL — target 2026-07-15 once
-- 30+ settled legs accumulate.

INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
VALUES (
    'bot_acca_leg_shadow',
    'Virtual bot. Holds shadow_bets rows for every leg picked by any acca variant, treated as hypothetical singles at the acca leg odds. Never places real bets. Used to evaluate whether widening singles-bot filters would be +EV.',
    -- Bankroll must be > 0 per chk_bots_bankroll_positive. Nominal 1.00 —
    -- never staked (shadow_bets has fixed 10u nominal stake, no bankroll touch).
    1.00, 1.00, true
)
ON CONFLICT (name) DO NOTHING;
