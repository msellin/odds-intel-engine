-- WEATHER-GEOCODE (2026-05-12)
-- Track how each venue's coordinates were obtained.
ALTER TABLE venues ADD COLUMN IF NOT EXISTS geocode_source text;
