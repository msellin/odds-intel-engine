-- v8 bot bankroll seed. v8 = v7 stacking + kd_diff (team K/D feature from
-- /stats/teams bulk page + roster aggregation fallback). Sneak peek lift:
-- +0.7pp AUC on full sample, +2pp on K/D-covered subset.
--
-- FIX 2026-06-10 (round 2): bots.strategy is also NOT NULL — copying the
-- shape of 226_cs2_v7_bot.sql which is the closest sibling.

INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES ('bot_cs2_v8',
    'CS2 v8 stacking (v7 + kd_diff team K/D feature)',
    'v7 stacking + team K/D diff from /stats/teams bulk + roster aggregate fallback. +0.7pp AUC on full sample, +2pp on K/D-covered subset.',
    1000.00, 1000.00, TRUE)
ON CONFLICT (name) DO NOTHING;
