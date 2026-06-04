-- WC-E1 (2026-06-04): Monte Carlo tournament simulation results.
--
-- One row per (team, snapshot_at) capturing how often that team reached
-- each tournament stage across N simulations. The script
-- `scripts/wc_monte_carlo.py` writes a fresh snapshot daily at 06:30 UTC
-- during the WC window (gated in workers.scheduler). The frontend page
-- `/world-cup/who-can-win` reads only the most recent snapshot.
--
-- Probabilities are stored as 0..1 floats. Per-team rows from one snapshot
-- share the same `snapshot_at` and `n_sims`, so the FE can show "10,000
-- simulations · 4h ago" once and the rest of the page reads team-wise.

CREATE TABLE IF NOT EXISTS wc_monte_carlo_results (
    team_id      uuid        NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    snapshot_at  timestamptz NOT NULL DEFAULT NOW(),
    n_sims       int         NOT NULL,
    p_advance    numeric(6,4) NOT NULL DEFAULT 0,  -- P(top 2 OR best-8 third)
    p_r16        numeric(6,4) NOT NULL DEFAULT 0,  -- P(won R32)
    p_qf         numeric(6,4) NOT NULL DEFAULT 0,  -- P(reached QF)
    p_sf         numeric(6,4) NOT NULL DEFAULT 0,  -- P(reached SF)
    p_final      numeric(6,4) NOT NULL DEFAULT 0,  -- P(reached final)
    p_winner     numeric(6,4) NOT NULL DEFAULT 0,  -- P(won the tournament)
    PRIMARY KEY (team_id, snapshot_at)
);

-- Read pattern: ORDER BY snapshot_at DESC LIMIT 1 then join to teams.
CREATE INDEX IF NOT EXISTS idx_wc_monte_carlo_snapshot
    ON wc_monte_carlo_results (snapshot_at DESC);

CREATE INDEX IF NOT EXISTS idx_wc_monte_carlo_winner
    ON wc_monte_carlo_results (snapshot_at DESC, p_winner DESC);

ALTER TABLE wc_monte_carlo_results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON wc_monte_carlo_results;
CREATE POLICY "Public read" ON wc_monte_carlo_results FOR SELECT USING (true);

COMMENT ON TABLE wc_monte_carlo_results IS
    'WC-E1 (2026-06-04): per-team tournament-stage probabilities from a daily '
    'Monte Carlo simulation of WC2026. Written by scripts/wc_monte_carlo.py '
    'at 06:30 UTC (gated to WC window). Each snapshot is a complete set of '
    'rows (one per team) sharing snapshot_at + n_sims. FE reads the most '
    'recent snapshot only.';
