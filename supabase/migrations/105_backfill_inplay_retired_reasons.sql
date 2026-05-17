-- PERF-RETIRED-CLEANUP (2026-05-17): backfill retired_reason for three early
-- inplay experiments that were soft-retired on 2026-05-09 with NULL reason.
-- The pipeline already skips them (gate is `is_active AND retired_at IS NULL`)
-- but they show up on the public /performance Retired Strategies section
-- without context — a NULL reason looks like data we forgot, not transparency.
--
-- inplay_a2 / inplay_c_home: 0 bets ever placed. Will be filtered out of the
-- public view by the frontend (no story to tell at 0 bets), but the reason
-- is set anyway for /admin/bots and audit purposes.
-- inplay_f: 3 settled bets, -€2.60 — too small to evaluate. Will show on the
-- public page with the reason text.

UPDATE bots SET retired_reason =
    'Early inplay variant retired 2026-05-09 — superseded by inplay_a / inplay_b before final config. Never placed a bet in production.'
WHERE name = 'inplay_a2' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
    'Home-side variant of inplay_c retired 2026-05-09 — strategy collapsed into the unified inplay_c during the May 9 inplay reorganization. Never placed a bet in production.'
WHERE name = 'inplay_c_home' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
    'Early inplay variant retired 2026-05-09 — superseded during the May 9 inplay reorganization. Only 3 settled bets before retirement, too small a sample to evaluate.'
WHERE name = 'inplay_f' AND retired_reason IS NULL;
