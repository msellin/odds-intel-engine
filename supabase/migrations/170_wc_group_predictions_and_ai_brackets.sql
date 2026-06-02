-- WC-GROUP-PREDICTOR + WC-AI-GHOSTS (2026-06-02)
--
-- Two coordinated features that share the same leaderboard:
--
--   1. Group-stage standings predictor — users assign 1st/2nd/3rd/4th per
--      group BEFORE the first match. Locks at WC kickoff (2026-06-11 19:00 UTC,
--      same as the bracket). 12 groups × 4 positions = 48 picks per user.
--      Scoring (per group): 1st=5, 2nd=3, 3rd=2, 4th=1, perfect-group bonus=+5.
--      Max per group = 16; max total = 192.
--
--   2. AI ghost competitors — 5 named model entries on the SAME leaderboard
--      (Elite AI / Pro AI / Free AI / Market Implied / Chalk). Each one's
--      bracket + group-standings predictions are stored as rows with
--      `ai_label` set and `user_id` NULL. NOT eligible for prizes —
--      leaderboard UI shows a footnote; prize SQL filters `WHERE ai_label IS NULL`.
--
-- Combined leaderboard score = bracket_score + group_standings_score.
-- Same calculation for humans and AI ghosts; AI rows skipped for prizes only.
--
-- ── New table: per-user group-standings picks ──────────────────────────────

CREATE TABLE IF NOT EXISTS wc_group_predictions (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid references profiles(id) on delete cascade,  -- nullable for AI ghosts
    group_letter    text not null,                                    -- 'A' .. 'L'
    position        int  not null,                                    -- 1-4 (1st .. 4th)
    picked_team_id  uuid not null references teams(id),
    ai_label        text,                                             -- non-null for AI ghost rows
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    CONSTRAINT chk_wc_grp_position CHECK (position BETWEEN 1 AND 4),
    CONSTRAINT chk_wc_grp_letter   CHECK (group_letter ~ '^[A-L]$'),
    -- Exactly one of {user_id, ai_label} must be set (XOR).
    CONSTRAINT chk_wc_grp_owner    CHECK (
        (user_id IS NOT NULL AND ai_label IS NULL) OR
        (user_id IS NULL     AND ai_label IS NOT NULL)
    )
);

-- Uniqueness:
--  • Real users: (user_id, group_letter, position) — one row per slot per user.
--  • AI ghosts:  (ai_label, group_letter, position) — one row per slot per AI.
-- Implemented as two partial unique indexes (cleaner than NULLS NOT DISTINCT
-- which is Postgres 15+ and we want migration to work on older instances).
CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_grp_user
    ON wc_group_predictions (user_id, group_letter, position)
    WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_grp_ai
    ON wc_group_predictions (ai_label, group_letter, position)
    WHERE ai_label IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wc_grp_user
    ON wc_group_predictions (user_id) WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wc_grp_ai
    ON wc_group_predictions (ai_label) WHERE ai_label IS NOT NULL;


-- ── wc_bracket_picks — relax for AI ghosts ─────────────────────────────────
-- Existing column user_id is NOT NULL + FK to profiles. AI ghost rows need
-- user_id NULL + ai_label set.
ALTER TABLE wc_bracket_picks ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE wc_bracket_picks ADD COLUMN IF NOT EXISTS ai_label text;

-- Replace the existing unique constraint with two partial uniques (user/ai).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_wc_bracket_pick' AND conrelid = 'wc_bracket_picks'::regclass
    ) THEN
        ALTER TABLE wc_bracket_picks DROP CONSTRAINT uq_wc_bracket_pick;
    END IF;
END$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_bracket_pick_user
    ON wc_bracket_picks (user_id, round, position)
    WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_bracket_pick_ai
    ON wc_bracket_picks (ai_label, round, position)
    WHERE ai_label IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wc_bracket_picks_ai
    ON wc_bracket_picks (ai_label) WHERE ai_label IS NOT NULL;


-- ── wc_bracket_meta — add total_score + group_score + percentile + ai_label ─
ALTER TABLE wc_bracket_meta
    ADD COLUMN IF NOT EXISTS group_standings_score int NOT NULL DEFAULT 0;

ALTER TABLE wc_bracket_meta
    ADD COLUMN IF NOT EXISTS total_score int NOT NULL DEFAULT 0;

-- numeric(5,2) — e.g. 78.50 means user is at the 78.5th percentile (top 78.5%).
ALTER TABLE wc_bracket_meta
    ADD COLUMN IF NOT EXISTS current_percentile numeric(5,2);

ALTER TABLE wc_bracket_meta
    ADD COLUMN IF NOT EXISTS ai_label text;

-- ── Re-key wc_bracket_meta so AI ghost rows can have user_id NULL ─────────
-- ORDER MATTERS:
--   1. Add the new `id` surrogate column (idempotent)
--   2. Backfill any existing rows' id
--   3. Lift the existing PK off user_id (Postgres won't let us drop NOT NULL
--      on a column that's still part of a primary key — sqlstate 42P16, which
--      is exactly the error this migration first failed on)
--   4. NOW drop NOT NULL on user_id
--   5. Promote `id` to the new PK
--   6. Re-establish per-user uniqueness via a partial unique index

-- Step 1+2 — surrogate column + backfill
ALTER TABLE wc_bracket_meta ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid();
UPDATE wc_bracket_meta SET id = COALESCE(id, gen_random_uuid()) WHERE id IS NULL;
ALTER TABLE wc_bracket_meta ALTER COLUMN id SET NOT NULL;

-- Step 3 — drop the existing PK off user_id (if it exists)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'wc_bracket_meta_pkey' AND conrelid = 'wc_bracket_meta'::regclass
    ) THEN
        ALTER TABLE wc_bracket_meta DROP CONSTRAINT wc_bracket_meta_pkey;
    END IF;
END$$;

-- Step 4 — NOW we can drop NOT NULL on user_id. AI ghost rows store user_id NULL.
ALTER TABLE wc_bracket_meta ALTER COLUMN user_id DROP NOT NULL;

-- Step 5 — promote `id` to the new PK
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'wc_bracket_meta_id_pkey' AND conrelid = 'wc_bracket_meta'::regclass
    ) THEN
        ALTER TABLE wc_bracket_meta ADD CONSTRAINT wc_bracket_meta_id_pkey PRIMARY KEY (id);
    END IF;
END$$;

-- Re-establish the per-user uniqueness via a partial unique index (one row
-- per human user; AI ghosts get one row per ai_label).
CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_bracket_meta_user
    ON wc_bracket_meta (user_id) WHERE user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_bracket_meta_ai
    ON wc_bracket_meta (ai_label) WHERE ai_label IS NOT NULL;

-- Leaderboard sort — combined ranking by total_score.
CREATE INDEX IF NOT EXISTS idx_wc_bracket_meta_total
    ON wc_bracket_meta (total_score DESC);


-- ── RLS — group predictions ────────────────────────────────────────────────
ALTER TABLE wc_group_predictions ENABLE ROW LEVEL SECURITY;

-- Users read/write only their own picks. AI rows (user_id NULL) are written
-- server-side by the AI ghost generator script using the service role and
-- are NOT exposed to anonymous viewers (a viewer reading other users' picks
-- pre-lock would leak strategy).
DROP POLICY IF EXISTS "wc_grp_own_select" ON wc_group_predictions;
CREATE POLICY "wc_grp_own_select" ON wc_group_predictions
    FOR SELECT USING (user_id IS NOT NULL AND auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_grp_own_insert" ON wc_group_predictions;
CREATE POLICY "wc_grp_own_insert" ON wc_group_predictions
    FOR INSERT WITH CHECK (user_id IS NOT NULL AND auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_grp_own_update" ON wc_group_predictions;
CREATE POLICY "wc_grp_own_update" ON wc_group_predictions
    FOR UPDATE USING (user_id IS NOT NULL AND auth.uid() = user_id)
               WITH CHECK (user_id IS NOT NULL AND auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_grp_own_delete" ON wc_group_predictions;
CREATE POLICY "wc_grp_own_delete" ON wc_group_predictions
    FOR DELETE USING (user_id IS NOT NULL AND auth.uid() = user_id);

-- Note: existing wc_bracket_picks + wc_bracket_meta RLS policies remain in
-- place. AI ghost rows live with user_id NULL; the existing policies
-- (`auth.uid() = user_id`) naturally fail-closed for AI rows under anon — the
-- leaderboard reads AI rows through the public-readable `wc_bracket_meta`
-- policy which is `USING (true)`, so this is fine.
