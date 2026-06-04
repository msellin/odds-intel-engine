-- WC-A2 (2026-06-04): per-nation roster strength snapshot.
--
-- National-team ELO from `team_elo_international` reflects RESULTS history
-- (how often the country wins). It does NOT reflect the CURRENT squad's
-- club quality — a nation that historically lost games but now fields
-- Premier League / La Liga regulars has hidden upside the ELO can't see.
--
-- This table stores a roster-strength snapshot per nation, computed by
-- `scripts/compute_wc_roster_strength.py`:
--   • avg_starting_xi_club_elo : mean clubelo.com ELO of the top 11 players
--     by transfermarkt market value (proxy for "who actually plays")
--   • total_squad_value_eur    : sum of transfermarkt market values, EUR
--   • top_player_value_eur     : single most valuable player (star power)
--   • roster_quality_score     : composite normalised score for ranking
--   • n_players_resolved       : how many squad players successfully
--                                matched to a clubelo rating (data quality)
--
-- One row per (team_id, snapshot_date). Re-running the scraper on the same
-- day is idempotent via PK upsert. Engine-side only — RLS on, no public
-- read policy (consumed by the model, not the frontend yet).

CREATE TABLE IF NOT EXISTS team_roster_strength (
    team_id                    uuid    NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    snapshot_date              date    NOT NULL,
    avg_starting_xi_club_elo   numeric(8,2),
    total_squad_value_eur      bigint,
    top_player_value_eur       bigint,
    roster_quality_score       numeric(10,4),
    n_players_resolved         integer NOT NULL DEFAULT 0,
    created_at                 timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (team_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_team_roster_strength_date
    ON team_roster_strength (snapshot_date DESC);

ALTER TABLE team_roster_strength ENABLE ROW LEVEL SECURITY;
-- No public-read policy: engine-side only (model consumption). Service-role
-- key bypasses RLS for the scraper writes; anon/authenticated cannot read.
