-- USER-PICK-MARKS 2026-08-22
--
-- Per-user checkbox on /picks so the operator can mark which upcoming picks
-- they have manually placed with a book. Operator ships ~9 picks/day and
-- doesn't remember which ones already went in — a persistent per-row tick
-- box solves it.
--
-- Scope: purely a personal bookkeeping surface. Not exposed to other users,
-- not used by the model, not used by any bot. One user, one pick, one row.
--
-- pick_id references simulated_bets.id (UUID) — no FK because we may want
-- marks to survive rows migrating tables, and PostgREST reads always go
-- through the server-side /api/me/pick-marks route with an explicit
-- user_id filter (service key bypasses RLS). No cascade needed either —
-- when a pick disappears the mark just becomes a dangling row we can
-- prune on read.

CREATE TABLE IF NOT EXISTS user_pick_marks (
    user_id     uuid        NOT NULL,
    pick_id     uuid        NOT NULL,
    marked_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, pick_id)
);

CREATE INDEX IF NOT EXISTS idx_user_pick_marks_user
    ON user_pick_marks (user_id);

-- Belt-and-braces: anon + authenticated PostgREST roles get no direct access.
-- The /api/me/pick-marks server route is the only writer, using the service
-- key with explicit user_id scoping.
ALTER TABLE user_pick_marks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_pick_marks no anon" ON user_pick_marks;

CREATE POLICY "user_pick_marks no anon"
    ON user_pick_marks
    FOR ALL
    TO anon, authenticated
    USING (false)
    WITH CHECK (false);

COMMENT ON TABLE user_pick_marks IS
    'Per-user checkbox marking a pick (simulated_bets.id) as "I placed this bet manually". Server-only writes via /api/me/pick-marks.';
