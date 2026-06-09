-- Per-side (CT/T) halftime scores per map. Critical for tier-2/3 betting:
-- the side a team starts on shifts pistol-round expectations and overall
-- map win prob meaningfully.

ALTER TABLE cs2_hltv_match_maps
    ADD COLUMN IF NOT EXISTS team1_ct_rounds   INTEGER,
    ADD COLUMN IF NOT EXISTS team1_t_rounds    INTEGER,
    ADD COLUMN IF NOT EXISTS team2_ct_rounds   INTEGER,
    ADD COLUMN IF NOT EXISTS team2_t_rounds    INTEGER;
