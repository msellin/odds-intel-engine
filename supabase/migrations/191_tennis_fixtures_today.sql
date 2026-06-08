-- All today's tennis fixtures with Pinnacle-derived thresholds.
-- Populated by scripts/tennis/value_scanner.py — upserted every run.
CREATE TABLE IF NOT EXISTS tennis_fixtures_today (
    fixture_id      text        PRIMARY KEY,
    tournament_name text,
    player_home     text        NOT NULL,
    player_away     text        NOT NULL,
    surface         text,
    kickoff_time    timestamptz NOT NULL,
    pin_raw_home    numeric,                 -- raw Pinnacle odds for player_home (with vig)
    pin_raw_away    numeric,
    threshold_home  numeric     NOT NULL,    -- de-vigged fair odds = minimum to bet player_home
    threshold_away  numeric     NOT NULL,
    pin_margin_pct  numeric,                 -- Pinnacle margin e.g. 2.6
    scanned_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tennis_fixtures_today_kickoff ON tennis_fixtures_today(kickoff_time);
