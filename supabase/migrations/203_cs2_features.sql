-- New per-team match features computed in cs2_features.py.
-- They accumulate on cs2_predictions / cs2_upcoming_matches for retraining;
-- the current production logistic doesn't use them yet.

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS form_momentum1     FLOAT,
    ADD COLUMN IF NOT EXISTS form_momentum2     FLOAT,
    ADD COLUMN IF NOT EXISTS days_since_match1  INTEGER,
    ADD COLUMN IF NOT EXISTS days_since_match2  INTEGER,
    ADD COLUMN IF NOT EXISTS opp_strength_avg1  FLOAT,
    ADD COLUMN IF NOT EXISTS opp_strength_avg2  FLOAT,
    ADD COLUMN IF NOT EXISTS h2h_team1_win_pct  FLOAT,
    ADD COLUMN IF NOT EXISTS h2h_count          INTEGER;

ALTER TABLE cs2_predictions
    ADD COLUMN IF NOT EXISTS form_momentum1     FLOAT,
    ADD COLUMN IF NOT EXISTS form_momentum2     FLOAT,
    ADD COLUMN IF NOT EXISTS days_since_match1  INTEGER,
    ADD COLUMN IF NOT EXISTS days_since_match2  INTEGER,
    ADD COLUMN IF NOT EXISTS opp_strength_avg1  FLOAT,
    ADD COLUMN IF NOT EXISTS opp_strength_avg2  FLOAT,
    ADD COLUMN IF NOT EXISTS h2h_team1_win_pct  FLOAT,
    ADD COLUMN IF NOT EXISTS h2h_count          INTEGER;
