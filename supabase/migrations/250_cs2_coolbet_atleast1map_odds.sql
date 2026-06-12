-- CS2-COOLBET-ATLEAST1MAP-EDGE (2026-06-12): Coolbet odds for the
-- "wins at least 1 map" market in BO3 matches. Mirrors coolbet_odds1/2
-- (match winner) but for the +1.5 map handicap line which is equivalent
-- to "wins ≥1 map" for the team that has the +1.5 head start.
--
-- We already compute fair_odds_map1/2 (model's true odds) and
-- threshold_map1/2 (min acceptable price) but never compared against
-- a real bookmaker — so the admin page couldn't show edge for that
-- market. With these columns populated by cs2_coolbet_scanner, the
-- frontend can render edge exactly like it does for match_winner.

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS coolbet_odds_map1 NUMERIC,
    ADD COLUMN IF NOT EXISTS coolbet_odds_map2 NUMERIC;

COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_map1 IS
    'Coolbet odds for team1 winning at least 1 map (BO3+). Populated by '
    'cs2_coolbet_scanner from the "Match Handicap" market (+1.5 line). '
    'NULL when Coolbet hasn''t opened the market yet, or the match is '
    'BO1 (atleast-1-map = match win, so no separate market).';

COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_map2 IS
    'Coolbet odds for team2 winning at least 1 map (BO3+). See _map1.';
