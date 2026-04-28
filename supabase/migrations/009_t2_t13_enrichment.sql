-- =============================================================================
-- OddsIntel — Migration 009: T2–T13 API-Football Enrichment
-- =============================================================================
-- T2  /teams/statistics      → team_season_stats (full season aggregates)
-- T3  /injuries              → match_injuries (per fixture)
-- T4  /fixtures/statistics?half → match_stats half-time columns
-- T5  /odds/live             → odds_snapshots.is_live flag
-- T7  /fixtures/lineups      → matches.lineups_home/away columns
-- T8  /fixtures/events       → match_events.af_event_id (AF source dedup)
-- T9  /standings             → league_standings
-- T10 /fixtures/headtohead   → matches.h2h_raw + summary columns
-- T11 /sidelined             → player_sidelined
-- T12 /fixtures/players      → match_player_stats
-- T13 /transfers             → team_transfers
-- =============================================================================


-- ─── T2: Team Season Statistics ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS team_season_stats (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_api_id             integer NOT NULL,
    league_api_id           integer NOT NULL,
    season                  integer NOT NULL,
    fetched_date            date    NOT NULL,

    -- Form & fixtures
    form                    text,               -- e.g. "WWDLWW"
    played_total            integer,
    played_home             integer,
    played_away             integer,
    wins_total              integer,
    wins_home               integer,
    wins_away               integer,
    draws_total             integer,
    draws_home              integer,
    draws_away              integer,
    losses_total            integer,
    losses_home             integer,
    losses_away             integer,

    -- Goals
    goals_for_total         integer,
    goals_for_home          integer,
    goals_for_away          integer,
    goals_against_total     integer,
    goals_against_home      integer,
    goals_against_away      integer,
    goals_for_avg           numeric(5,2),
    goals_against_avg       numeric(5,2),

    -- Key model features
    clean_sheets_total      integer,
    clean_sheets_home       integer,
    clean_sheets_away       integer,
    failed_to_score_total   integer,
    failed_to_score_home    integer,
    failed_to_score_away    integer,
    clean_sheet_pct         numeric(5,4),       -- computed: clean_sheets / played
    failed_to_score_pct     numeric(5,4),       -- computed: failed_to_score / played

    -- Biggest results
    biggest_win_home        text,               -- e.g. "3-0"
    biggest_win_away        text,
    biggest_loss_home       text,
    biggest_loss_away       text,
    streak_wins             integer,
    streak_draws            integer,
    streak_losses           integer,

    -- Penalty stats
    penalty_scored          integer,
    penalty_missed          integer,
    penalty_total           integer,
    penalty_scored_pct      text,               -- e.g. "75.00%"

    -- Tactical
    most_used_formation     text,               -- most played formation
    formations_jsonb        jsonb,              -- all formations with count

    -- Cards by minute (JSONB — full distribution)
    yellow_cards_by_minute  jsonb,
    red_cards_by_minute     jsonb,

    -- Goals by minute (JSONB)
    goals_for_by_minute     jsonb,
    goals_against_by_minute jsonb,

    -- Full raw payload
    raw                     jsonb,

    created_at              timestamptz DEFAULT now(),
    updated_at              timestamptz DEFAULT now(),

    UNIQUE (team_api_id, league_api_id, season, fetched_date)
);

CREATE INDEX IF NOT EXISTS idx_team_season_stats_team
    ON team_season_stats (team_api_id, season, fetched_date DESC);
CREATE INDEX IF NOT EXISTS idx_team_season_stats_league
    ON team_season_stats (league_api_id, season, fetched_date DESC);


-- ─── T3: Match Injuries ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS match_injuries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id        uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    af_fixture_id   integer,
    team_api_id     integer,
    team_side       text,               -- 'home' or 'away' (resolved at fetch time)
    player_id       integer,
    player_name     text,
    player_type     text,               -- 'Player' or 'Coach'
    status          text,               -- 'Missing Fixture' or 'Questionable'
    reason          text,               -- e.g. 'Hamstring', 'Suspension', 'Illness'
    raw             jsonb,
    created_at      timestamptz DEFAULT now(),

    UNIQUE (match_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_match_injuries_match ON match_injuries (match_id);
CREATE INDEX IF NOT EXISTS idx_match_injuries_team  ON match_injuries (team_api_id, player_id);


-- ─── T4: Half-time stats columns on match_stats ──────────────────────────────

ALTER TABLE match_stats
    ADD COLUMN IF NOT EXISTS shots_home_ht             integer,
    ADD COLUMN IF NOT EXISTS shots_away_ht             integer,
    ADD COLUMN IF NOT EXISTS shots_on_target_home_ht   integer,
    ADD COLUMN IF NOT EXISTS shots_on_target_away_ht   integer,
    ADD COLUMN IF NOT EXISTS possession_home_ht        integer,
    ADD COLUMN IF NOT EXISTS corners_home_ht           integer,
    ADD COLUMN IF NOT EXISTS corners_away_ht           integer,
    ADD COLUMN IF NOT EXISTS fouls_home_ht             integer,
    ADD COLUMN IF NOT EXISTS fouls_away_ht             integer,
    ADD COLUMN IF NOT EXISTS yellow_cards_home_ht      integer,
    ADD COLUMN IF NOT EXISTS yellow_cards_away_ht      integer,
    ADD COLUMN IF NOT EXISTS xg_home_ht                numeric(5,2),
    ADD COLUMN IF NOT EXISTS xg_away_ht                numeric(5,2),
    ADD COLUMN IF NOT EXISTS passes_home_ht            integer,
    ADD COLUMN IF NOT EXISTS passes_away_ht            integer,
    ADD COLUMN IF NOT EXISTS offsides_home_ht          integer,
    ADD COLUMN IF NOT EXISTS offsides_away_ht          integer;

-- Also extend match_stats with the full-match columns that parse_fixture_stats already handles
-- but that were not stored before (shots_on_target, fouls, offsides, saves, passes, pass_accuracy)
ALTER TABLE match_stats
    ADD COLUMN IF NOT EXISTS shots_on_target_home      integer,
    ADD COLUMN IF NOT EXISTS shots_on_target_away      integer,
    ADD COLUMN IF NOT EXISTS fouls_home                integer,
    ADD COLUMN IF NOT EXISTS fouls_away                integer,
    ADD COLUMN IF NOT EXISTS offsides_home             integer,
    ADD COLUMN IF NOT EXISTS offsides_away             integer,
    ADD COLUMN IF NOT EXISTS saves_home                integer,
    ADD COLUMN IF NOT EXISTS saves_away                integer,
    ADD COLUMN IF NOT EXISTS passes_home               integer,
    ADD COLUMN IF NOT EXISTS passes_away               integer,
    ADD COLUMN IF NOT EXISTS pass_accuracy_home        integer,
    ADD COLUMN IF NOT EXISTS pass_accuracy_away        integer,
    ADD COLUMN IF NOT EXISTS yellow_cards_home         integer,
    ADD COLUMN IF NOT EXISTS yellow_cards_away         integer,
    ADD COLUMN IF NOT EXISTS red_cards_home            integer,
    ADD COLUMN IF NOT EXISTS red_cards_away            integer;


-- ─── T5: Live odds flag on odds_snapshots ────────────────────────────────────

ALTER TABLE odds_snapshots
    ADD COLUMN IF NOT EXISTS is_live boolean DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_live
    ON odds_snapshots (match_id, is_live, timestamp DESC)
    WHERE is_live = true;


-- ─── T7: Lineups on matches ───────────────────────────────────────────────────

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS lineups_home           jsonb,
    ADD COLUMN IF NOT EXISTS lineups_away           jsonb,
    ADD COLUMN IF NOT EXISTS formation_home         text,
    ADD COLUMN IF NOT EXISTS formation_away         text,
    ADD COLUMN IF NOT EXISTS coach_home             text,
    ADD COLUMN IF NOT EXISTS coach_away             text,
    ADD COLUMN IF NOT EXISTS lineups_fetched_at     timestamptz;


-- ─── T8: AF event ID on match_events (enables AF as source, dedup) ───────────

ALTER TABLE match_events
    ADD COLUMN IF NOT EXISTS af_event_order integer;   -- sequential index within fixture

-- Index for dedup: one AF event per match at a given minute+type+team combo
-- (AF events don't have stable IDs like Sofascore, use order index)
CREATE UNIQUE INDEX IF NOT EXISTS idx_match_events_af_dedup
    ON match_events (match_id, af_event_order)
    WHERE af_event_order IS NOT NULL;


-- ─── T9: League Standings ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS league_standings (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_api_id       integer NOT NULL,
    season              integer NOT NULL,
    fetched_date        date    NOT NULL,

    -- Team info
    team_api_id         integer NOT NULL,
    team_name           text,

    -- Standing position
    rank                integer,
    points              integer,
    goals_diff          integer,
    group_name          text,
    form                text,           -- last 5 e.g. "WWDLW"
    status              text,           -- "same", "up", "down"
    description         text,           -- "Promotion - Champions League", "Relegation", etc.

    -- Overall
    played              integer,
    wins                integer,
    draws               integer,
    losses              integer,
    goals_for           integer,
    goals_against       integer,

    -- Home record
    home_played         integer,
    home_wins           integer,
    home_draws          integer,
    home_losses         integer,
    home_goals_for      integer,
    home_goals_against  integer,

    -- Away record
    away_played         integer,
    away_wins           integer,
    away_draws          integer,
    away_losses         integer,
    away_goals_for      integer,
    away_goals_against  integer,

    raw                 jsonb,
    created_at          timestamptz DEFAULT now(),

    UNIQUE (league_api_id, season, fetched_date, team_api_id)
);

CREATE INDEX IF NOT EXISTS idx_league_standings_team
    ON league_standings (team_api_id, season, fetched_date DESC);
CREATE INDEX IF NOT EXISTS idx_league_standings_league
    ON league_standings (league_api_id, season, fetched_date DESC);


-- ─── T10: H2H on matches ─────────────────────────────────────────────────────

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS h2h_raw        jsonb,      -- last 5 H2H as AF fixture objects
    ADD COLUMN IF NOT EXISTS h2h_home_wins  integer,    -- in last 5
    ADD COLUMN IF NOT EXISTS h2h_draws      integer,
    ADD COLUMN IF NOT EXISTS h2h_away_wins  integer;


-- ─── T11: Player Sidelined History ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS player_sidelined (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id   integer NOT NULL,
    player_name text,
    team_api_id integer,
    type        text,       -- e.g. "Knee Injury", "Hamstring Injury", "Suspension"
    start_date  date,
    end_date    date,
    raw         jsonb,
    created_at  timestamptz DEFAULT now(),

    UNIQUE (player_id, start_date, type)
);

CREATE INDEX IF NOT EXISTS idx_player_sidelined_player ON player_sidelined (player_id, start_date DESC);


-- ─── T12: Per-Player Match Statistics ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS match_player_stats (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id        uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    af_fixture_id   integer,
    team_api_id     integer,
    team_side       text,           -- 'home' or 'away'
    player_id       integer NOT NULL,
    player_name     text,
    shirt_number    integer,
    position        text,           -- 'G', 'D', 'M', 'F'
    minutes_played  integer,
    rating          numeric(4,2),
    captain         boolean DEFAULT false,

    -- Attacking
    goals           integer,
    assists         integer,
    shots_total     integer,
    shots_on_target integer,

    -- Passing
    passes_total    integer,
    passes_key      integer,
    pass_accuracy   numeric(5,2),

    -- Defensive
    tackles_total   integer,
    blocks          integer,
    interceptions   integer,
    duels_total     integer,
    duels_won       integer,

    -- Other
    dribbles_attempted integer,
    dribbles_success   integer,
    fouls_drawn     integer,
    fouls_committed integer,
    yellow_cards    integer,
    red_cards       integer,

    -- GK specific
    goals_conceded  integer,
    saves           integer,

    -- Penalties
    penalty_scored  integer,
    penalty_missed  integer,
    penalty_saved   integer,

    raw             jsonb,
    created_at      timestamptz DEFAULT now(),

    UNIQUE (match_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_match_player_stats_match  ON match_player_stats (match_id);
CREATE INDEX IF NOT EXISTS idx_match_player_stats_player ON match_player_stats (player_id);
CREATE INDEX IF NOT EXISTS idx_match_player_stats_team   ON match_player_stats (team_api_id, player_id);


-- ─── T13: Team Transfers ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS team_transfers (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_api_id         integer NOT NULL,   -- the team we fetched for
    player_id           integer NOT NULL,
    player_name         text,
    transfer_date       date,
    transfer_type       text,               -- "Free", "Loan", "N/A", or fee string like "€50M"
    from_team_api_id    integer,
    from_team_name      text,
    to_team_api_id      integer,
    to_team_name        text,
    raw                 jsonb,
    created_at          timestamptz DEFAULT now(),

    UNIQUE (team_api_id, player_id, transfer_date)
);

CREATE INDEX IF NOT EXISTS idx_team_transfers_team   ON team_transfers (team_api_id, transfer_date DESC);
CREATE INDEX IF NOT EXISTS idx_team_transfers_player ON team_transfers (player_id);


-- ─── RLS: public read on all new tables ──────────────────────────────────────

ALTER TABLE team_season_stats   ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_injuries      ENABLE ROW LEVEL SECURITY;
ALTER TABLE league_standings    ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_sidelined    ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_player_stats  ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_transfers      ENABLE ROW LEVEL SECURITY;

CREATE POLICY "public read team_season_stats"   ON team_season_stats   FOR SELECT USING (true);
CREATE POLICY "public read match_injuries"      ON match_injuries      FOR SELECT USING (true);
CREATE POLICY "public read league_standings"    ON league_standings    FOR SELECT USING (true);
CREATE POLICY "public read player_sidelined"    ON player_sidelined    FOR SELECT USING (true);
CREATE POLICY "public read match_player_stats"  ON match_player_stats  FOR SELECT USING (true);
CREATE POLICY "public read team_transfers"      ON team_transfers      FOR SELECT USING (true);
