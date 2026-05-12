-- WEATHER-GEOCODE (2026-05-12)
-- Add address field to venues so Nominatim fallback geocoding can use street address.
ALTER TABLE venues ADD COLUMN IF NOT EXISTS address text;
