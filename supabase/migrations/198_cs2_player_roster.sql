-- CS2 player quality + roster change signals
-- Added by cs2_elo_scanner.py v2: live ELO, player ratings, roster change flags
ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS roster_change1  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS roster_change2  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS roster_note1    TEXT,
    ADD COLUMN IF NOT EXISTS roster_note2    TEXT,
    ADD COLUMN IF NOT EXISTS player_rating1  FLOAT,   -- avg HLTV rating, team1 last known lineup
    ADD COLUMN IF NOT EXISTS player_rating2  FLOAT;
