-- WC-V2-T1 (2026-06-02): OddsIntel vs You — per-fixture 1X2 picks.
--
-- Separate from `user_picks` (which is the cross-site tracking table tied to
-- best-odds and result settlement) because the WC vs-You game has different
-- semantics:
--   * No odds attached — pure pick vs model 1X2 outcome
--   * Lock at kickoff (the global one), not at insert time
--   * Scoring is a simple "you N / model M out of K" — no PnL
-- Keeping it isolated avoids muddying the settlement pipeline that walks
-- user_picks for paper results.

CREATE TABLE IF NOT EXISTS wc_user_picks (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references profiles(id) on delete cascade,
    match_id   uuid not null references matches(id) on delete cascade,
    pick       text not null,                    -- '1' | 'X' | '2'
    locked_at  timestamptz,                      -- set when match kicks off
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    CONSTRAINT uq_wc_user_pick UNIQUE (user_id, match_id),
    CONSTRAINT chk_wc_user_pick CHECK (pick IN ('1', 'X', '2'))
);

CREATE INDEX IF NOT EXISTS idx_wc_user_picks_user
    ON wc_user_picks (user_id);

CREATE INDEX IF NOT EXISTS idx_wc_user_picks_match
    ON wc_user_picks (match_id);


-- ── RLS ─────────────────────────────────────────────────────────────────────
ALTER TABLE wc_user_picks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "wc_user_picks_own_select" ON wc_user_picks;
CREATE POLICY "wc_user_picks_own_select" ON wc_user_picks
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_user_picks_own_insert" ON wc_user_picks;
CREATE POLICY "wc_user_picks_own_insert" ON wc_user_picks
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_user_picks_own_update" ON wc_user_picks;
CREATE POLICY "wc_user_picks_own_update" ON wc_user_picks
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "wc_user_picks_own_delete" ON wc_user_picks;
CREATE POLICY "wc_user_picks_own_delete" ON wc_user_picks
    FOR DELETE USING (auth.uid() = user_id);
