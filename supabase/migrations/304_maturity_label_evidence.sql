-- 304_maturity_label_evidence.sql
-- BOT-MATURITY-UNEARNED-2026-09-06
--
-- `maturity_label = 'calibrated'` is not cosmetic. It gates:
--   * workers/automation/coolbet_signaler.py:92 — whether a pick may be
--     promoted to the PUBLIC Telegram channel
--   * workers/jobs/settlement.py:2377
--   * workers/jobs/coolbet_daily_summary.py:105
--   * workers/jobs/coolbet_daemon_healthcheck.py:150
--
-- It is a claim that a strategy is proven. Two active bots hold it without any
-- evidence behind it, measured 2026-09-06:
--
--   bot_v10_all           559 settled   <- legitimately calibrated
--   bot_1x2_specialist      8 settled
--   bot_dnb_specialist      0 settled   <- has NEVER produced a bet, ever
--
-- The consequence is concrete rather than cosmetic: if `bot_dnb_specialist`
-- ever fires, its FIRST EVER pick is eligible for the public channel carrying a
-- "calibrated" label, and the operator places real money manually on those
-- signals. `bot_1x2_specialist` at n=8 is the same problem with a slightly
-- larger fig leaf — its +45% flat ROI over 8 bets is noise, and its own audit
-- row records an ROI t-stat of +1.12.
--
-- Demote both to `beta`. That is NOT a retirement and NOT a removal from the
-- public leaderboard: `beta` remains in PUBLIC_MATURITY_LABELS, so both keep
-- running and stay visible as what they actually are — unproven strategies.
-- What it removes is eligibility for the public-channel promotion gate, which
-- neither has earned.
--
-- Re-promotion should follow the same route any other bot takes: the CLV gate
-- in weekly_bot_review at CLV_MIN_N, not a manual label edit.

UPDATE bots
   SET maturity_label = 'beta',
       updated_at     = NOW()
 WHERE maturity_label = 'calibrated'
   AND retired_at IS NULL
   AND name IN ('bot_dnb_specialist', 'bot_1x2_specialist');
