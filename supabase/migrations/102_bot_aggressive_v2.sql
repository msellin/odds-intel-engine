-- AGGRESSIVE-V2 (2026-05-17): register bot_aggressive_v2 — tightened sibling of bot_aggressive.
-- ensure_bots() also creates the row lazily on next pipeline run, but registering here
-- gives the migration trail a clear marker and makes the row available before the first
-- morning cohort runs.
INSERT INTO bots (name, strategy, description)
VALUES (
    'bot_aggressive_v2',
    'AGGRESSIVE-V2 — drop draws + OU under 2.5; cap odds 1.50-3.30; min edge 5%; selection_filter=[Home,Away,Over 2.5]',
    'Tightened sibling of bot_aggressive. v1 retroactive replay under v2 rules: 129/441 bets kept at +11.6% ROI / +€90 vs v1''s -5.7% / -€141.'
)
ON CONFLICT (name) DO UPDATE
    SET strategy = EXCLUDED.strategy,
        description = EXCLUDED.description;
