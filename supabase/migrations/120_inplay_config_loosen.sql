-- INPLAY-CONFIG-LOOSEN (2026-05-22)
-- Three inplay bot configs updated based on funnel analysis of live_match_snapshots:
--   inplay_e: window tightened 25-50 → 25-30 (minute-bucket ROI: ≤30 = +17.7%, later = negative)
--   inplay_m: OU threshold 2.40 → 2.20 (only 10% of candidates passed 2.40; 18% pass 2.20)
--   inplay_n: window widened 72-80 → 65-82; away-favourite path added (2.2× more candidates)

UPDATE bots SET strategy_description = 'Bet: Under 2.5 goals.
Entry: Minutes 25–30, score ≤1 goal. Real xG data required. Pace ratio (live xG ÷ expected xG by this minute) < 0.70 — the game is running meaningfully below its pre-match rate.
Edge thesis: If a game is genuinely slow-paced this early — not just unlucky — the Bayesian posterior drops the expected goal total below what the live Under market implies. Window tightened to 25–30 (was 25–50): minute-bucket analysis showed ROI of +17.7% in ≤30 min; every later bucket was negative. The early window is the edge; the later one was noise.' WHERE name = 'inplay_e';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 30–60, score is exactly 1-0 or 0-1. Pre-match BTTS prob ≥ 0.48 and O2.5 prob ≥ 0.45 (an open, both-teams-attack type match). Live OU 2.5 over odds ≥ 2.20.
Edge thesis: After a first goal in an open game, soft bookmakers anchor too heavily on the 1-0 scoreline and drift OU 2.5 toward Under. The Bayesian posterior over the remaining xG — updated with the one observed goal — keeps equaliser + second-goal probability higher than the market implies. OU floor lowered from 2.40 to 2.20 (2026-05-22): funnel showed only 10% of candidates passed 2.40; 18% pass 2.20; the edge check still gates on actual model value.' WHERE name = 'inplay_m';

UPDATE bots SET strategy_description = 'Bet: Strong favourite (home or away) to win.
Entry: Minutes 65–82, score is level (0-0 or 1-1). The pre-match favourite (win prob ≥ 0.62 for either side) has drifted to live win odds ≥ 2.20 because the market is anchoring on the scoreline.
Edge thesis: A genuine quality favourite at level score in the final 25 minutes still has more than enough time to win, but the live market overshoots the "time pressure" penalty. Bivariate Poisson on remaining minutes confirms the favourite''s win probability exceeds the implied market odds by ≥ 3%. Window expanded from 72–80 to 65–82 and away-favourite path added (2026-05-22): funnel analysis showed 2.2× more candidates with the wider window.' WHERE name = 'inplay_n';
