-- Bot retirement: hide merged/dropped bots from the public /performance page
-- while keeping them visible in the admin dashboard with a toggle. Retired
-- bots no longer appear in dashboard_cache.bot_breakdown.
ALTER TABLE bots
    ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;

-- Retire bots merged or dropped on 2026-05-08:
--   inplay_a2     — merged into A (single low-scoring xG divergence strategy)
--   inplay_c_home — merged into C (home favourite branch)
--   inplay_f      — dropped (Odds Momentum Reversal — sharp books already price the signal)
UPDATE bots
SET retired_at = NOW()
WHERE name IN ('inplay_a2', 'inplay_c_home', 'inplay_f')
  AND retired_at IS NULL;
