-- SPECIALIST-BOTS-WHITELIST 2026-05-28
--
-- Three reforms + two new bots based on 2023-2026 backtest (119K matches, clean
-- data — women's/youth/cup leagues excluded). Key findings:
--
-- 1. bot_draw_specialist: tier_filter=[2,3,4] blocked Brazil Serie A and Austria
--    Bundesliga (T1, both strongly positive) while including Hungary NB II (-66.8%),
--    Portugal Segunda Liga (-88.4%), Slovenia 2.SNL (-35%), Slovakia (-53.6%) etc.
--    Fix: replace with league_name_filter (12 confirmed leagues).
--
-- 2. bot_dnb_away_value: tier_filter=[1,2,3] was blocking England League Two (T4)
--    which was the STRONGEST DNB away signal at +25.4%/99 bets with zero coverage.
--    Also bleeding ~-350 PnL on Italy (-16.9%), Netherlands (-19.8%), France (-16.6%),
--    Turkey (-18.9%), Poland Ekstraklasa (-16.1%), Belgium (-10.8%).
--    Fix: replace with league_name_filter (5 confirmed leagues).
--
-- 3. bot_dnb_home_value: broad T1-2 filter generating losses across major EU leagues.
--    Fix: replace with league_name_filter (5 confirmed leagues).
--
-- New bots:
--
-- 4. bot_under25_specialist: OU2.5 under in 3 confirmed leagues (Eng Championship
--    +19%, Poland Ekstraklasa +25.9%, Sweden Ettan Norra +33.3%). bot_ou25_global
--    fires here too but is -6.2% ROI because Spain/Portugal/France drag it down.
--
-- 5. bot_sweden_over25: paper bot for Over 2.5 in Superettan + Allsvenskan.
--    Both positive but below 30-bet validation threshold (23+15 bets). Accumulating
--    live evidence; graduate at 30+ settled bets + >=+5% ROI.

-- Reformed bots — update descriptions (config changes are in Python; no schema change needed)
UPDATE bots
SET description = 'DRAW-LEAGUE-WHITELIST 2026-05-28: draw specialist — 12 leagues confirmed by 2023-2026 backtest. Replaced tier_filter=[2,3,4] with explicit league_name_filter. Drops Hungary/Portugal/Slovenia/Bulgaria leakers; adds Austria Bundesliga + Brazil Serie A (T1, previously blocked).'
WHERE name = 'bot_draw_specialist';

UPDATE bots
SET description = 'DNB-AWAY-WHITELIST 2026-05-28: away DNB in 5 confirmed leagues only. Dropped tier_filter=[1,2,3]; kills Italian/French/Turkish bleeding (~-350 PnL). Adds England League Two (T4, +25.4% — was blocked by tier filter).'
WHERE name = 'bot_dnb_away_value';

UPDATE bots
SET description = 'DNB-HOME-WHITELIST 2026-05-28: home DNB in 5 confirmed leagues. Replaced tier_filter=[1,2] with explicit whitelist from 2023-2026 backtest.'
WHERE name = 'bot_dnb_home_value';

-- New bots
INSERT INTO bots (
    name,
    strategy,
    description,
    strategy_description,
    starting_bankroll,
    current_bankroll,
    is_active,
    maturity_label
) VALUES
(
    'bot_under25_specialist',
    'OU2.5 under — England Championship / Poland Ekstraklasa / Sweden Ettan Norra',
    'UNDER25-SPECIALIST 2026-05-28: OU2.5 under in 3 confirmed leagues (Eng Championship +19%, Poland Ekstraklasa +25.9%, Sweden Ettan Norra +33.3%). Subset of bot_ou25_global filtered to profitable leagues only.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
),
(
    'bot_sweden_over25',
    'OU2.5 over — Sweden Superettan + Allsvenskan (paper)',
    'SWEDEN-OVER25 2026-05-28: paper bot on Over 2.5 in Swedish top 2 divisions. Below 30-bet threshold (23+15 bets). Graduate at 30+ settled + >=+5% ROI.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
