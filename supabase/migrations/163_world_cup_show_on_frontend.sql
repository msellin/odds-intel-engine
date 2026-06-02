-- WC-PHASE-1 (2026-06-02): Flip show_on_frontend for FIFA World Cup 2026.
-- Backfill of 72 group-stage fixtures via
--   python -m workers.jobs.fetch_fixtures --league 1 --season 2026
-- landed in `matches` but the league row has show_on_frontend=false, so
-- the frontend filter `WHERE l.show_on_frontend = true` hides them.

UPDATE leagues
SET show_on_frontend = true,
    updated_at = NOW()
WHERE api_football_id = 1   -- FIFA World Cup
  AND show_on_frontend = false;

-- Surface a couple of intentional siblings while we're at it:
-- WC Qualification — Intercontinental Play-offs (id=37) is the final 6-team
-- mini-tournament that determines the last 2 WC slots and runs alongside
-- the group stage. Backfill of finished qualifier matches is filed under
-- WC-PHASE-2 (see dev/active/world-cup-prep-tasks.md).
