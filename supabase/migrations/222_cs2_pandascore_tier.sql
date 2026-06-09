-- PandaScore exposes a curated `tier` field on /tournaments/past (a/b/c/d).
-- This is the only reliable tier classifier we've found — our v5 regex tier
-- was too noisy (85% in default bucket). Adding tournament_tier + prizepool
-- as columns so sneak peek v7+ can use them directly.

ALTER TABLE cs2_pandascore_matches
    ADD COLUMN IF NOT EXISTS tournament_id   INTEGER,
    ADD COLUMN IF NOT EXISTS tournament_tier TEXT,    -- 'a' | 'b' | 'c' | 'd' | NULL
    ADD COLUMN IF NOT EXISTS prizepool       TEXT;    -- "$1,000,000" raw string

CREATE INDEX IF NOT EXISTS cs2_pandascore_matches_tier_idx
    ON cs2_pandascore_matches (tournament_tier);
