-- CS2-MATCH-ID-BRIDGE — durable bo3gg ↔ HLTV match-id mapping.
--
-- Sneak peeks anchor on cs2_results (bo3gg universe) but features live in
-- HLTV tables (cs2_hltv_matches and children). Exact-string team joins
-- collapse to <1% coverage from team-name drift (e.g. "FaZe Clan" vs
-- "FaZe", "Team Spirit" vs "Spirit"). This bridge persists the fuzzy
-- match once so every downstream sneak peek joins HLTV detail in O(1).
--
-- Populated by scripts/esports/cs2_match_id_bridge_populate.py.
-- Re-runnable: ON CONFLICT DO UPDATE on (bo3gg_id, hltv_match_id).

CREATE TABLE IF NOT EXISTS cs2_match_id_bridge (
  bo3gg_id        TEXT NOT NULL,
  hltv_match_id   BIGINT NOT NULL,
  confidence      NUMERIC NOT NULL,        -- 0.0-1.0
  joined_by       TEXT NOT NULL,           -- 'exact' | 'norm_team' | 'fuzzy' | 'manual'
  team_score_avg  NUMERIC,                 -- fuzz score across both team names
  time_drift_sec  INTEGER,                 -- |bo3gg_kickoff - hltv_kickoff|
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (bo3gg_id, hltv_match_id)
);

CREATE INDEX IF NOT EXISTS cs2_match_id_bridge_hltv_idx
    ON cs2_match_id_bridge (hltv_match_id);
CREATE INDEX IF NOT EXISTS cs2_match_id_bridge_bo3gg_idx
    ON cs2_match_id_bridge (bo3gg_id);

ALTER TABLE cs2_match_id_bridge ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_match_id_bridge FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
