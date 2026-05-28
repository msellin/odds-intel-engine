-- MULTI-STRATEGY-BOTS 2026-05-29
--
-- Support for "fat" bots with named strategy profiles: one bot entity in the DB,
-- multiple internal profiles (each with their own league whitelist, thresholds,
-- selection filter). Each bet stores strategy_profile so ROI per profile is
-- queryable directly:
--
--   SELECT strategy_profile, COUNT(*), SUM(pnl) / (COUNT(*) * 10.0) * 100 AS roi
--   FROM simulated_bets WHERE bot_id = (SELECT id FROM bots WHERE name = 'bot_dnb_specialist')
--   GROUP BY strategy_profile;
--
-- First use case: bot_dnb_specialist merges bot_dnb_home_value + bot_dnb_away_value.
-- Home and Away strategies have different league whitelists, edge thresholds and
-- odds ranges — profile="DNB Home" and profile="DNB Away" distinguish them.

-- 1. Add strategy_profile to simulated_bets and shadow_bets
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS strategy_profile TEXT DEFAULT NULL;

ALTER TABLE shadow_bets
    ADD COLUMN IF NOT EXISTS strategy_profile TEXT DEFAULT NULL;

-- 2. Retire the two separate DNB bots — their bets stay in history
UPDATE bots
SET
    is_active    = false,
    retired_at   = NOW(),
    retired_reason = 'MULTI-STRATEGY-BOTS 2026-05-29: merged into bot_dnb_specialist with DNB Home and DNB Away profiles. Historical bet_id linkage preserved.'
WHERE name IN ('bot_dnb_home_value', 'bot_dnb_away_value');

-- 3. Create bot_dnb_specialist
INSERT INTO bots (
    name,
    strategy,
    description,
    strategy_description,
    starting_bankroll,
    current_bankroll,
    is_active,
    maturity_label
) VALUES (
    'bot_dnb_specialist',
    'DNB — home + away specialist with per-strategy league whitelists',
    'MULTI-STRATEGY-BOTS 2026-05-29: merges DNB home and away signals into one bot with two named profiles. Profile "DNB Home": Austria Bundesliga (+19%), Mexico Liga MX (+43.8%), Russia (+11.5%), Israel Liga Leumit (+11.4%), Uruguay (+11.5%). Profile "DNB Away": England League Two (+25.4%), Sweden Allsvenskan (+20.6%), Brazil Serie B (+26.6%), England Championship (+10.3%), Argentina Primera Nacional (+13.2%). Per-profile ROI queryable via strategy_profile column on simulated_bets.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
