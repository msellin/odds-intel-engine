-- Register three new inplay bots: I (Favourite Stall), J (Goal Debt), L (Goal Contagion)
INSERT INTO bots (name, description)
VALUES
    ('inplay_i', 'Favourite Stall — strong fav 0-0 min 42-65, live fav odds drifted ≥ 3.0'),
    ('inplay_j', 'Goal Debt Over 1.5 — 0-0 min 30-52, prematch O25 ≥ 0.62, live OU1.5 ≥ 2.85'),
    ('inplay_l', 'Goal Contagion — first goal min 15-35 in high-λ match, Over 2.5 at remaining Poisson')
ON CONFLICT (name) DO UPDATE
    SET description = EXCLUDED.description;
