-- WC-A3 (2026-06-04): market consensus per WC2026 fixture.
--
-- Background: our ELO+Poisson national-team model produced "Brazil 22%
-- Morocco 50%" for the World Cup opener, while every public market source
-- (Bet365, Dimers, Opta) has Brazil at 55-69%. That's not a calibration
-- nudge — it's the model and the market disagreeing on the actual matchup.
--
-- This table stores a per-fixture market consensus 1X2 distribution scraped
-- from 2-3 FREE sources (eloratings.net, forebet, oddsportal, betfair
-- exchange where accessible). Probabilities are vig-removed per-source and
-- aggregated by simple mean. Sources are stored in JSONB so we can audit
-- which feeds agreed/diverged after the fact.
--
-- Consumed by:
--   - the upcoming blend layer (own-model × market consensus) — next wave
--   - the disagreement UI on /matches/[id] (show users when our model
--     diverges materially from the market — a high-conviction signal either
--     way is more interesting if 5 books agree)
--
-- Written by `scripts/scrape_wc_market_consensus.py` (daily 06:00 UTC via
-- workers.scheduler.job_wc_market_consensus, gated to the WC window).

CREATE TABLE IF NOT EXISTS wc_market_consensus (
    match_id      uuid PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    snapshot_at   timestamptz NOT NULL DEFAULT NOW(),
    home_prob     numeric NOT NULL,
    draw_prob     numeric NOT NULL,
    away_prob     numeric NOT NULL,
    n_sources     int NOT NULL,
    sources       jsonb NOT NULL,  -- {"eloratings": [0.55, 0.27, 0.18], "forebet": [...]}
    updated_at    timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wc_market_consensus_match
    ON wc_market_consensus(match_id);

-- Frontend reads this via the anon key — RLS public-read mirrors the
-- pattern used for team_elo_international + wc_bracket_predictions.
ALTER TABLE wc_market_consensus ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read" ON wc_market_consensus FOR SELECT USING (true);

COMMENT ON TABLE wc_market_consensus IS
    'WC-A3 (2026-06-04): aggregated 1X2 market consensus per WC2026 fixture, '
    'scraped from 2-3 free public sources and vig-removed. Used to blend '
    'with own ELO+Poisson model + show market-vs-model disagreement to users.';

COMMENT ON COLUMN wc_market_consensus.sources IS
    'JSONB map: {source_name: [home_prob, draw_prob, away_prob]} for every '
    'source that contributed to this snapshot. Lets us audit which sources '
    'agreed/diverged and drop a source if it shows systematic bias.';
