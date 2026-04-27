-- =============================================================================
-- OddsIntel — Migration 002: Live Tracking & Multi-Snapshot Odds
-- =============================================================================
-- Adds:
--   1. sofascore_event_id on matches (needed to look up live data)
--   2. minutes_to_kickoff on odds_snapshots (enables timing analysis)
--   3. live_match_snapshots table (in-play state every 5 min)
--   4. match_events table (goals, cards, subs with exact minute)
-- =============================================================================

-- 1. Add Sofascore event ID to matches so live tracker can look up by ID
alter table matches
    add column if not exists sofascore_event_id bigint unique;

create index if not exists idx_matches_sofascore_id
    on matches (sofascore_event_id)
    where sofascore_event_id is not null;

-- 2. Add minutes_to_kickoff to odds_snapshots (enables CLV timing analysis)
--    Negative = before kickoff (e.g. -120 = 2h before), positive = in-play
alter table odds_snapshots
    add column if not exists minutes_to_kickoff integer;

create index if not exists idx_odds_snapshots_timing
    on odds_snapshots (match_id, minutes_to_kickoff);

-- Fix the column name bug (pipeline was inserting created_at, schema has timestamp)
-- The timestamp column already exists with default now(), so we just ensure
-- it gets populated correctly going forward. No rename needed.

-- 3. Live match snapshots: in-play state captured every ~5 minutes
create table if not exists live_match_snapshots (
    id                      uuid primary key default gen_random_uuid(),
    match_id                uuid not null references matches (id) on delete cascade,
    minute                  smallint not null,
    added_time              smallint not null default 0,     -- injury time minutes
    score_home              smallint not null default 0,
    score_away              smallint not null default 0,

    -- Match stats from Sofascore at this moment
    shots_home              smallint,
    shots_away              smallint,
    shots_on_target_home    smallint,
    shots_on_target_away    smallint,
    xg_home                 numeric(4, 2),
    xg_away                 numeric(4, 2),
    possession_home         numeric(5, 2),     -- 0-100
    corners_home            smallint,
    corners_away            smallint,
    attacks_home            smallint,
    attacks_away            smallint,

    -- Live Kambi odds for O/U lines at this moment
    -- null = market not available / not yet traded
    live_ou_05_over         numeric(6, 3),
    live_ou_05_under        numeric(6, 3),
    live_ou_15_over         numeric(6, 3),
    live_ou_15_under        numeric(6, 3),
    live_ou_25_over         numeric(6, 3),
    live_ou_25_under        numeric(6, 3),
    live_ou_35_over         numeric(6, 3),
    live_ou_35_under        numeric(6, 3),
    live_ou_45_over         numeric(6, 3),
    live_ou_45_under        numeric(6, 3),

    -- Live 1X2 odds
    live_1x2_home           numeric(6, 3),
    live_1x2_draw           numeric(6, 3),
    live_1x2_away           numeric(6, 3),

    -- Model context: what the pre-match model expected
    -- Copied from predictions table at match start for easy analysis
    model_xg_home           numeric(4, 2),
    model_xg_away           numeric(4, 2),
    model_ou25_prob         numeric(5, 4),

    captured_at             timestamptz not null default now(),

    constraint chk_live_snapshots_minute     check (minute >= 0 and minute <= 130),
    constraint chk_live_snapshots_scores     check (score_home >= 0 and score_away >= 0),
    constraint chk_live_snapshots_possession check (
        possession_home is null or (possession_home >= 0 and possession_home <= 100)
    )
);

create index if not exists idx_live_snapshots_match_id
    on live_match_snapshots (match_id);

create index if not exists idx_live_snapshots_captured_at
    on live_match_snapshots (captured_at);

-- Composite: fetch all snapshots for a match in order
create index if not exists idx_live_snapshots_match_minute
    on live_match_snapshots (match_id, minute, captured_at);

-- Analysis index: find all snapshots at a specific minute across matches
create index if not exists idx_live_snapshots_minute_score
    on live_match_snapshots (minute, score_home, score_away)
    where minute between 5 and 30;   -- the interesting live-bet window

-- 4. Match events: goals, cards, substitutions with exact minute
create table if not exists match_events (
    id                  uuid primary key default gen_random_uuid(),
    match_id            uuid not null references matches (id) on delete cascade,
    minute              smallint not null,
    added_time          smallint not null default 0,
    event_type          text not null,
    -- 'goal', 'own_goal', 'yellow_card', 'red_card', 'yellow_red_card',
    -- 'substitution_in', 'substitution_out', 'penalty_scored', 'penalty_missed',
    -- 'var_decision'
    team                text not null,      -- 'home' or 'away'
    player_name         text,
    assist_name         text,               -- for goals
    detail              text,               -- extra context
    sofascore_event_id  bigint unique,      -- dedup key

    created_at          timestamptz not null default now(),

    constraint chk_match_events_minute check (minute >= 0 and minute <= 130),
    constraint chk_match_events_team   check (team in ('home', 'away'))
);

create index if not exists idx_match_events_match_id
    on match_events (match_id);

create index if not exists idx_match_events_type
    on match_events (event_type);

create index if not exists idx_match_events_match_type
    on match_events (match_id, event_type);

-- Useful for: "show me all goals before minute 15 in high-xG games"
create index if not exists idx_match_events_goals_by_minute
    on match_events (minute, event_type)
    where event_type in ('goal', 'own_goal', 'penalty_scored');
