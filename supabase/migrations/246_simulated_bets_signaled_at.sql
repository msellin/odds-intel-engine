-- COOLBET-SIGNALER-A (2026-06-12): track which simulated_bets we've already
-- sent a Telegram bet-signal for. Without this, the signaler would resend
-- the same pick every pipeline run (every ~15 min) since the natural dedup
-- against real_bets only fires after a placement, and option A's design is
-- "signal-only, no placement on Railway".
--
-- One column, no view/index — the signaler reads simulated_bets directly
-- and dedup is a single boolean check. signaled_at also lets us answer
-- "what was signaled when" in /today and admin dashboards.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS signaled_at TIMESTAMPTZ;

COMMENT ON COLUMN simulated_bets.signaled_at IS
    'Set by workers/automation/coolbet_signaler.py when a Telegram bet-signal '
    'has been sent for this pick. NULL = not yet signaled. Prevents duplicate '
    'signals across pipeline runs. Independent of real_bets — the signaler '
    'never writes real_bets (that''s the placer''s job).';
