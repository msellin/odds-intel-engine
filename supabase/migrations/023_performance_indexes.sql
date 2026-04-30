-- Performance indexes for slow frontend queries.
-- Matches page: 15s → target <3s
-- Track record: 5.5s → target <2s

-- predictions: used by getModelAccuracy() with WHERE match_id IN (...) AND market IN (...)
CREATE INDEX IF NOT EXISTS idx_predictions_match_market
  ON predictions (match_id, market);

-- matches: used by getModelAccuracy() with WHERE status = 'finished' AND result IN (...)
CREATE INDEX IF NOT EXISTS idx_matches_status_result
  ON matches (status, result) WHERE status = 'finished';

-- match_signals: used by batchFetchSignalSummary() with WHERE match_id IN (...) AND captured_at >= ...
CREATE INDEX IF NOT EXISTS idx_match_signals_match_captured
  ON match_signals (match_id, captured_at DESC);

-- odds_snapshots: composite for RPC queries filtering by match + market + timestamp
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_match_market_ts
  ON odds_snapshots (match_id, market, timestamp DESC);
