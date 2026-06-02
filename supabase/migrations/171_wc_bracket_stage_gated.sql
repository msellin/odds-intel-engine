-- WC-BRACKET-STAGE-GATED (2026-06-02)
--
-- Rewrites the World Cup bracket from single-lock + set-membership scoring
-- to a stage-gated BBC pattern: each knockout round opens for picks only
-- after the previous round resolves, and scoring is POSITIONAL (winner of
-- THIS specific matchup, not "any team that advanced").
--
-- This migration is additive and reversible — no existing rows are dropped
-- and no columns are renamed. The semantics change lives entirely in app
-- code (workers/jobs/wc_bracket_scoring.py + the bracket page).
--
-- ── New: wc_bracket_slot_assignments ───────────────────────────────────────
-- Maps each bracket slot (round, position) to the actual AF match once
-- that round's fixtures are seeded. Populated by workers/jobs/wc_bracket_slot_sync.py.
-- Slot count per round:
--   r32: 16, r16: 8, qf: 4, sf: 2, final: 1.
-- Champion is NOT modelled as a slot — it's derived from the (final, 0)
-- pick in app code, so the user only ever explicitly picks the Final winner.

CREATE TABLE IF NOT EXISTS wc_bracket_slot_assignments (
    id           uuid primary key default gen_random_uuid(),
    round        text not null,                     -- 'r32' | 'r16' | 'qf' | 'sf' | 'final'
    position     int  not null,                     -- 0..15 / 0..7 / 0..3 / 0..1 / 0
    match_id     uuid references matches(id),       -- nullable until AF seeds the round
    seeded_at    timestamptz,                       -- when we first filled match_id
    locked_at    timestamptz,                       -- when picks for this round lock (= first match kickoff in this round)
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),

    CONSTRAINT uq_wc_slot UNIQUE (round, position),
    CONSTRAINT chk_wc_slot_round CHECK (round IN ('r32','r16','qf','sf','final')),
    CONSTRAINT chk_wc_slot_position CHECK (position >= 0 AND position <= 15)
);

CREATE INDEX IF NOT EXISTS idx_wc_slot_round
    ON wc_bracket_slot_assignments (round);
CREATE INDEX IF NOT EXISTS idx_wc_slot_match
    ON wc_bracket_slot_assignments (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wc_slot_locked
    ON wc_bracket_slot_assignments (round, locked_at) WHERE locked_at IS NOT NULL;

-- RLS — read-only for anonymous; writes only via service-role (scheduler job).
ALTER TABLE wc_bracket_slot_assignments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "wc_slot_public_select" ON wc_bracket_slot_assignments;
CREATE POLICY "wc_slot_public_select" ON wc_bracket_slot_assignments
    FOR SELECT USING (true);


-- ── matches.round_label — store AF's free-text round name ──────────────────
-- AF returns strings like 'Round of 32 - 1', 'Quarter-finals - 2', 'Final'.
-- Storing the raw text lets the slot-sync job derive (round, position)
-- without re-querying AF. Nullable so non-WC matches stay untouched.

ALTER TABLE matches ADD COLUMN IF NOT EXISTS round_label text;

CREATE INDEX IF NOT EXISTS idx_matches_round_label
    ON matches (round_label) WHERE round_label IS NOT NULL;


-- ── Seed empty slot rows so the FE can render a complete bracket skeleton ──
-- (Even before AF seeds the actual matches, the FE renders 16 R32 cards
-- showing "Opens after group stage". This is just empty rows; match_id is
-- null and locked_at is null.)
INSERT INTO wc_bracket_slot_assignments (round, position)
SELECT 'r32', generate_series(0, 15)
ON CONFLICT (round, position) DO NOTHING;

INSERT INTO wc_bracket_slot_assignments (round, position)
SELECT 'r16', generate_series(0, 7)
ON CONFLICT (round, position) DO NOTHING;

INSERT INTO wc_bracket_slot_assignments (round, position)
SELECT 'qf', generate_series(0, 3)
ON CONFLICT (round, position) DO NOTHING;

INSERT INTO wc_bracket_slot_assignments (round, position)
SELECT 'sf', generate_series(0, 1)
ON CONFLICT (round, position) DO NOTHING;

INSERT INTO wc_bracket_slot_assignments (round, position)
VALUES ('final', 0)
ON CONFLICT (round, position) DO NOTHING;
