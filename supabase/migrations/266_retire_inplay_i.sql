-- INPLAY-I-30-BET-WATCH gate triggered 2026-06-30.
-- Retirement criteria: ROI < -2% AND n >= 25 settled.
-- Actual: -22.7% ROI on 28 settled bets (hit rate 21.4%).
-- Strategy: Favourite Stall — strong fav 0-0 min 42-65, live fav odds drifted >= 3.0.
-- High-odds range (avg ~5.0) means even a marginal model edge can't survive friction.
UPDATE bots
SET
    is_active    = false,
    retired_at   = NOW(),
    retired_reason = 'INPLAY-I-30-BET-WATCH 2026-06-30: -22.7% ROI on 28 settled bets (gate: ROI<-2% AND n>=25). Favourite-stall at odds 4.0-6.0 — hit rate 21.4% vs ~20% implied. No evidence of model edge over market at these late-game odds. No re-enable path without a structural odds-drift signal improvement.'
WHERE name = 'inplay_i';
