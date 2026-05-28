-- OU25-SPECIALIST 2026-05-29
--
-- Combine bot_under25_specialist + bot_sweden_over25 + retire bot_ou25_global
-- into bot_ou25_specialist with two named strategy profiles:
--   "Under 2.5 Specialist" — Eng Championship (+19%), Poland (+25.9%), Sweden Ettan Norra (+33.3%)
--   "Over 2.5 Sweden"      — Superettan (+51.2% paper) + Allsvenskan (+40% paper)
--
-- Per-profile ROI query:
--   SELECT strategy_profile, COUNT(*), SUM(pnl) / (COUNT(*) * 10.0) * 100 AS roi
--   FROM simulated_bets WHERE bot_id = (SELECT id FROM bots WHERE name = 'bot_ou25_specialist')
--   GROUP BY strategy_profile;

-- 1. Retire bot_ou25_global (broad -6.2% ROI — replaced by specialist)
UPDATE bots
SET
    is_active      = false,
    retired_at     = NOW(),
    retired_reason = 'OU25-SPECIALIST 2026-05-29: -6.2% ROI on broad coverage. Replaced by bot_ou25_specialist with confirmed league whitelists.'
WHERE name = 'bot_ou25_global';

-- 2. Retire bot_under25_specialist and bot_sweden_over25 (merged into bot_ou25_specialist)
UPDATE bots
SET
    is_active      = false,
    retired_at     = NOW(),
    retired_reason = 'OU25-SPECIALIST 2026-05-29: merged into bot_ou25_specialist with Under 2.5 Specialist and Over 2.5 Sweden profiles. Historical bet_id linkage preserved.'
WHERE name IN ('bot_under25_specialist', 'bot_sweden_over25');

-- 3. Create bot_ou25_specialist
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
    'bot_ou25_specialist',
    'OU 2.5 — under specialist + over Sweden with per-strategy league whitelists',
    'OU25-SPECIALIST 2026-05-29: merges bot_under25_specialist + bot_sweden_over25. Profile "Under 2.5 Specialist": Eng Championship (+19%), Poland Ekstraklasa (+25.9%), Sweden Ettan Norra (+33.3%). Profile "Over 2.5 Sweden": Superettan (+51.2% paper) + Allsvenskan (+40% paper) — accumulating live evidence toward 30-bet graduation threshold. Per-profile ROI queryable via strategy_profile column on simulated_bets.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
