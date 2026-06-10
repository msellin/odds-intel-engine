-- v8 bot bankroll seed. v8 = v7 stacking + kd_diff (team K/D feature from
-- /stats/teams bulk page + roster aggregation fallback). Sneak peek lift:
-- +0.7pp AUC on full sample, +2pp on K/D-covered subset.
--
-- FIX 2026-06-10: column on bots is `name` (UNIQUE) not `bot_name`. The
-- original version crashed migration push, which blocked 232/233/234 from
-- applying. The bots table has been keyed on `name` since mig 001.

INSERT INTO bots (name, starting_bankroll, current_bankroll, is_active, created_at)
VALUES ('bot_cs2_v8', 1000.00, 1000.00, true, NOW())
ON CONFLICT (name) DO NOTHING;
