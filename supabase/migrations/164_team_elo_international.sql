-- WC-PHASE-3 (2026-06-02): national-team ELO storage.
--
-- The existing `team_elo_daily` is updated by settlement.py with club-match
-- K=30 / home_adv=+100 and is keyed on a single (team_id, date). National-
-- team ELO needs different math (different K per competition tier, neutral
-- venues in tournaments, much sparser update cadence) so we keep it in a
-- separate table to avoid polluting club ELO trajectories.
--
-- Populated by `scripts/compute_international_elo.py` (one-shot, walks the
-- 6,651 finished international matches in chronological order). Consumed
-- by `workers/model/national_team_predictor.py` at prediction time.
--
-- A team can appear in both tables: club teams use team_elo_daily,
-- national teams use team_elo_international. Conceptually disjoint
-- (national team rows have country='World' in `teams`).

CREATE TABLE IF NOT EXISTS team_elo_international (
    id           uuid primary key default gen_random_uuid(),
    team_id      uuid not null references teams(id) on delete cascade,
    match_date   date not null,
    elo_rating   numeric(8,2) not null default 1500.00,
    n_matches    integer not null default 0,
    last_comp    text,           -- friendly | qualifier_nl | tournament
    created_at   timestamptz not null default now(),

    CONSTRAINT uq_team_elo_intl UNIQUE (team_id, match_date)
);

CREATE INDEX IF NOT EXISTS idx_team_elo_intl_team_date
    ON team_elo_international (team_id, match_date DESC);

ALTER TABLE team_elo_international ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON team_elo_international FOR SELECT USING (true);
