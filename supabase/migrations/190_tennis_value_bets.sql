-- Tennis value bets — simulated paper bets from Pinnacle sharp-vs-soft scanner
CREATE TABLE IF NOT EXISTS tennis_value_bets (
    id                 uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    fixture_id         text        NOT NULL,
    tournament_name    text,
    player_home        text        NOT NULL,
    player_away        text        NOT NULL,
    surface            text,
    kickoff_time       timestamptz NOT NULL,
    market             text        NOT NULL DEFAULT 'match_winner',
    selection          text        NOT NULL,   -- 'home' or 'away'
    pin_fair_odds      numeric     NOT NULL,   -- Pinnacle de-vigged fair price
    pin_raw_home       numeric,               -- raw Pinnacle home odds (with vig)
    pin_raw_away       numeric,               -- raw Pinnacle away odds (with vig)
    bookmaker          text        NOT NULL,
    book_odds          numeric     NOT NULL,
    edge_pct           numeric     NOT NULL,   -- (book_odds * pin_prob) - 1
    kelly_fraction     numeric,
    stake              numeric     DEFAULT 1.0,
    logged_at          timestamptz DEFAULT now(),
    result             text,                  -- 'win' | 'loss' | 'void' — filled on settlement
    pnl                numeric,
    closing_odds       numeric,               -- Pinnacle closing odds for selected side
    clv                numeric,               -- closing line value
    notes              text
);

CREATE INDEX IF NOT EXISTS tennis_value_bets_kickoff   ON tennis_value_bets(kickoff_time);
CREATE INDEX IF NOT EXISTS tennis_value_bets_fixture    ON tennis_value_bets(fixture_id);
CREATE INDEX IF NOT EXISTS tennis_value_bets_logged_at  ON tennis_value_bets(logged_at);
