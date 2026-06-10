-- Top-N CS2 players over a rolling window from HLTV /stats/players.
-- Lets us flag "star player present" in a roster — top-30 by HLTV Rating in
-- the last year is the conventional skill bar. Feeds v10's star_player_present
-- feature + IGL × star role interaction (TASK #59).
--
-- One row per (hltv_player_id, period_start, period_end). Refreshed weekly
-- from /stats/players?startDate=X&endDate=Y&minMapCount=N — one page = top 50.

CREATE TABLE IF NOT EXISTS cs2_hltv_top_players (
    id              BIGSERIAL PRIMARY KEY,
    hltv_player_id  INTEGER NOT NULL,
    nickname        TEXT,
    team_name       TEXT,
    rank            INTEGER,
    maps_played     INTEGER,
    kd_ratio        NUMERIC(5,3),
    rating          NUMERIC(5,3),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (hltv_player_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_cs2_hltv_top_players_lookup
    ON cs2_hltv_top_players (period_end DESC, rank);

CREATE INDEX IF NOT EXISTS idx_cs2_hltv_top_players_nickname
    ON cs2_hltv_top_players (LOWER(nickname));
