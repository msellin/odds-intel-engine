-- =============================================================================
-- OddsIntel — Migration 004: Prediction Audit Trail
-- =============================================================================
-- Tracks model probability at each information stage for every bet.
-- Enables measuring value-add of each data source (stats, AI news, lineups).
--
-- Stages:
--   stats_only   — raw Poisson model output (morning pipeline, before AI)
--   post_ai      — after AI news checker adjusts probability
--   pre_kickoff  — after confirmed lineups / late odds movement
--   closing      — at kickoff, final state for CLV comparison
-- =============================================================================

create table if not exists prediction_snapshots (
    id                  uuid primary key default gen_random_uuid(),
    bet_id              uuid not null references simulated_bets (id) on delete cascade,
    stage               text not null,
    model_probability   numeric(6, 4) not null,
    implied_probability numeric(6, 4),
    edge_percent        numeric(6, 4),
    odds_at_snapshot    numeric(10, 4),
    metadata            jsonb,               -- extra context per stage (e.g. AI flag, missing players)
    captured_at         timestamptz not null default now(),

    constraint chk_snapshot_stage check (
        stage in ('stats_only', 'post_ai', 'pre_kickoff', 'closing')
    ),
    -- One snapshot per bet per stage
    constraint uq_snapshot_per_bet_stage unique (bet_id, stage)
);

create index idx_prediction_snapshots_bet_id on prediction_snapshots (bet_id);
create index idx_prediction_snapshots_stage on prediction_snapshots (stage);
create index idx_prediction_snapshots_captured on prediction_snapshots (captured_at);

-- Enable RLS and allow public reads (consistent with other tables)
alter table prediction_snapshots enable row level security;

create policy "Public read access on prediction_snapshots"
    on prediction_snapshots for select
    using (true);
