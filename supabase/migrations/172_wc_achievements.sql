-- WC-ACHIEVEMENTS (2026-06-02): WC-themed achievement & streak system.
--
-- Achievements are PARALLEL to scoring — they do not affect wc_bracket_meta
-- totals. The detection job (`workers/jobs/wc_achievement_detection.py`)
-- scans current state every 15 minutes during the WC window and awards
-- badges idempotently. The UNIQUE (user_id, slug) constraint guarantees a
-- single achievement is never double-awarded.
--
-- Schema notes:
--   detail JSONB — optional metadata. Examples:
--     * vs_you_streak_5 → {"matches": ["uuid1","uuid2",...]}
--     * groups_perfect_one → {"groups": ["A","D"]}
--     * called_the_upset → {"team_id": "uuid", "advanced_to": "r16"}
--   The FE tooltip uses this to show proof when relevant. Null is fine.

CREATE TABLE IF NOT EXISTS wc_user_achievements (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references profiles(id) on delete cascade,
    slug         text not null,
    earned_at    timestamptz not null default now(),
    detail       jsonb,
    CONSTRAINT uq_wc_user_achievement UNIQUE (user_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_wc_user_achievements_user
    ON wc_user_achievements (user_id);

CREATE INDEX IF NOT EXISTS idx_wc_user_achievements_slug
    ON wc_user_achievements (slug);


-- ── RLS ─────────────────────────────────────────────────────────────────────
ALTER TABLE wc_user_achievements ENABLE ROW LEVEL SECURITY;

-- Public read — leaderboard rows render badges next to any user's name.
-- Writes only via server (service role), so no INSERT/UPDATE policy needed.
DROP POLICY IF EXISTS "wc_ach_public_select" ON wc_user_achievements;
CREATE POLICY "wc_ach_public_select" ON wc_user_achievements
    FOR SELECT USING (true);
