-- MODEL-SIGNALS (2026-05-11)
-- Wire weather data into model:
-- 1. Add city/country/lat/lon to venues so we can geocode for Open-Meteo.
-- 2. Add weather feature columns to match_feature_vectors for model training.

-- Venues: add location fields for geocoding
ALTER TABLE venues
    ADD COLUMN IF NOT EXISTS city    text,
    ADD COLUMN IF NOT EXISTS country text,
    ADD COLUMN IF NOT EXISTS lat     numeric(8,5),
    ADD COLUMN IF NOT EXISTS lon     numeric(8,5);

-- match_feature_vectors: add weather columns
ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS weather_temp_c    numeric(5,2),
    ADD COLUMN IF NOT EXISTS weather_wind_kmh  numeric(5,2),
    ADD COLUMN IF NOT EXISTS weather_rain_mm   numeric(5,2),
    ADD COLUMN IF NOT EXISTS weather_humidity  numeric(5,2);
