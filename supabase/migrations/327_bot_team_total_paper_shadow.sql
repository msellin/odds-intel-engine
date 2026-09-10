-- USE-COLLECTED-MARKETS-2026-09-10 / TEAM-TOTAL-PAPER — register the first
-- "collected-but-unused market" as a proper SHADOW bot. We already sweep
-- team-total prices on every book (Epicbet/Betano/Unibet + Pinnacle) but have
-- never modelled them. Full-match team totals are the cleanest of the unused
-- markets: a `team_total_{side}_{line}` settles from the FINAL SCORE, which
-- exists for every finished match — so, unlike corners/cards, there is NO
-- settlement-coverage gap. Pinnacle prices the .5 lines (_05/_15/_25/_35),
-- giving a real sharp anchor.
--
-- Strategy (line-shop vs sharp anchor, same shape as the corners paper bot):
-- best reachable team-total price (Epicbet/Betano/Unibet) vs de-vigged two-way
-- Pinnacle; record a paper pick when price*devig_p-1 >= floor. EUR 10 nominal,
-- shadow_bets only — never places, never affects bankroll. Settled from
-- matches.score_home/score_away by workers/jobs/team_total_paper_bot.py.

-- Allow the bot's dedicated cohort value (one row per match/line/side via the
-- existing unique (shadow_cohort, bot_id, match_id, market, selection)).
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY[
               'morning','midday','pre_ko','corners_paper',
               'coolbet_ou_model','coolbet_1x2_model','ou35_model',
               'coolbet_trigger','unibet_trigger','team_total_paper'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES (
    'bot_team_total_paper_shadow_v1',
    'Shadow bot for the team-total line-shop strategy — logs a hypothetical EUR10 pick when the best Epicbet/Betano/Unibet team-total O/U price beats de-vigged Pinnacle fair value. Settled from the final score (no coverage gap).',
    'team_total_line_shop',
    'Fires on full-match team_total_<side>_<line> markets (excludes first-half) for upcoming fixtures. Fair value = de-vigged two-way Pinnacle. Records the best price among reachable books (Epicbet/Betano/Unibet) when price x devig_p - 1 >= floor (default 0). Writes only to shadow_bets — never places or affects bankroll. Settled from matches.score_home/score_away (over/under, .5 lines never push). Purpose: harvest a market we already collect prices for; accumulate forward then multi-dimensionally sweep (side x line x edge band x book) for a profitable slice.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE,
    maturity_label = 'experimental',
    description = EXCLUDED.description,
    strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL,
    retired_reason = NULL,
    updated_at = NOW();
