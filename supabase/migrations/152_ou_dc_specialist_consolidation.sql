-- OU-DC-CONSOLIDATION 2026-05-29
--
-- Consolidate all OU bots into bot_ou_specialist (3 profiles) and all DC bots
-- into bot_dc_specialist (3 profiles). Reduces bot count by 4.
--
-- bot_ou_specialist profiles:
--   "Under 2.5 Specialist" — Eng Championship, Poland, Sweden Ettan Norra (confirmed)
--   "Over 2.5 Sweden"      — Superettan + Allsvenskan (paper, below threshold)
--   "Over 3.5 Global"      — all leagues, 14% edge, +35% backtest (no whitelist yet)
--
-- bot_dc_specialist profiles (expanded):
--   "X2 Value"    — Brazil Serie B, China Super League (confirmed)
--   "1X Israel"   — Israel Liga Leumit (confirmed)
--   "DC Global"   — all leagues, all selections (data collection, was bot_dc_value)

-- 1. Retire bots being absorbed
UPDATE bots
SET is_active = false, retired_at = NOW(),
    retired_reason = 'OU-DC-CONSOLIDATION 2026-05-29: merged into bot_ou_specialist with Under 2.5 Specialist + Over 2.5 Sweden + Over 3.5 Global profiles.'
WHERE name = 'bot_ou25_specialist';

UPDATE bots
SET is_active = false, retired_at = NOW(),
    retired_reason = 'OU-DC-CONSOLIDATION 2026-05-29: merged into bot_ou_specialist as Over 3.5 Global profile.'
WHERE name = 'bot_ou35_attacking';

UPDATE bots
SET is_active = false, retired_at = NOW(),
    retired_reason = 'OU-DC-CONSOLIDATION 2026-05-29: merged into bot_dc_specialist as DC Global profile.'
WHERE name = 'bot_dc_value';

UPDATE bots
SET is_active = false, retired_at = NOW(),
    retired_reason = 'OU-DC-CONSOLIDATION 2026-05-29: redundant subset of bot_dc_value. Retired without merge — fully covered by DC Global profile in bot_dc_specialist.'
WHERE name = 'bot_dc_strong_fav';

-- 2. Create bot_ou_specialist
INSERT INTO bots (name, strategy, description, strategy_description, starting_bankroll, current_bankroll, is_active, maturity_label)
VALUES (
    'bot_ou_specialist',
    'OU all markets — under/over specialist with per-strategy profiles',
    'OU-SPECIALIST 2026-05-29: consolidates all OU bots. Profile "Under 2.5 Specialist": Eng Championship (+19%), Poland (+25.9%), Sweden Ettan Norra (+33.3%). Profile "Over 2.5 Sweden": Superettan+Allsvenskan (paper). Profile "Over 3.5 Global": all leagues, +35% backtest, 14% edge, no whitelist yet.',
    NULL, 1000.00, 1000.00, true, 'beta'
);

-- 3. Update bot_dc_specialist to reflect 3rd profile
UPDATE bots
SET description = 'DC-SPECIALIST 2026-05-29: all DC bots consolidated. Profile "X2 Value": Brazil Serie B (+20.1%), China Super League (+13.7%). Profile "1X Israel": Israel Liga Leumit (+13.3%). Profile "DC Global": all leagues/selections (data collection, absorbs bot_dc_value).'
WHERE name = 'bot_dc_specialist';
