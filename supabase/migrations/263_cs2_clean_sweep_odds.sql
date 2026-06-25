-- CS2 clean-sweep market — Coolbet odds (2026-06-25).
--
-- The same Coolbet "Match Handicap" market that prices `atleast1map`
-- (+1.5 handicap, "wins ≥1 map") also prices its mirror: the -1.5 handicap
-- ("must win 2-0 in BO3 / 3-0 in BO5") = a clean sweep. The scanner already
-- fetches both outcomes from this market but currently discards the -1.5
-- side. New columns capture it so the bot can trade the market.
--
-- Model probability for clean_sweep is derived on the fly in cs2_bot.py
-- as win_prob_i² for BO3 (i³ for BO5) — no stored fair_odds column needed.

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS coolbet_odds_cs1 NUMERIC,
    ADD COLUMN IF NOT EXISTS coolbet_odds_cs2 NUMERIC;

COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_cs1 IS
    'Coolbet odds for team1 to clean-sweep (win 2-0 in BO3, 3-0 in BO5). '
    'Same Coolbet Match Handicap market as coolbet_odds_map1/2 (atleast1map), '
    'but the -1.5 outcome instead of +1.5. Populated by cs2_coolbet_scanner '
    'since CS2-CLEAN-SWEEP 2026-06-25. NULL on BO1 — clean sweep undefined.';

COMMENT ON COLUMN cs2_upcoming_matches.coolbet_odds_cs2 IS
    'Coolbet odds for team2 to clean-sweep. See _cs1.';
