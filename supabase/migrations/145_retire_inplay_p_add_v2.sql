-- INPLAY-P-V2 (2026-05-28):
-- inplay_p showed -15.4% ROI across 192 settled bets. Root cause: two bad odds
-- buckets — 2.50-2.99 (-49.1% ROI, 27 bets) and 5.0+ (-56% ROI, 67 bets).
-- Remaining buckets (2.20-2.49, 3.00-4.99) combined for +6.3% ROI on 125 bets.
-- Retired inplay_p and created inplay_p_v2 excluding both bad buckets.

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    retired_reason = 'Replaced by inplay_p_v2 on 2026-05-28. 2.50-2.99 odds bucket was -49.1% ROI (27 bets) and 5.0+ was -56% ROI (67 bets). Remaining buckets were +6.3% ROI — v2 excludes the -EV ranges.'
WHERE name = 'inplay_p';

INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
SELECT
    'inplay_p_v2',
    'inplay_p_v2',
    'Post-Equalizer v2 — equalizing team at 1-1 within 4min, live win odds 2.20-2.49 or 3.00-5.00, Poisson edge ≥ 3%',
    starting_bankroll,
    current_bankroll,
    true
FROM bots
WHERE name = 'inplay_p'
ON CONFLICT (name) DO NOTHING;
