-- CS2-HLTV-TEAM-FTU — per-team "Firepower / Teamwork / Utility" stats from HLTV.
--
-- Source: HLTV /stats/teams/ftu — the only "FTU" page that exists on HLTV
-- (per-team /stats/teams/ftu/{teamId} returns 404; /stats/teams/utility and
-- /stats/teams/grenades both 404 as well). The bulk page exposes ten
-- columns per team grouped under the visual Firepower/Teamwork/Utility
-- banner; we store the raw columns and let the model compose features.
--
-- NOTE TO READER (the task spec assumed first-3-utility grenade counts —
-- flashes/HE/molly/smoke thrown per round — but HLTV does NOT publish
-- per-team utility throw counts. The /stats/teams/ftu page is the closest
-- match: it bundles utility-related signals (flash assists, ADR) alongside
-- firepower and teamwork composites. We store the columns the page
-- actually provides; the v19 feature build derives utility-like diffs
-- from FA + ADR + Traded%.
--
-- Columns (matched to the live page 2026-06-10):
--   rw_pct       — round win % (Firepower bucket)
--   opk_pct      — opening kill %
--   multik_pct   — multi-kill %
--   five_v_four  — 5v4 conversion %
--   four_v_five  — 4v5 retake %
--   traded_pct   — % of deaths traded (Teamwork composite)
--   adr          — average damage per round (Utility-adjacent)
--   fa           — flash assists per round (Utility composite)
--
-- Side filter is honored by HLTV (overall / COUNTER_TERRORIST / TERRORIST)
-- so we add a `side` column to the PK (with 'all' for the overall slice).
--
-- Re-runnable: ON CONFLICT DO UPDATE keyed on
-- (hltv_team_id, side, period_start, period_end).

CREATE TABLE IF NOT EXISTS cs2_hltv_team_ftu (
    hltv_team_id    BIGINT      NOT NULL,
    team_name       TEXT        NOT NULL,
    side            TEXT        NOT NULL,   -- 'all' | 'ct' | 't'
    period_start    DATE        NOT NULL,
    period_end      DATE        NOT NULL,

    maps_played     INTEGER,

    rw_pct          NUMERIC(6,3),
    opk_pct         NUMERIC(6,3),
    multik_pct      NUMERIC(6,3),
    five_v_four     NUMERIC(6,3),
    four_v_five     NUMERIC(6,3),
    traded_pct      NUMERIC(6,3),
    adr             NUMERIC(8,3),
    fa              NUMERIC(6,3),

    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (hltv_team_id, side, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS cs2_hltv_team_ftu_team_idx
    ON cs2_hltv_team_ftu (hltv_team_id);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_ftu_team_name_idx
    ON cs2_hltv_team_ftu (team_name, side);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_ftu_period_idx
    ON cs2_hltv_team_ftu (period_end DESC);

ALTER TABLE cs2_hltv_team_ftu ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_team_ftu FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
