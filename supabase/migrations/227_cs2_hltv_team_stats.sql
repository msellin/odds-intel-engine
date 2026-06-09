-- Direct team stats from HLTV /stats/teams and /stats/teams/pistols pages.
-- Replaces the roster×player_stats aggregation path with direct team-level
-- numbers for K/D, Rating 3.0, pistol win pct (with T/CT splits), and the
-- R2 conversion/break columns that the per-team pistol page exposes but the
-- current cs2_team_pistol_stats table does not.
--
-- Coverage: ~108 teams in 90d window, ~200 in 1yr (vs ~188 via roster
-- aggregation today). Replaces the 26-team /stats/teams/pistols/{id} path
-- with the bulk table view.
--
-- One row per (team, period). Both endpoints write to the same row keyed by
-- (hltv_team_id, period_start, period_end); pistol columns null until that
-- side's fetch lands.

CREATE TABLE IF NOT EXISTS cs2_hltv_team_stats (
    id              BIGSERIAL PRIMARY KEY,
    hltv_team_id    INTEGER     NOT NULL,
    team_name       TEXT        NOT NULL,
    slug            TEXT,
    period_start    DATE        NOT NULL,
    period_end      DATE        NOT NULL,

    -- /stats/teams page columns
    maps            INTEGER,
    kd_diff         INTEGER,
    kd              NUMERIC(5,3),
    rating_3        NUMERIC(5,3),

    -- /stats/teams/pistols page (no side filter)
    pistol_played   INTEGER,
    pistol_won      INTEGER,
    pistol_lost     INTEGER,
    pistol_pct      NUMERIC(5,2),
    r2_conv_pct     NUMERIC(5,2),
    r2_break_pct    NUMERIC(5,2),

    -- /stats/teams/pistols?side=...  (split by half)
    ct_pistol_pct   NUMERIC(5,2),
    t_pistol_pct    NUMERIC(5,2),

    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (hltv_team_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_cs2_hltv_team_stats_lookup
    ON cs2_hltv_team_stats (team_name, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_cs2_hltv_team_stats_period_only
    ON cs2_hltv_team_stats (period_end DESC, period_start DESC);
