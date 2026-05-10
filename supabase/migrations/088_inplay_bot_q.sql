-- Register inplay_q (Red Card Overreaction Over 2.5).
-- Same migration pattern as 082 (M/N): name + strategy + description.
-- ensure_bots() also registers it lazily on next live cycle, but having the
-- bot row ready before the first cycle keeps the migration trail clean and
-- gives a deterministic start-of-trading marker.
INSERT INTO bots (name, strategy, description)
VALUES
    ('inplay_q',
     'Red Card Overreaction — red 15-55, total ≤ 1, 11-man possession ≥ 55%, live OU2.5 over ≥ 2.30, bet Over 2.5',
     'Red Card Overreaction — red 15-55, total ≤ 1, 11-man possession ≥ 55%, live OU2.5 over ≥ 2.30, bet Over 2.5')
ON CONFLICT (name) DO UPDATE
    SET description = EXCLUDED.description,
        strategy = EXCLUDED.strategy;
