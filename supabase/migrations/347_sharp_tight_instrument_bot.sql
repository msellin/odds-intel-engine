-- SHARP-TIGHT-INSTRUMENT (2026-09-15) — the one OWN configuration two
-- independent research rounds agreed was worth OBSERVING, and neither thought
-- was worth a euro.
--
-- Pre-registration: dev/active/own-sharp-tight-preregistration.md
-- Evidence:        docs/OWN_SHARP_CONFIG_SWEEP_2026_09_14.md
--                  docs/OWN_SWEEP_VERIFICATION_2026_09_14.md
--
-- WHY IT EXISTS. The first sweep graded 70,200 cells and found nothing. It
-- could not have: it swept a constant EXPECTED-ROI floor while the live gate is
-- a constant PROBABILITY-DIFFERENCE floor, and since roi_edge = prob_edge x
-- odds, a constant probability floor is a CURVE in odds. No cell of that grid
-- could express it. Swept properly, prob-edge >= 2 pct with odds <= 2.50 is the
-- only surviving family: n=225, ROI +17.07 pct, CI [+4.18, +29.95], no losing
-- fold, OOS +23.40 pct.
--
-- WHY IT IS PAPER AND STAYS PAPER. That result is almost certainly luck:
--   * twelve-day effect -- +0.99 pct (n=79) before 2026-09-02 vs +25.76 pct
--     (n=146) after; Coolbet alone on a constant 37-day pool does the same
--     (+0.99 -> +40.02, n=46)
--   * margin-corrected OWN-BOOK CLV on the same legs is -5.36 to -7.56 pct
--     beside those +19-42 pct ROIs. A random leg is ~-7.2 pct, so the selection
--     buys ~1.9pp of closing-line value -- real, and nowhere near the 7-8 pct
--     vig it has to clear. ANALYSIS_GOTCHAS §8: believe the CLV.
--
-- So: an instrument that MEASURES a hypothesis, not a bot that expresses a
-- belief. It is NOT in the placer's PLACEABLE_BOTS whitelist and gets no
-- coolbet_placer_bots toggle, so it cannot stake money even if someone clears
-- the global pause.
--
-- PROMOTION IS PRE-COMMITTED AND ROI CANNOT TRIGGER IT, at any value:
--   promote  only if margin-corrected own-book CLV > 0 with CI excluding 0 at n >= 300
--   retire   if that CLV is < -2 pct at n >= 300
--   else     keep observing; do NOT re-cut the rule

INSERT INTO bots (name, description, strategy, is_active, maturity_label,
                  starting_bankroll, current_bankroll, created_at)
SELECT 'bot_trigger_1x2_sharp_tight_v1',
       'INSTRUMENT (paper, never placeable). Sharp-anchored 1x2 trigger at the '
       'TIGHT gate the original sweep could not express: P_shin - 1/odds >= 2 pct '
       'AND odds <= 2.50, pooled over Coolbet/Epicbet/Unibet-Site. Backtest '
       'n=225 ROI +17.07 pct CI [+4.18,+29.95] OOS +23.40 pct -- but a 12-day '
       'effect with margin-corrected own-book CLV of -5.4 to -7.6 pct, so it is '
       'being MEASURED, not believed. Promotion requires own-book CLV > 0 at '
       'n>=300; ROI may never promote it.',
       'sharp_devig', TRUE, 'experiment', 1000, 1000, now()
WHERE NOT EXISTS (SELECT 1 FROM bots WHERE name = 'bot_trigger_1x2_sharp_tight_v1');
