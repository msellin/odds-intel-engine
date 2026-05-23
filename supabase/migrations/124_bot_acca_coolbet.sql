-- COMBO-NEW (2026-05-23): register bot_acca_coolbet, a straight 5-fold
-- variant whose candidate pool is restricted to matches in leagues that
-- exist on Coolbet. The point: produce one acca per day that the user
-- can actually place (everything outside Coolbet's coverage was paper-
-- only by definition).
--
-- Config lives in workers/jobs/acca_bot.py ACCA_VARIANTS — gates are
-- identical to bot_acca_value (8% per-leg edge, odds 1.40-2.50, OU15
-- required, N=5) plus coolbet_only=True which filters via
-- _coolbet_match_ids() against workers/automation/coolbet_leagues_cache.json.

INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
VALUES (
    'bot_acca_coolbet',
    'Straight 5-leg acca, candidate matches restricted to leagues offered on Coolbet so the combo is actually placeable. Same edge/odds gates as bot_acca_value.',
    1000.00, 1000.00, true
)
ON CONFLICT (name) DO NOTHING;
