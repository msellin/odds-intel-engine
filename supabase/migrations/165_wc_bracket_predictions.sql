-- WC-V2-T1 (2026-06-02): Bracket predictor game for World Cup 2026.
--
-- Two tables:
--   wc_bracket_picks  — one row per (user, round, slot) — the user's bracket
--   wc_bracket_meta   — one row per user — golden boot + score + rank cache
--
-- Lock semantics: the first WC kickoff is 2026-06-11 19:00 UTC. UI enforces
-- the lock and server actions re-check via `now() < '2026-06-11T19:00:00Z'`.
-- We do NOT add a check constraint on created_at — operator override needs
-- to remain trivial in dev. `wc_bracket_meta.locked_at` records the user's
-- own lock-in time (independent of the global cutoff) for audit.
--
-- Scoring (recomputed on every result write; lives in app code, not SQL):
--   R32 = 1pt   R16 = 2pt   QF = 4pt   SF = 8pt   Final = 16pt
--   Champion = 32pt   Golden Boot = 10pt
--   Max possible = 83 pts (16 + 8 + 4 + 2 + 1 + 32 + 10 = wait, let's check:
--     R32 16 slots * 1 = 16
--     R16  8 slots * 2 = 16
--     QF   4 slots * 4 = 16
--     SF   2 slots * 8 = 16
--     Final 1 slot * 16 = 16
--     Champion 1 slot * 32 = 32
--     Golden Boot           = 10
--     Maximum = 16+16+16+16+16+32+10 = 122. The spec says 83 — using spec's
--     value in the app layer; this comment is informational only.)

CREATE TABLE IF NOT EXISTS wc_bracket_picks (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references profiles(id) on delete cascade,
    round         text not null,           -- 'r32' | 'r16' | 'qf' | 'sf' | 'final' | 'champion'
    position      int not null,            -- slot number within the round (0-indexed)
    picked_team_id uuid not null references teams(id),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),

    CONSTRAINT uq_wc_bracket_pick UNIQUE (user_id, round, position),
    CONSTRAINT chk_wc_bracket_round CHECK (round IN ('r32', 'r16', 'qf', 'sf', 'final', 'champion'))
);

CREATE INDEX IF NOT EXISTS idx_wc_bracket_picks_user
    ON wc_bracket_picks (user_id);

CREATE INDEX IF NOT EXISTS idx_wc_bracket_picks_team
    ON wc_bracket_picks (picked_team_id);


CREATE TABLE IF NOT EXISTS wc_bracket_meta (
    user_id            uuid primary key references profiles(id) on delete cascade,
    golden_boot_player text,
    locked_at          timestamptz,
    current_score      int not null default 0,
    current_rank       int,
    updated_at         timestamptz not null default now()
);

-- Leaderboard index — DESC for "top N by score".
CREATE INDEX IF NOT EXISTS idx_wc_bracket_meta_score
    ON wc_bracket_meta (current_score DESC);

-- ── RLS ─────────────────────────────────────────────────────────────────────
ALTER TABLE wc_bracket_picks ENABLE ROW LEVEL SECURITY;
ALTER TABLE wc_bracket_meta  ENABLE ROW LEVEL SECURITY;

-- Picks: users read/write only their own rows. Anonymous viewers cannot see
-- individual picks (would leak bracket pre-lock). Leaderboard reads go via
-- wc_bracket_meta which is anon-readable below.
DROP POLICY IF EXISTS "wc_picks_own_select" ON wc_bracket_picks;
CREATE POLICY "wc_picks_own_select" ON wc_bracket_picks
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_picks_own_insert" ON wc_bracket_picks;
CREATE POLICY "wc_picks_own_insert" ON wc_bracket_picks
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_picks_own_update" ON wc_bracket_picks;
CREATE POLICY "wc_picks_own_update" ON wc_bracket_picks
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_picks_own_delete" ON wc_bracket_picks;
CREATE POLICY "wc_picks_own_delete" ON wc_bracket_picks
    FOR DELETE USING (auth.uid() = user_id);

-- Meta: leaderboard column subset must be visible to anonymous viewers for
-- /world-cup/bracket/leaderboard. Anon-readable is fine — username/avatar
-- joins happen in app code with profiles.display_name (already public).
DROP POLICY IF EXISTS "wc_meta_public_select" ON wc_bracket_meta;
CREATE POLICY "wc_meta_public_select" ON wc_bracket_meta
    FOR SELECT USING (true);

DROP POLICY IF EXISTS "wc_meta_own_insert" ON wc_bracket_meta;
CREATE POLICY "wc_meta_own_insert" ON wc_bracket_meta
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_meta_own_update" ON wc_bracket_meta;
CREATE POLICY "wc_meta_own_update" ON wc_bracket_meta
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
