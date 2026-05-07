-- MGR-CHANGE: Cache coach history per team for manager change signal.
-- AF /coachs?team={id} returns career entries with start/end dates.
-- end_date NULL = current coach. Signal window: 90 days post-appointment.

CREATE TABLE IF NOT EXISTS team_coaches (
  id           SERIAL PRIMARY KEY,
  team_af_id   INTEGER NOT NULL,
  coach_name   TEXT    NOT NULL,
  start_date   DATE    NOT NULL,
  end_date     DATE,             -- NULL = current coach
  fetched_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE (team_af_id, start_date)
);

CREATE INDEX IF NOT EXISTS team_coaches_team_af_id_idx ON team_coaches (team_af_id);
CREATE INDEX IF NOT EXISTS team_coaches_end_date_idx   ON team_coaches (team_af_id, end_date NULLS LAST);
