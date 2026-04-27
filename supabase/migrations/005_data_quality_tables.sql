-- =============================================================================
-- OddsIntel — Migration 005: Data Quality & ML Feature Tables
-- =============================================================================
-- Adds:
--   1. team_elo_daily — persistent ELO ratings per team per date
--   2. team_form_cache — cached rolling form metrics per team per date
-- Also:
--   - match_stats already exists (001) but was never populated
--   - model_evaluations already exists (001) but was never populated
-- =============================================================================

-- 1. Team ELO daily ratings
-- Stores ELO rating per team per date, enabling trajectory analysis
-- and fast lookup during live prediction (no recomputation needed).
create table if not exists team_elo_daily (
    id          uuid primary key default gen_random_uuid(),
    team_id     uuid not null references teams (id) on delete cascade,
    date        date not null,
    elo_rating  numeric(8,2) not null default 1500.00,
    created_at  timestamptz not null default now(),

    constraint uq_team_elo_daily unique (team_id, date)
);

create index if not exists idx_team_elo_daily_team_date
    on team_elo_daily (team_id, date desc);

create index if not exists idx_team_elo_daily_date
    on team_elo_daily (date);

-- 2. Team form cache
-- Stores rolling 10-match form metrics per team per date.
-- Replaces on-demand computation in features.py.
create table if not exists team_form_cache (
    id              uuid primary key default gen_random_uuid(),
    team_id         uuid not null references teams (id) on delete cascade,
    date            date not null,
    matches_played  smallint not null default 0,
    win_pct         numeric(5,4),
    draw_pct        numeric(5,4),
    loss_pct        numeric(5,4),
    ppg             numeric(5,3),
    goals_scored_avg    numeric(5,3),
    goals_conceded_avg  numeric(5,3),
    goal_diff_avg       numeric(6,3),
    clean_sheet_pct     numeric(5,4),
    over25_pct          numeric(5,4),
    btts_pct            numeric(5,4),
    created_at      timestamptz not null default now(),

    constraint uq_team_form_cache unique (team_id, date)
);

create index if not exists idx_team_form_cache_team_date
    on team_form_cache (team_id, date desc);

create index if not exists idx_team_form_cache_date
    on team_form_cache (date);

-- Enable public read on new tables (consistent with existing RLS setup)
alter table team_elo_daily enable row level security;
create policy "Public read" on team_elo_daily for select using (true);

alter table team_form_cache enable row level security;
create policy "Public read" on team_form_cache for select using (true);
