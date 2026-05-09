-- Register two new inplay bots: M (Equalizer Magnet), N (Late Favourite Push)
-- ensure_bots() also registers them lazily on next cycle, but registering via
-- migration makes the bot row available before the first live cycle runs and
-- gives the migration trail a clear marker for when each bot started trading.
INSERT INTO bots (name, strategy, description)
VALUES
    ('inplay_m',
     'Equalizer Magnet — 1-0 or 0-1 min 30-60, BTTS prematch ≥ 0.48, live OU25 ≥ 3.0, bet Over 2.5',
     'Equalizer Magnet — 1-0 or 0-1 min 30-60, BTTS prematch ≥ 0.48, live OU25 ≥ 3.0, bet Over 2.5'),
    ('inplay_n',
     'Late Favourite Push — 0-0/1-1 min 72-80, home_win_prob ≥ 0.65, live home odds drifted ≥ 2.20, bet Home',
     'Late Favourite Push — 0-0/1-1 min 72-80, home_win_prob ≥ 0.65, live home odds drifted ≥ 2.20, bet Home')
ON CONFLICT (name) DO UPDATE
    SET description = EXCLUDED.description,
        strategy = EXCLUDED.strategy;
