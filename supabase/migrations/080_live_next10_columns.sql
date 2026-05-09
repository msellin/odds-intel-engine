-- Capture AF "Next 10 Minutes Total" market (id=65) — Over/Under 0.5 goals
-- in the next ~10-minute window. Already returned in /odds/live payload, so
-- this is a free data capture — zero new AF calls. Enables a future strategy:
-- 0-0 minute 70-80 in a high-λ match → bet Over 0.5 in the next 10 min.
ALTER TABLE live_match_snapshots
    ADD COLUMN IF NOT EXISTS live_next10_over  NUMERIC,
    ADD COLUMN IF NOT EXISTS live_next10_under NUMERIC;
