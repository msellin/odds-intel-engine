-- BOT-OU15-DIAGNOSE-CLOSE (2026-05-25): final retirement of bot_ou15_defensive.
--
-- Timeline:
--   2026-05-08  Bot goes silent. Last placed bet on this date.
--   2026-05-20  Migration 113 retires the bot after BOT-FUNNEL-DIAGNOSTIC
--               showed 97/98 candidates dying at ↓edge (BOT-OU15-RETIRE).
--               BOT-OU15-EDGE-REPAIR relaxed thresholds (T1/T2 6%→4%,
--               T3/T4 5%→3%) — 0 of 104 candidates recovered.
--   2026-05-22  Migration 117 un-retires it ("un-retire all 8 main bots
--               to generate signal volume"). Expected behaviour was
--               explicitly "likely fire ~0 bets (edge-starved)".
--   2026-05-22→25  Still silent across the re-enable window.
--
-- Diagnostics that were ruled out before retiring the first time:
--   COOLBET-OR-PIN-REQUIRED (audit section 5: Pinnacle covers virtually
--     every league bot_ou15_defensive bet)
--   PIN-VETO-EXT, ACCESSIBLE-BM (audit section 6: only 13.3% non-accessible)
--   MFV inference, ALN-1 (audit sections)
--   Calibration tightening — BOT-OU15-EDGE-REPAIR already tested
--     thresholds 4/4/3/3 with zero recovery on 104 candidates.
--
-- Conclusion: no further diagnostic is informative. The May 7-8
-- calibration shift (VIG-REMOVE / DRAW-PER-LEAGUE / H2H-SPLITS)
-- compressed the model's OU 1.5 probability output to within 3% of
-- bookmaker implied across the board.  At 75-80% baseline implied
-- probability for "over 1.5", the market is already efficient at
-- 1.30-1.60 odds — no threshold change rescues this.
--
-- Re-enable trigger (unchanged from migration 113):
--   30+ shadow_bets at ≥3% real ROI sustained over a week,
--   OR an explicit model retrain that demonstrably restores OU 1.5 edge.
--
-- Per SHADOW-RETIRED-OK the bot will still produce shadow_bets for
-- recovery detection.

UPDATE bots
SET is_active = false,
    retired_at = now(),
    retired_reason = 'BOT-OU15-DIAGNOSE-CLOSE 2026-05-25: 17-day silent '
                    'period (2026-05-08 → 2026-05-25). All diagnostics '
                    'ruled out: COOLBET/PIN-VETO/ACCESSIBLE-BM/ALN-1/MFV '
                    'inference/calibration (BOT-FUNNEL-DIAGNOSTIC + '
                    'BOT-OU15-EDGE-REPAIR — 0/104 candidates recovered '
                    'after relaxing thresholds 6%→4% and 5%→3%). '
                    'Migration 117 un-retired it 2026-05-22; still silent '
                    'through 2026-05-25. Calibration drift killed OU 1.5 '
                    'edge structurally; market is too efficient at 1.30-'
                    '1.60 odds for a Poisson + Platt blend to find edge. '
                    'Re-enable trigger: 30+ shadow_bets at ≥3% real ROI '
                    'over a week, OR explicit model change restoring edge.'
WHERE name = 'bot_ou15_defensive'
  AND retired_at IS NULL;

-- Sanity: row should now be inactive with retired_at populated.
DO $$
DECLARE
    n_active INT;
BEGIN
    SELECT COUNT(*) INTO n_active
    FROM bots
    WHERE name = 'bot_ou15_defensive'
      AND (is_active = true OR retired_at IS NULL);
    IF n_active > 0 THEN
        RAISE EXCEPTION 'BOT-OU15-DIAGNOSE-CLOSE: bot_ou15_defensive still active or retired_at null';
    END IF;
END $$;
