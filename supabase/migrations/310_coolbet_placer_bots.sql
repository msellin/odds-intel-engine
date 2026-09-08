-- COOLBET-PLACER-CONTROL-2026-09-08 — runtime on/off for real-money Coolbet UI
-- placement, per bot, WITHOUT touching pick generation.
--
-- Until now which bots the UI placer (scripts/place_coolbet_ui.py) may stake
-- real money on was a CODE-LEVEL set (EXECUTE_ALLOWED_BOTS) plus an env flag
-- (COOLBET_UI_MODEL_EDGE_OU). Flipping a bot on or off meant an edit + deploy.
-- This table makes it a runtime toggle a superadmin flips from /admin/shadow-bots.
--
-- SAFETY MODEL — this table can only ever REDUCE what places, never expand it.
-- The placer keeps a hard code-level whitelist PLACEABLE_BOTS = the set of bots
-- that may EVER place. The effective real-money allowlist is
--     PLACEABLE_BOTS  ∩  (rows here WHERE ui_place_enabled = true).
-- A bot not in PLACEABLE_BOTS can NEVER place even if someone inserts an enabled
-- row here; and on ANY DB error the placer reads this as the EMPTY set and
-- places nothing (fail closed). So an attacker or a mistake in this table can
-- turn placement OFF but can never turn on a bot the code does not already trust.
--
-- ON CONFLICT (bot_name) DO NOTHING on re-run: the seed must NOT clobber a
-- value a human has since changed. Re-applying the migration is a no-op once
-- the rows exist.

CREATE TABLE IF NOT EXISTS coolbet_placer_bots (
    bot_name         text        PRIMARY KEY,
    ui_place_enabled boolean     NOT NULL DEFAULT false,
    note             text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE coolbet_placer_bots IS
    'Runtime on/off for real-money Coolbet UI placement per bot (COOLBET-PLACER-CONTROL). '
    'Effective allowlist = code-level PLACEABLE_BOTS ∩ rows here WHERE ui_place_enabled. '
    'Can only reduce placement, never expand it; placer fails closed (empty) on any read error.';

-- Seed exactly the two placeable bots. value_v1 stays ON (its 1x2 line-shop is
-- +13% and it already placed real money before this table existed). The
-- model-edge O/U bot stays OFF pending the dedup / placement-of-record work
-- (COOLBET-REALMONEY-EDGE-GATE-RECONCILE) — it must NOT auto-enable here.
INSERT INTO coolbet_placer_bots (bot_name, ui_place_enabled, note, updated_at) VALUES
    ('bot_coolbet_value_v1',    true,  'Coolbet 1x2 line-shop',                                            now()),
    ('bot_coolbet_ou_model_v1', false, 'Coolbet O/U model-edge — OFF pending dedup/placement-of-record',   now())
ON CONFLICT (bot_name) DO NOTHING;

-- RLS: this is a REAL-MONEY control table exposed through PostgREST, so the anon
-- and authenticated roles must not touch it directly — the /admin/shadow-bots
-- superadmin gate + the /api/admin/coolbet-placer-bots server route (service
-- role, which BYPASSES RLS) are the only intended writers. Without this, a
-- direct PostgREST call under the anon key could flip a toggle and, e.g., enable
-- O/U real-money placement (which IS inside PLACEABLE_BOTS), sidestepping the
-- superadmin check entirely. Mirrors the user_pick_marks lockdown (migration 278).
-- The engine reads this table over a direct psycopg2 connection as the table
-- owner, which is unaffected by these policies.
ALTER TABLE coolbet_placer_bots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "coolbet_placer_bots no anon" ON coolbet_placer_bots;

CREATE POLICY "coolbet_placer_bots no anon"
    ON coolbet_placer_bots
    FOR ALL
    TO anon, authenticated
    USING (false)
    WITH CHECK (false);
