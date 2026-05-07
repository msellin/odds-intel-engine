-- Add blocked_shots columns to match_stats (fouls, offsides, saves, pass_accuracy already exist from 009)
ALTER TABLE match_stats
    ADD COLUMN IF NOT EXISTS blocked_shots_home    integer,
    ADD COLUMN IF NOT EXISTS blocked_shots_away    integer;

-- Add extended stat columns to live_match_snapshots for live tracking
ALTER TABLE live_match_snapshots
    ADD COLUMN IF NOT EXISTS fouls_home            smallint,
    ADD COLUMN IF NOT EXISTS fouls_away            smallint,
    ADD COLUMN IF NOT EXISTS offsides_home         smallint,
    ADD COLUMN IF NOT EXISTS offsides_away         smallint,
    ADD COLUMN IF NOT EXISTS saves_home            smallint,
    ADD COLUMN IF NOT EXISTS saves_away            smallint,
    ADD COLUMN IF NOT EXISTS blocked_shots_home    smallint,
    ADD COLUMN IF NOT EXISTS blocked_shots_away    smallint,
    ADD COLUMN IF NOT EXISTS pass_accuracy_home    smallint,
    ADD COLUMN IF NOT EXISTS pass_accuracy_away    smallint;
