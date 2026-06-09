-- Which side each team started on (first half).
-- Match-page HTML exposes this in the center half-score block — we already
-- parse the per-half scores via _HALF_SCORE_RE, just weren't storing the side.
--
-- Why this matters: map side bias is real (Nuke +14pp CT, Anubis +14pp T,
-- Overpass +12.8pp CT). Which side a team STARTS on (knife round result)
-- is a 4-8% win-prob shift on lopsided maps. Currently we capture per-side
-- rounds (team1_ct_rounds + team1_t_rounds) but not the order — i.e. we
-- know team1 scored 5 CT and 8 T, but don't know if they started CT or T.
-- team2's starting side is just the opposite of team1's.

ALTER TABLE cs2_hltv_match_maps
    ADD COLUMN IF NOT EXISTS team1_first_half_side TEXT;  -- 'ct' | 't' | NULL
