-- Migration 065: Venues lookup table + venue_af_id on matches
-- Supports AF-VENUES: surface + capacity signals via /venues endpoint

ALTER TABLE matches ADD COLUMN IF NOT EXISTS venue_af_id INTEGER;

CREATE TABLE IF NOT EXISTS venues (
    af_id     INTEGER PRIMARY KEY,
    name      TEXT,
    surface   TEXT,       -- e.g. 'grass', 'artificial turf', 'indoor'
    capacity  INTEGER,
    fetched_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS venues_surface_idx ON venues (surface);
