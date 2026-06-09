-- WC-PICKS-ONCONFLICT-FIX (2026-06-10)
--
-- Migration 170 replaced the human-side UNIQUE constraints on three WC tables
-- with PARTIAL unique indexes (`WHERE user_id IS NOT NULL`) so AI ghost rows
-- (user_id NULL, ai_label set) could share the table. That broke every
-- frontend `.upsert(..., { onConflict: "..." })` call: PostgREST's ON CONFLICT
-- inference can't pick a partial index unless the WHERE predicate is named
-- explicitly, and the Supabase JS client doesn't pass one. Result:
--
--     "there is no unique or exclusion constraint matching the ON CONFLICT
--      specification"
--
-- Hit first on wc_group_predictions (group standings predictor); same fault
-- also affects wc_bracket_picks and wc_bracket_meta.
--
-- Fix: drop the partial human-side indexes and replace them with regular
-- UNIQUE constraints on the same columns. Default NULLS DISTINCT means NULL
-- user_id rows (AI ghosts) coexist freely; their uniqueness is still enforced
-- by the *ai_label* partial indexes left in place from migration 170.

-- ── wc_group_predictions ───────────────────────────────────────────────────
DROP INDEX IF EXISTS uq_wc_grp_user;

ALTER TABLE wc_group_predictions
    DROP CONSTRAINT IF EXISTS uq_wc_group_predictions_user;
ALTER TABLE wc_group_predictions
    ADD CONSTRAINT uq_wc_group_predictions_user
    UNIQUE (user_id, group_letter, position);

-- ── wc_bracket_picks ───────────────────────────────────────────────────────
DROP INDEX IF EXISTS uq_wc_bracket_pick_user;

ALTER TABLE wc_bracket_picks
    DROP CONSTRAINT IF EXISTS uq_wc_bracket_picks_user;
ALTER TABLE wc_bracket_picks
    ADD CONSTRAINT uq_wc_bracket_picks_user
    UNIQUE (user_id, round, position);

-- ── wc_bracket_meta ────────────────────────────────────────────────────────
DROP INDEX IF EXISTS uq_wc_bracket_meta_user;

ALTER TABLE wc_bracket_meta
    DROP CONSTRAINT IF EXISTS uq_wc_bracket_meta_user;
ALTER TABLE wc_bracket_meta
    ADD CONSTRAINT uq_wc_bracket_meta_user
    UNIQUE (user_id);
