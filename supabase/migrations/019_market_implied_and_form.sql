-- Migration 019: Market-implied feature columns + match form strings
--
-- MKT-STR: market_implied_home/draw/away are already stored as match_signals
--   by write_morning_signals() but were missing from match_feature_vectors.
--   Adding them here so _build_feature_row() can include them in XGBoost training.
--
-- ML-3: form_home / form_away store the last-5-match form string ("WWDLW")
--   populated by write_morning_signals() from league_standings.form.
--   Used by the frontend to render form dots in the match list.

-- ── match_feature_vectors: market-implied probability features ────────────────
ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS market_implied_home  numeric(6,4),
    ADD COLUMN IF NOT EXISTS market_implied_draw  numeric(6,4),
    ADD COLUMN IF NOT EXISTS market_implied_away  numeric(6,4);

-- ── matches: form string for each team (last 5 results, e.g. "WWDLW") ─────────
ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS form_home text,
    ADD COLUMN IF NOT EXISTS form_away text;
