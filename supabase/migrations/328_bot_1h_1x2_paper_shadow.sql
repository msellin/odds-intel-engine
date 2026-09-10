-- USE-COLLECTED-MARKETS #2 / FIRST-HALF-1X2-PAPER — register the first-half result
-- (1X2 at half time) line-shop strategy as a SHADOW bot. A three-way market we sweep
-- on every book but never modelled. Settles from the HALF-TIME score
-- (matches.ht_score_home/away, 98% covered) — no settlement gap. Sharp anchor =
-- Shin-de-vigged Pinnacle 1H triple. shadow_bets only, EUR 10 nominal.
-- (over_under_1h was deferred first: Pinnacle 1H O/U is sparse upcoming + mostly
--  Asian lines needing push/half-win logic — a follow-up.)

ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY[
               'morning','midday','pre_ko','corners_paper',
               'coolbet_ou_model','coolbet_1x2_model','ou35_model',
               'coolbet_trigger','unibet_trigger','team_total_paper','fh_1x2_paper'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES (
    'bot_1h_1x2_paper_shadow_v1',
    'Shadow bot for the first-half result (1X2 at HT) line-shop strategy — EUR10 pick when the best Epicbet/Betano/Unibet 1H 1X2 price beats Shin-de-vigged Pinnacle. Settled from the half-time score (no gap).',
    'fh_1x2_line_shop',
    'Fires on 1x2_1h (home/draw/away) for upcoming fixtures. Fair value = Shin-de-vigged three-way Pinnacle (workers.model.devig). Best reachable book (Epicbet/Betano/Unibet) when price x P_sharp - 1 >= floor (default 0). shadow_bets only. Settled from matches.ht_score_home/away (HT result). USE-COLLECTED-MARKETS: harvest a market we collect but never modelled; accumulate then multi-dim sweep.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental', description=EXCLUDED.description,
    strategy=EXCLUDED.strategy, strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();
