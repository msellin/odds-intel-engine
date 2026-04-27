-- =============================================================================
-- OddsIntel — Initial Database Schema
-- Migration: 001_initial_schema.sql
-- =============================================================================

-- Enable required extensions
create extension if not exists "pgcrypto";

-- =============================================================================
-- ENUMS & TYPES
-- =============================================================================

create type match_status as enum ('scheduled', 'live', 'finished', 'postponed', 'cancelled');
create type match_result as enum ('home', 'draw', 'away');
create type bet_result as enum ('won', 'lost', 'void', 'pending');
create type player_position as enum ('GK', 'DEF', 'MID', 'FWD');
create type impact_type as enum ('injury', 'suspension', 'lineup', 'transfer', 'tactical', 'motivation', 'weather', 'other');
create type user_tier as enum ('scout', 'analyst', 'sharp', 'syndicate');
create type odds_format as enum ('decimal', 'american', 'fractional');

-- =============================================================================
-- CORE REFERENCE TABLES
-- =============================================================================

-- Leagues
create table leagues (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    country     text not null,
    tier        smallint not null default 1,
    is_active   boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index idx_leagues_country on leagues (country);
create index idx_leagues_is_active on leagues (is_active) where is_active = true;

-- Teams
create table teams (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    league_id   uuid not null references leagues (id) on delete restrict,
    stadium_lat numeric(9,6),
    stadium_lng numeric(9,6),
    country     text not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index idx_teams_league_id on teams (league_id);
create index idx_teams_country on teams (country);

-- Seasons
create table seasons (
    id          uuid primary key default gen_random_uuid(),
    league_id   uuid not null references leagues (id) on delete cascade,
    year        smallint not null,
    start_date  date not null,
    end_date    date not null,
    created_at  timestamptz not null default now(),

    constraint uq_seasons_league_year unique (league_id, year),
    constraint chk_seasons_dates check (end_date > start_date)
);

create index idx_seasons_league_id on seasons (league_id);

-- Referees
create table referees (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    created_at  timestamptz not null default now()
);

-- Managers
create table managers (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    created_at  timestamptz not null default now()
);

-- Manager tenures
create table manager_tenures (
    id          uuid primary key default gen_random_uuid(),
    manager_id  uuid not null references managers (id) on delete cascade,
    team_id     uuid not null references teams (id) on delete cascade,
    date_from   date not null,
    date_to     date,

    constraint chk_manager_tenures_dates check (date_to is null or date_to >= date_from)
);

create index idx_manager_tenures_manager_id on manager_tenures (manager_id);
create index idx_manager_tenures_team_id on manager_tenures (team_id);
create index idx_manager_tenures_active on manager_tenures (team_id) where date_to is null;

-- Players
create table players (
    id            uuid primary key default gen_random_uuid(),
    name          text not null,
    team_id       uuid references teams (id) on delete set null,
    position      player_position,
    market_value  numeric(12,2),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index idx_players_team_id on players (team_id);

-- =============================================================================
-- MATCH DATA
-- =============================================================================

-- Matches
create table matches (
    id            uuid primary key default gen_random_uuid(),
    date          timestamptz not null,
    home_team_id  uuid not null references teams (id) on delete restrict,
    away_team_id  uuid not null references teams (id) on delete restrict,
    league_id     uuid not null references leagues (id) on delete restrict,
    season        smallint not null,
    score_home    smallint,
    score_away    smallint,
    result        match_result,
    status        match_status not null default 'scheduled',
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),

    constraint chk_matches_different_teams check (home_team_id <> away_team_id),
    constraint chk_matches_scores_non_negative check (
        (score_home is null or score_home >= 0) and
        (score_away is null or score_away >= 0)
    )
);

create index idx_matches_date on matches (date);
create index idx_matches_league_id on matches (league_id);
create index idx_matches_home_team_id on matches (home_team_id);
create index idx_matches_away_team_id on matches (away_team_id);
create index idx_matches_status on matches (status);
create index idx_matches_season on matches (season);
create index idx_matches_league_date on matches (league_id, date);
create index idx_matches_league_season on matches (league_id, season);

-- Match stats (one row per match)
create table match_stats (
    match_id        uuid primary key references matches (id) on delete cascade,
    xg_home         numeric(5,2),
    xg_away         numeric(5,2),
    shots_home      smallint,
    shots_away      smallint,
    possession_home numeric(5,2),
    corners_home    smallint,
    corners_away    smallint,
    yellows_home    smallint,
    yellows_away    smallint,
    reds_home       smallint,
    reds_away       smallint,
    created_at      timestamptz not null default now(),

    constraint chk_match_stats_possession check (
        possession_home is null or (possession_home >= 0 and possession_home <= 100)
    ),
    constraint chk_match_stats_non_negative check (
        (shots_home is null or shots_home >= 0) and
        (shots_away is null or shots_away >= 0) and
        (corners_home is null or corners_home >= 0) and
        (corners_away is null or corners_away >= 0) and
        (yellows_home is null or yellows_home >= 0) and
        (yellows_away is null or yellows_away >= 0) and
        (reds_home is null or reds_home >= 0) and
        (reds_away is null or reds_away >= 0)
    )
);

-- Match weather (one row per match)
create table match_weather (
    match_id        uuid primary key references matches (id) on delete cascade,
    temp_c          numeric(5,2),
    wind_kmh        numeric(5,2),
    wind_direction  text,
    rain_mm         numeric(5,2),
    humidity        numeric(5,2),
    created_at      timestamptz not null default now(),

    constraint chk_match_weather_humidity check (
        humidity is null or (humidity >= 0 and humidity <= 100)
    )
);

-- Referee match assignments
create table referee_matches (
    id              uuid primary key default gen_random_uuid(),
    referee_id      uuid not null references referees (id) on delete cascade,
    match_id        uuid not null references matches (id) on delete cascade,
    yellows         smallint not null default 0,
    reds            smallint not null default 0,
    penalties_given smallint not null default 0,
    fouls           smallint not null default 0,

    constraint uq_referee_matches unique (referee_id, match_id),
    constraint chk_referee_matches_non_negative check (
        yellows >= 0 and reds >= 0 and penalties_given >= 0 and fouls >= 0
    )
);

create index idx_referee_matches_referee_id on referee_matches (referee_id);
create index idx_referee_matches_match_id on referee_matches (match_id);

-- Lineups
create table lineups (
    id                uuid primary key default gen_random_uuid(),
    match_id          uuid not null references matches (id) on delete cascade,
    team_id           uuid not null references teams (id) on delete cascade,
    player_id         uuid not null references players (id) on delete cascade,
    position          player_position,
    is_starter        boolean not null default true,
    minute_subbed_in  smallint,
    minute_subbed_out smallint,

    constraint uq_lineups_match_team_player unique (match_id, team_id, player_id),
    constraint chk_lineups_minutes check (
        (minute_subbed_in is null or minute_subbed_in >= 0) and
        (minute_subbed_out is null or minute_subbed_out >= 0)
    )
);

create index idx_lineups_match_id on lineups (match_id);
create index idx_lineups_team_id on lineups (team_id);
create index idx_lineups_player_id on lineups (player_id);
create index idx_lineups_match_team on lineups (match_id, team_id);

-- Injuries
create table injuries (
    id              uuid primary key default gen_random_uuid(),
    player_id       uuid not null references players (id) on delete cascade,
    injury_type     text not null,
    date_from       date not null,
    date_to         date,
    matches_missed  smallint not null default 0,
    created_at      timestamptz not null default now(),

    constraint chk_injuries_dates check (date_to is null or date_to >= date_from),
    constraint chk_injuries_matches_missed check (matches_missed >= 0)
);

create index idx_injuries_player_id on injuries (player_id);
create index idx_injuries_active on injuries (player_id) where date_to is null;

-- =============================================================================
-- ODDS & MARKET DATA
-- =============================================================================

-- Odds snapshots (normalized: one row per bookmaker/market/selection snapshot)
create table odds_snapshots (
    id          uuid primary key default gen_random_uuid(),
    match_id    uuid not null references matches (id) on delete cascade,
    bookmaker   text not null,
    market      text not null,
    selection   text not null,
    odds        numeric(10,4) not null,
    timestamp   timestamptz not null default now(),
    is_closing  boolean not null default false,

    constraint chk_odds_snapshots_odds_positive check (odds > 0)
);

create index idx_odds_snapshots_match_id on odds_snapshots (match_id);
create index idx_odds_snapshots_match_market on odds_snapshots (match_id, market);
create index idx_odds_snapshots_timestamp on odds_snapshots (timestamp);
create index idx_odds_snapshots_closing on odds_snapshots (match_id, market, is_closing) where is_closing = true;

-- =============================================================================
-- PREDICTIONS & AI
-- =============================================================================

-- Predictions
create table predictions (
    id                  uuid primary key default gen_random_uuid(),
    match_id            uuid not null references matches (id) on delete cascade,
    market              text not null,
    model_probability   numeric(5,4) not null,
    implied_probability numeric(5,4) not null,
    edge_percent        numeric(5,2) not null,
    confidence          numeric(5,4) not null,
    reasoning           text,
    created_at          timestamptz not null default now(),

    constraint chk_predictions_probability check (
        model_probability >= 0 and model_probability <= 1 and
        implied_probability >= 0 and implied_probability <= 1
    ),
    constraint chk_predictions_confidence check (confidence >= 0 and confidence <= 1)
);

create index idx_predictions_match_id on predictions (match_id);
create index idx_predictions_created_at on predictions (created_at);
create index idx_predictions_edge on predictions (edge_percent) where edge_percent > 3;

-- News events
create table news_events (
    id                uuid primary key default gen_random_uuid(),
    match_id          uuid references matches (id) on delete set null,
    source            text not null,
    source_url        text,
    raw_text          text not null,
    extracted_entity  text,
    impact_type       impact_type,
    impact_magnitude  numeric(5,2),
    detected_at       timestamptz not null default now(),
    processed_at      timestamptz,

    constraint chk_news_events_magnitude check (
        impact_magnitude is null or (impact_magnitude >= 0 and impact_magnitude <= 100)
    )
);

create index idx_news_events_match_id on news_events (match_id);
create index idx_news_events_detected_at on news_events (detected_at);
create index idx_news_events_unprocessed on news_events (detected_at) where processed_at is null;

-- =============================================================================
-- PAPER TRADING / BOTS
-- =============================================================================

-- Bots
create table bots (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null unique,
    strategy            text not null,
    description         text,
    starting_bankroll   numeric(12,2) not null default 10000.00,
    current_bankroll    numeric(12,2) not null default 10000.00,
    is_active           boolean not null default true,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint chk_bots_bankroll_positive check (starting_bankroll > 0)
);

-- Simulated bets
create table simulated_bets (
    id                uuid primary key default gen_random_uuid(),
    bot_id            uuid not null references bots (id) on delete cascade,
    match_id          uuid not null references matches (id) on delete cascade,
    market            text not null,
    selection         text not null,
    odds_at_pick      numeric(10,4) not null,
    pick_time         timestamptz not null default now(),
    stake             numeric(12,2) not null,
    model_probability numeric(5,4) not null,
    edge_percent      numeric(5,2) not null,
    closing_odds      numeric(10,4),
    clv               numeric(5,4),
    result            bet_result not null default 'pending',
    pnl               numeric(12,2),
    bankroll_after    numeric(12,2),
    news_triggered    boolean not null default false,
    reasoning         text,
    created_at        timestamptz not null default now(),

    constraint chk_simulated_bets_odds_positive check (odds_at_pick > 0),
    constraint chk_simulated_bets_stake_positive check (stake > 0),
    constraint chk_simulated_bets_probability check (model_probability >= 0 and model_probability <= 1)
);

create index idx_simulated_bets_bot_id on simulated_bets (bot_id);
create index idx_simulated_bets_match_id on simulated_bets (match_id);
create index idx_simulated_bets_result on simulated_bets (result);
create index idx_simulated_bets_pick_time on simulated_bets (pick_time);
create index idx_simulated_bets_bot_result on simulated_bets (bot_id, result);

-- Model evaluations
create table model_evaluations (
    id                uuid primary key default gen_random_uuid(),
    date              date not null,
    league_id         uuid references leagues (id) on delete set null,
    market            text not null,
    total_bets        integer not null default 0,
    hits              integer not null default 0,
    hit_rate          numeric(5,4),
    roi               numeric(5,2),
    avg_clv           numeric(5,4),
    calibration_score numeric(5,4),
    notes             text,
    created_at        timestamptz not null default now(),

    constraint chk_model_evaluations_hits check (hits >= 0 and hits <= total_bets),
    constraint chk_model_evaluations_hit_rate check (
        hit_rate is null or (hit_rate >= 0 and hit_rate <= 1)
    )
);

create index idx_model_evaluations_date on model_evaluations (date);
create index idx_model_evaluations_league_id on model_evaluations (league_id);

-- =============================================================================
-- USER / AUTH TABLES
-- =============================================================================

-- Profiles (linked to Supabase auth.users)
create table profiles (
    id                  uuid primary key references auth.users (id) on delete cascade,
    email               text not null,
    display_name        text,
    tier                user_tier not null default 'scout',
    preferred_leagues   text[] default '{}',
    preferred_markets   text[] default '{}',
    default_stake       numeric(12,2) default 10.00,
    bankroll            numeric(12,2) default 1000.00,
    odds_format         odds_format not null default 'decimal',
    timezone            text not null default 'UTC',
    stripe_customer_id  text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint chk_profiles_default_stake check (default_stake is null or default_stake > 0),
    constraint chk_profiles_bankroll check (bankroll is null or bankroll >= 0)
);

-- User bets
create table user_bets (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references profiles (id) on delete cascade,
    match_id    uuid not null references matches (id) on delete cascade,
    market      text not null,
    selection   text not null,
    odds        numeric(10,4) not null,
    stake       numeric(12,2) not null,
    result      bet_result not null default 'pending',
    pnl         numeric(12,2),
    created_at  timestamptz not null default now(),

    constraint chk_user_bets_odds_positive check (odds > 0),
    constraint chk_user_bets_stake_positive check (stake > 0)
);

create index idx_user_bets_user_id on user_bets (user_id);
create index idx_user_bets_match_id on user_bets (match_id);
create index idx_user_bets_created_at on user_bets (created_at);
create index idx_user_bets_user_result on user_bets (user_id, result);

-- User notification settings
create table user_notification_settings (
    user_id           uuid primary key references profiles (id) on delete cascade,
    value_bet_alerts  boolean not null default true,
    lineup_alerts     boolean not null default true,
    injury_alerts     boolean not null default true,
    weekly_report     boolean not null default true,
    edge_threshold    numeric(5,2) not null default 3.00,

    constraint chk_notification_edge_threshold check (edge_threshold >= 0 and edge_threshold <= 100)
);

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================

-- Enable RLS on user-facing tables
alter table profiles enable row level security;
alter table user_bets enable row level security;
alter table user_notification_settings enable row level security;

-- Profiles: users can read and update their own profile
create policy "Users can view own profile"
    on profiles for select
    using (auth.uid() = id);

create policy "Users can update own profile"
    on profiles for update
    using (auth.uid() = id)
    with check (auth.uid() = id);

-- User bets: users can read, insert, and update their own bets
create policy "Users can view own bets"
    on user_bets for select
    using (auth.uid() = user_id);

create policy "Users can insert own bets"
    on user_bets for insert
    with check (auth.uid() = user_id);

create policy "Users can update own bets"
    on user_bets for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- Notification settings: users can read, insert, and update their own settings
create policy "Users can view own notification settings"
    on user_notification_settings for select
    using (auth.uid() = user_id);

create policy "Users can insert own notification settings"
    on user_notification_settings for insert
    with check (auth.uid() = user_id);

create policy "Users can update own notification settings"
    on user_notification_settings for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- =============================================================================
-- TRIGGER: Auto-create profile on new auth.users signup
-- =============================================================================

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, tier)
    values (
        new.id,
        coalesce(new.email, ''),
        'scout'
    );
    return new;
end;
$$;

create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user();

-- =============================================================================
-- TRIGGER: Auto-update updated_at columns
-- =============================================================================

create or replace function public.update_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger trg_leagues_updated_at
    before update on leagues
    for each row execute function public.update_updated_at();

create trigger trg_teams_updated_at
    before update on teams
    for each row execute function public.update_updated_at();

create trigger trg_players_updated_at
    before update on players
    for each row execute function public.update_updated_at();

create trigger trg_matches_updated_at
    before update on matches
    for each row execute function public.update_updated_at();

create trigger trg_bots_updated_at
    before update on bots
    for each row execute function public.update_updated_at();

create trigger trg_profiles_updated_at
    before update on profiles
    for each row execute function public.update_updated_at();
