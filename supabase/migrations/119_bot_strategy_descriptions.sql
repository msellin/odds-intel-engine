-- BOT-STRATEGY-DESCRIPTIONS (2026-05-22)
-- Add strategy_description TEXT to bots table — plain-English explanation of
-- what each bot bets on, who we back, and the core edge thesis.
-- Separate from description (changelog/technical notes).

ALTER TABLE bots ADD COLUMN IF NOT EXISTS strategy_description TEXT;

COMMENT ON COLUMN bots.strategy_description IS
  'Plain-English explanation of the bot strategy: what it bets on, entry conditions, and why there is edge.';

-- ── In-play bots ──────────────────────────────────────────────────────────────

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 20–40, score 0-0 or 1-0 (low-goal state). The xG meter is running hotter than the scoreline suggests — combined live xG already elevated vs prematch pace, shots on target ≥ 3.
Edge thesis: Market prices OU 2.5 based on the visible 0-0/1-0 scoreline. Bayesian posterior xG (prematch prior updated by actual pace) predicts more goals than the market implies. We bet over when the model edge vs live odds is ≥ 1.5%.'
WHERE name = 'inplay_a';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 12–50, score is 1-0 (either team leading). The trailing team is pressing — showing xG ≥ 0.20 or SoT ≥ 1. Prematch BTTS prob ≥ 42% (this is a match where both teams were expected to score).
Edge thesis: Trailing-team pressure signals an equaliser is coming. With prematch xG elevated, the Bayesian model gives enough remaining lambda for 2 more goals. Bet Over 2.5 when the market hasn't priced in the comeback momentum.'
WHERE name = 'inplay_b';

UPDATE bots SET strategy_description = 'Bet: The pre-match favourite to win (1X2).
Entry: Minutes 25–70, the pre-match favourite is losing by 1 goal. They have possession dominance (≥ 52% home, ≥ 55% away) and xG or SoT advantage. No red cards.
Edge thesis: Market drifts the favourite''s win odds up after they concede. But Poisson on remaining xG shows they''re still likely to equalise and win — the drift overshoots. We back the quality team to come back.'
WHERE name = 'inplay_c';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 48–80, score ≤ 1 goal. Game has been creating chances (xG ≥ 0.7 or SoT ≥ 6). OU 2.5 over odds ≥ 2.10. Prematch O25 probability ≥ 46%.
Edge thesis: Late in a busy game with few goals — the remaining lambda is compressed into fewer minutes. Bayesian model says there''s still enough pace for 2+ more goals. Market underprices Over 2.5 because it anchors on the low current score.'
WHERE name = 'inplay_d';

UPDATE bots SET strategy_description = 'Bet: Under 2.5 goals.
Entry: Minutes 25–50, score ≤ 1 goal, real xG only (no proxy). Live xG pace is less than 70% of the prematch expected pace at this minute. Corners also tracking below pace.
Edge thesis: The match has gone cold — xG and corner data confirm this isn''t just an unlucky scoreline. Market still prices Under 2.5 as though the game could burst open. We bet Under when the model edge is ≥ 3%.'
WHERE name = 'inplay_e';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 30–70, score ≤ 1 goal. At least 2 corners gained in the last 10 minutes (corner cluster = sustained final-third pressure). OU 2.5 over odds ≥ 2.10. Prematch O25 ≥ 45%.
Edge thesis: Corner clusters indicate set-piece-heavy pressure that the live xG model under-weights. Three or more corners in a 10-min window historically precedes goals. Market prices OU on the scoreline, not the corner trend — we bet the pressure.'
WHERE name = 'inplay_g';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 (or Over 1.5 fallback).
Entry: Minutes 46–55 (just after halftime). Score is 0-0. But the first half had real xG ≥ 0.7 or SoT ≥ 6 — lots of chances created, just no goals. OU 2.5 over odds > 2.30.
Edge thesis: HT 0-0 drifts the market heavily toward Under. But when the first half was genuinely busy (xG says so), goals are coming in the second half. Managers make changes, urgency increases. Market overshoots the Under — we back Over.'
WHERE name = 'inplay_h';

UPDATE bots SET strategy_description = 'Bet: The strong pre-match favourite to win (1X2).
Entry: Minutes 42–65, score is 0-0. The pre-match favourite had ≥ 62% win probability. Live odds have drifted to 3.0+ as the market panics over the blank scoreline.
Edge thesis: Market anchors heavily on the 0-0 and underweights the remaining ~45 minutes where quality advantage accumulates. Bivariate Poisson on remaining time still gives the favourite a higher win probability than 1/3.0 = 33%. We buy the quality drift.'
WHERE name = 'inplay_i';

UPDATE bots SET strategy_description = 'Bet: Over 1.5 goals.
Entry: Minutes 30–52, score is 0-0. High-expectation match (prematch O25 ≥ 55%). Live Over 1.5 odds have drifted to 2.50+ because of the blank scoreline.
Edge thesis: A "goal debt" has built up — Bayesian model says the prematch xG implies 2+ goals are still very likely even with 40–60 minutes left. The market overprices the 0-0 state and leaves Over 1.5 at odds we like. Bet: need 2 goals, model says they come.'
WHERE name = 'inplay_j';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Within ~4 minutes of the first goal being scored (score just turned 1-0), at minute 15–35 in an attacking match (prematch O25 ≥ 55%).
Edge thesis: Empirically (Dixon & Robinson 1998), the scoring rate spikes for ~8 minutes after the first goal in an open game. Market reprices OU 2.5 upward but often lags behind. We buy Over 2.5 immediately after the first goal while the market is still repricing.'
WHERE name = 'inplay_l';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: Minutes 30–60, score is 1-0. Attacking-type match (prematch BTTS ≥ 48%, O25 ≥ 45%). Live OU 2.5 over odds ≥ 2.40 (market has drifted to Under because the scoreline looks dangerous for Over).
Edge thesis: When a "both-attack" match hits 1-0 in the middle third, the market anchors on the scoreline and drifts OU 2.5 toward Under. But Bayesian model says the trailing team equalises and a third goal follows. Equalizer + one more = Over 2.5.'
WHERE name = 'inplay_m';

UPDATE bots SET strategy_description = 'Bet: Home team to win (1X2).
Entry: Minutes 72–80, score is 0-0 or 1-1 (level). Pre-match home favourite (≥ 65% win prob). Live home win odds ≥ 2.20 (drifted up from prematch ~1.45).
Edge thesis: Strong home favourites at 0-0 or 1-1 in the last 10–18 minutes see their live odds drift upward as the market worries about time. But bivariate Poisson on the remaining minutes still gives home a higher win probability than 1/2.20 = 45%. Final push: home quality advantage concentrates in late pressure.'
WHERE name = 'inplay_n';

UPDATE bots SET strategy_description = 'Bet: The pre-match underdog to win (1X2).
Entry: Minutes 25–55, the pre-match underdog (prematch win prob < 35%) is leading 1-0. Live win odds ≥ 2.80 (market still not backing them).
Edge thesis: When a genuine underdog leads 1-0, the market prices them as though the scoreline is a fluke — live odds stay high. Bivariate Poisson on remaining minutes: the underdog is defending a 1-0 lead with real xG data; P(hold lead) is higher than 1/2.80 implies. We back them to hold.'
WHERE name = 'inplay_o';

UPDATE bots SET strategy_description = 'Bet: The team that just equalised to win (1X2).
Entry: Minutes 30–75, within 4 minutes of a team equalising to 1-1. Live win odds ≥ 2.20 for the equalising team.
Edge thesis: After equalising, a team has momentum — the market anchors on the new 1-1 draw and suppresses the equaliser''s win odds. Bivariate Poisson says the team that just scored is on an upswing and has better-than-market win probability for the remaining time. Back the momentum.'
WHERE name = 'inplay_p';

UPDATE bots SET strategy_description = 'Bet: Over 2.5 goals.
Entry: A red card happened in minutes 15–55. Score ≤ 1 goal. The 11-man team has ≥ 55% possession. Live OU 2.5 over odds > 2.30 (market scared the red card will suppress goals).
Edge thesis: All other bots skip red-card matches — this is the only one that targets them. After a red card, the 11-man team dominates territory and creates chances. Market drifts OU 2.5 toward Under, but the 11-man pressure means goals are more likely, not less. We buy the Over when model edge ≥ 3%.'
WHERE name = 'inplay_q';

-- ── Pre-match combo/acca bots ─────────────────────────────────────────────────

UPDATE bots SET strategy_description = 'Bet: A single 5-leg accumulator (all 5 must win for a payout).
Entry: Each morning, scans all predictions for legs with ≥ 8% edge. Requires at least one OU 1.5 over leg in the pool (the edge driver). Combines 5 legs from different matches.
Markets: btts yes, OU 2.5 over/under, OU 3.5 over, OU 1.5 over.
Edge thesis: Backtest (3yr, N=5): on days where OU 1.5 over qualifies, leg win rate is ~73% — much higher than the 44% on non-OU15 days. Combined edge on 5 such legs is +EV despite the all-win requirement. Combined odds typically 15–40x.'
WHERE name = 'bot_acca_value';

UPDATE bots SET strategy_description = 'Bet: A single 5-leg accumulator using only well-validated markets.
Entry: Same as bot_acca_value but restricts legs to OU 2.5, OU 3.5, BTTS (no 1X2). Still requires OU 1.5 over in pool as the edge filter trigger. 5 legs, all must win.
Edge thesis: More conservative market selection — avoids 1X2 variance. Proven markets have the most reliable Poisson-model edge. Backtest shows OU 2.5/3.5/BTTS legs sustain the +EV on OU15 days while reducing selection noise.'
WHERE name = 'bot_acca_proven';

UPDATE bots SET strategy_description = 'Bet: A fours_up system — 6 tickets total: one 5-fold + five 4-folds. Tolerates one losing leg (the five 4-folds that exclude the loser still pay).
Entry: Same as bot_acca_value: 5 legs, ≥ 8% edge each, OU 1.5 over required in pool.
Edge thesis: Same edge driver as bot_acca_value, but lower variance. One bad leg doesn''t kill the whole bet — 5 of the 6 tickets still win. Trade-off: total payout lower because stake is split across 6 tickets. Better risk management for days where edge is high but one leg is uncertain.'
WHERE name = 'bot_combo_system';

UPDATE bots SET strategy_description = 'Bet: A fours_up system (6 tickets: 5-fold + five 4-folds) using only proven markets.
Entry: Same as bot_acca_proven: 5 legs from OU 2.5, OU 3.5, BTTS. OU 1.5 over required in pool. System structure identical to bot_combo_system.
Edge thesis: Combines the conservative market selection of bot_acca_proven with the variance reduction of the fours_up structure. Best risk/reward for days where market conditions are good but you want insurance against a single bad leg.'
WHERE name = 'bot_combo_proven_system';
