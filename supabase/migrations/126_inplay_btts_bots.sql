-- INPLAY-BTTS-AH-BOTS (2026-05-24): now that live BTTS + AH odds flow into
-- odds_snapshots (LIVE-BTTS-AH-FIX, 2026-05-24), give the in-play bots
-- structured columns on live_match_snapshots to read without re-querying.
-- Also adds the AH main-line trio for the next-iteration AH bot — populated
-- once the AH-bot ships, NULL until then.

ALTER TABLE live_match_snapshots
    ADD COLUMN IF NOT EXISTS live_btts_yes     NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS live_btts_no      NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS live_ah_main_line NUMERIC(4,2),
    ADD COLUMN IF NOT EXISTS live_ah_home_odds NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS live_ah_away_odds NUMERIC(6,3);

-- Register two BTTS-Yes inplay bots. AH bot deferred (INPLAY-AH-BOTS-V1) until
-- we've sorted the half-line/quarter-line resolution math.
INSERT INTO bots (name, strategy, description)
VALUES
    ('inplay_btts_press_v1',
     'BTTS Yes Late Press — score 1-0/0-1 min 35-75, both teams creating, prematch BTTS ≥ 0.42, live BTTS Yes ≥ 1.90, bet BTTS Yes',
     'BTTS Yes Late Press — score 1-0/0-1 min 35-75, both teams creating, prematch BTTS ≥ 0.42, live BTTS Yes ≥ 1.90, bet BTTS Yes'),
    ('inplay_btts_dryspell_v1',
     'BTTS Yes Dry Spell — 0-0 min 55-80, both teams creating ≥5 SoT total, prematch BTTS ≥ 0.50, live BTTS Yes ≥ 2.80, bet BTTS Yes',
     'BTTS Yes Dry Spell — 0-0 min 55-80, both teams creating ≥5 SoT total, prematch BTTS ≥ 0.50, live BTTS Yes ≥ 2.80, bet BTTS Yes')
ON CONFLICT (name) DO UPDATE
    SET description = EXCLUDED.description,
        strategy    = EXCLUDED.strategy;
