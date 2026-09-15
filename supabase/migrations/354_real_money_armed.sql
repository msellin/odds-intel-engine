-- 354_real_money_armed.sql
-- OWN-ARMED-UNDER-PAUSE (2026-09-15) — Phase 0 of the OWN implementation plan
-- (dev/active/own-implementation-plan.md, docs/OWN_STRATEGY_AUDIT_2026_09_15.md §4).
--
-- WHY. Real money was "paused" on 2026-09-14 (migration 343) and the placement
-- stack was still ARMED underneath the pause:
--   * best_price_router ran `--execute` at :20/:50 and was inert ONLY because
--     the env var ROUTER_ALLOW_REAL was unset in its launchd plist;
--   * the router iterated PLACEABLE_BOTS (code whitelist) rather than the DB
--     toggle, so ROUTER_ALLOW_REAL=1 alone would have staked with every bot's
--     ui_place_enabled = FALSE;
--   * is_placement_paused() failed OPEN on a DB error.
-- An env var's ABSENCE on one host is not a pause. It is an accident that has
-- not happened yet.
--
-- WHAT. One explicit, DB-resident, default-FALSE arming switch that every
-- executor reads through `workers/automation/placement_gate.py::assert_run_may_place()`
-- FAIL-CLOSED. `placement_paused` remains the kill switch (TRUE = stop now);
-- `real_money_armed` is the arming switch (FALSE = never start). Both must be
-- in the permissive state for money to move. They are separate because their
-- defaults differ: a fresh row is NOT paused (nothing to stop) and NOT armed
-- (nothing may start) — the safe state on both axes.
--
-- The owner arms it explicitly, with a reason, in Phase 3 of the OWN plan —
-- never a migration, never a deploy side effect.
--
-- Re-appliable (RE-APPLIABLE-MIGRATIONS 2026-09-14).

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS real_money_armed        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS real_money_armed_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS real_money_armed_reason TEXT;

COMMENT ON COLUMN coolbet_session_state.real_money_armed IS
    'ARMING switch for real-money placement on every executor (UI placer, '
    'best-price router incl. Unibet arm, API placer, manual-place drain). '
    'FALSE = no executor may stake, regardless of bot toggles or env vars. '
    'Read fail-closed by workers/automation/placement_gate.py. Distinct from '
    'placement_paused (the KILL switch): both must permit. Owner arms it '
    'explicitly with a reason; never set by a migration or a deploy.';

-- Belt and braces: whatever state the row is in, this migration leaves real
-- money DISARMED. Arming is an owner action after Phase 0 is verified.
UPDATE coolbet_session_state
   SET real_money_armed = FALSE,
       real_money_armed_at = NULL,
       real_money_armed_reason = 'disarmed by migration 354 (OWN-ARMED-UNDER-PAUSE); owner arms explicitly'
 WHERE id = 1;

-- ── real_bets.shadow_bet_id — REAL-BETS-SHADOW-LINK (found 2026-09-15) ──────
-- WHY. The real-money bots pick from `shadow_bets`, but `real_bets` has only
-- `simulated_bet_id` (FK → simulated_bets). EDGE-PCT-TAKEN-RECORDED (2026-09-13)
-- made the UI placer pass the shadow pick id into that column, so EVERY
-- confirmed placement after it failed its ledger write with a foreign-key
-- violation ("placed but could not write real_bets"). Money moved; the ledger
-- did not know. All 142 confirmed rows carry simulated_bet_id NULL, and the one
-- placement after the fix is the orphan scripts/reconcile_placed_attempts_to_real_bets.py
-- repairs. The link gets its own column with its own FK; store_real_bet routes
-- the id to whichever table actually holds it.
ALTER TABLE real_bets
    ADD COLUMN IF NOT EXISTS shadow_bet_id UUID REFERENCES shadow_bets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS real_bets_shadow_bet_id_idx ON real_bets (shadow_bet_id);
COMMENT ON COLUMN real_bets.shadow_bet_id IS
    'The shadow_bets pick this real stake came from (the real-money bots pick from '
    'shadow_bets). simulated_bet_id stays for the legacy pipeline bots. '
    'REAL-BETS-SHADOW-LINK 2026-09-15.';
