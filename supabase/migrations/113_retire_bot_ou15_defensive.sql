-- BOT-OU15-RETIRE (2026-05-20): bot_ou15_defensive went silent on 2026-05-08
-- and never recovered.  Diagnostic chain over the day:
--
--   BOT-FUNNEL-DIAGNOSTIC (first run):
--     98 candidates → 97 dropped at ↓edge → 0 accepted at 5-6% threshold.
--
--   BOT-OU15-EDGE-REPAIR shipped: thresholds relaxed (T1/T2 6%→4%, T3/T4 5%→3%).
--
--   Funnel re-run after relaxation:
--     104 candidates → 98 dropped at ↓edge → 0 accepted at 3-4% threshold.
--
-- The model's calibrated probability now sits within 3% of bookmaker implied
-- on essentially every OU 1.5 candidate.  Calibration drift likely from May
-- 7-8 changes (VIG-REMOVE / DRAW-PER-LEAGUE / H2H-SPLITS) — investigation
-- queued as BOT-OU15-CALIBRATION-FORENSIC if we want to learn from it.
--
-- Per SHADOW-RETIRED-OK the bot will still produce shadow_bets going forward
-- so we can detect any future recovery (eg post-retrain edge resurgence).
-- Re-enable trigger: 30+ shadow_bets at ≥3% real ROI sustained over a week.

UPDATE bots
SET is_active = false,
    retired_at = now(),
    retired_reason = 'BOT-OU15-RETIRE 2026-05-20: silent since 2026-05-08. '
                    'BOT-FUNNEL-DIAGNOSTIC confirmed pure edge-threshold '
                    'starvation — model calibration now sits within 3% of '
                    'bookmaker implied on virtually every OU 1.5 candidate. '
                    'Relaxing thresholds 6%→4% (T1/T2) and 5%→3% (T3/T4) '
                    'recovered 0 of 104 candidates. Likely cause: May 7-8 '
                    'calibration shift (VIG-REMOVE / DRAW-PER-LEAGUE / '
                    'H2H-SPLITS). Re-enable trigger: 30+ shadow_bets at '
                    '≥3% real ROI sustained over a week, OR explicit model-'
                    'change that restores OU 1.5 edge.'
WHERE name = 'bot_ou15_defensive'
  AND retired_at IS NULL;
