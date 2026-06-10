-- v8 bot bankroll seed. v8 = v7 stacking + kd_diff (team K/D feature from
-- /stats/teams bulk page + roster aggregation fallback). Sneak peek lift:
-- +0.7pp AUC on full sample, +2pp on K/D-covered subset.

INSERT INTO bots (bot_name, starting_bankroll, current_bankroll, is_active, created_at)
VALUES ('bot_cs2_v8', 1000.00, 1000.00, true, NOW())
ON CONFLICT (bot_name) DO NOTHING;
