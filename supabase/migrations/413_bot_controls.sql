-- 413 — #139 phase A: the /admin/bots control panel's write path (2026-09-24).
--
-- (Numbered 413, not 412: 412_rating_1x2_tables.sql from #141 was applied first.)
--
-- Spec: dev/active/bots-control-panel-spec.md §2.2, with the owner decisions in §16 taking
-- precedence over the earlier text. This migration adds the audit log, the single write
-- function behind every page control, the owner arming function, the per-bot lock, the
-- DB-held real-money eligibility list (seeded OFF for every bot with a placement path), and
-- the Mac placer heartbeat.
--
-- WHY ONE FUNCTION. Every earlier toggle (the placer switch, Telegram /pause, the footprint
-- pause) was a bare UPDATE with no reason field and no history. Migration 343 found the
-- result: a note saying "OFF pending dry-run" on a row whose flag read TRUE, and nothing to
-- say who flipped it or when. admin_set_control() locks the target row, checks the value the
-- caller believed was current, validates the change, applies it and appends the audit row, all
-- in ONE transaction. A change without its audit row cannot exist, and a refused or stale
-- request is logged too.
--
-- WHY THE ELIGIBILITY LIST IS NOW THE DB (owner decision 4, 2026-09-24, as corrected the same
-- day). The owner selects which bots actively bet from the page. The rows of coolbet_placer_bots
-- ARE that list: ui_place_enabled is the per-bot "real-money eligible" switch. The old hand-listed
-- PLACEABLE_BOTS = {two names} is gone. What stays in code is the RULE for which bots HAVE a
-- placement path at all (placement_gate.placement_path_reason), so a bot with no placer cannot be
-- "enabled" into nothing. Invariant I1 ("a default name is not a guard", 2026-08-28) is kept by:
-- one explicit row per bot, no default-ON (the trigger below refuses an INSERT that arrives
-- enabled), UPDATE-only from the page, server refusal for retired / locked / no-placement-path
-- bots, and an audit row for every change. Today's two bots stay exactly as they are (both OFF).
--
-- ARMING (owner decision 1). Arming real money is two steps on the page: type ARM REAL MONEY,
-- then give a written reason. No Telegram code. It never expires (decision 2). It is a separate
-- function so no per-bot control, and no generic control call, can reach real_money_armed = true.
--
-- ACCESS. Every object here is private: anon / authenticated / PUBLIC get nothing. The web calls
-- the two functions with the service key after its own superadmin (arming: owner) check.

BEGIN;

-- This migration seeds eligibility rows and a lock; the guards below admit that only with the
-- migration flag (transaction-local, gone at COMMIT).
SET LOCAL oddsintel.migration = 'on';

-- ── 1. per-bot lock + the eligibility list ────────────────────────────────────────────────────

ALTER TABLE coolbet_placer_bots ADD COLUMN IF NOT EXISTS locked_reason text;

COMMENT ON COLUMN coolbet_placer_bots.locked_reason IS
    'Pinned OFF by evidence. While set, admin_set_control refuses to switch the bot on and '
    'placement_gate never lets it stake. Cleared only by a migration (with the smoke test that '
    'pins the evidence), never from the page.';

-- Every bot that HAS a placement path gets an eligibility row, inserted OFF. "Has a placement
-- path" is the code rule placement_gate.placement_path_reason (owner decision 4 as corrected
-- 2026-09-24 — NOT a hand-listed set of names): picks in shadow_bets (the placers load
-- shadow_bets_unique by bot name, for any bot), pre-match (not the in-play family), priced at a
-- book a placer supports (Coolbet: UI placer + router; Unibet-Site: the router's Unibet arm), and
-- not a publish-only pre-registered test or its control. simulated_bets bots are not capable: the
-- only placer for that ledger (coolbet_placer.place_all_bets) is no longer a supported executor.
-- The rule is applied here to the exported bot_config; smoke CONTROL-PLACEMENT-PATH-RULE-AGREES
-- keeps this SQL, the Python rule and the page's copy in step.
-- ON CONFLICT DO NOTHING: today's two rows (both OFF) keep their state and notes, and a re-run
-- never clobbers a value the operator has since set.
INSERT INTO coolbet_placer_bots (bot_name, ui_place_enabled, note, updated_at)
SELECT c.bot_name, false, 'seeded OFF by migration 413 (has a placement path)', now()
  FROM bot_config c
  JOIN bots b ON b.name = c.bot_name
 WHERE b.is_active AND b.retired_at IS NULL
   AND c.ledger = 'shadow_bets'
   AND coalesce(c.family, '') NOT IN ('inplay', 'forward_test', 'control')
   AND c.books && ARRAY['Coolbet', 'Unibet-Site']::text[]
ON CONFLICT (bot_name) DO NOTHING;

-- The O/U model bot is pinned OFF by evidence (smoke OU-CALIBRATOR-DOMAIN-MISMATCH). Without the
-- lock, a page click would be the first thing to notice, after money could already move.
UPDATE coolbet_placer_bots
   SET locked_reason = 'OU-CALIBRATOR-DOMAIN-MISMATCH (2026-09-13): every pick it staked came from a '
                       'calibrator fitted on raw ensemble probs and applied to Pinnacle-shrunk probs. '
                       'n=32, -43.5% ROI, CLV -5.7% (t=-4.6). Re-enable only on positive post-fix CLV. '
                       'Needs a migration and a smoke-test change.'
 WHERE bot_name = 'bot_coolbet_ou_model_v1'
   AND locked_reason IS NULL;

-- ...and switching a row ON happens ONLY inside admin_set_control (typed bot name + reason +
-- audit row). The pre-413 route behind /admin/shadow-bots (/api/admin/coolbet-placer-bots) did a
-- bare UPDATE, and after this migration there are eleven seeded rows it could have flipped on with
-- one click and no reason. This trigger closes that at the table:
--   * OFF -> ON is refused unless the transaction-local flag `oddsintel.control_fn` that
--     admin_set_control sets around its own UPDATE is present;
--   * clearing or changing an existing `locked_reason` is refused unless `oddsintel.migration` is
--     set (a lock is lifted by a reviewed migration, never from a UI or a stray UPDATE);
--   * switching OFF and ADDING a lock stay open to every writer (the safe directions).
--
-- WHAT THESE FLAGS ARE (and are not). They are an ACCIDENT GUARD, not a security boundary. Any
-- session that can run arbitrary SQL as the table owner can `SET LOCAL oddsintel.control_fn = 'on'`
-- itself. What they do guarantee: the web's service_role reaches the DB only through PostgREST,
-- which exposes the public-schema functions and tables but not set_config(), so the web cannot
-- start money except through the audited functions; and no engine code path, old route or
-- hand-typed UPDATE can do it by accident. pg_trigger_depth()/current_user cannot tell the
-- function apart from the engine here (both run as oddsintel_owner, both at depth 1).
-- RELIABILITY_LEDGER §25 records this.
CREATE OR REPLACE FUNCTION public.coolbet_placer_bots_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
    v_fn  boolean := coalesce(current_setting('oddsintel.control_fn', true), '') = 'on';
    v_mig boolean := coalesce(current_setting('oddsintel.migration', true), '') = 'on';
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- A row is added only by a reviewed migration (which is how the eligibility list and its
        -- locks are seeded), and always OFF. This also closes "DELETE + re-INSERT without the lock".
        IF NOT v_mig THEN
            RAISE EXCEPTION 'coolbet_placer_bots: rows are added only by a reviewed migration '
                            '(SET LOCAL oddsintel.migration = ''on'')';
        END IF;
        IF NEW.ui_place_enabled AND NOT v_fn THEN
            RAISE EXCEPTION 'coolbet_placer_bots: a new eligibility row must be inserted OFF (%). '
                            'Switch it on from /admin/bots, which audits the change.', NEW.bot_name;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.ui_place_enabled AND NOT OLD.ui_place_enabled AND NOT v_fn THEN
        RAISE EXCEPTION 'coolbet_placer_bots: % may be switched ON only through admin_set_control '
                        '(/admin/bots: typed bot name + reason, audited)', NEW.bot_name;
    END IF;
    IF OLD.locked_reason IS NOT NULL AND NEW.locked_reason IS DISTINCT FROM OLD.locked_reason AND NOT v_mig THEN
        RAISE EXCEPTION 'coolbet_placer_bots: the lock on % is lifted only by a reviewed migration '
                        '(SET LOCAL oddsintel.migration = ''on'')', NEW.bot_name;
    END IF;
    RETURN NEW;
END;
$$;

-- Rows and the table itself are never removed: switching OFF is the way to stop a bot. (A DELETE
-- would drop the bot from the eligibility list — safe for money — but DELETE + re-INSERT was the
-- way around the lock and the audit, so it is refused outright; so is TRUNCATE.)
CREATE OR REPLACE FUNCTION public.control_tables_no_delete()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    RAISE EXCEPTION '%: % is refused — control rows are switched, never removed (migration 413)', TG_TABLE_NAME, TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS coolbet_placer_bots_on_only_via_fn ON coolbet_placer_bots;
DROP TRIGGER IF EXISTS coolbet_placer_bots_insert_off ON coolbet_placer_bots;
DROP TRIGGER IF EXISTS coolbet_placer_bots_guard ON coolbet_placer_bots;
CREATE TRIGGER coolbet_placer_bots_guard
    BEFORE INSERT OR UPDATE ON coolbet_placer_bots
    FOR EACH ROW EXECUTE FUNCTION public.coolbet_placer_bots_guard();
DROP TRIGGER IF EXISTS coolbet_placer_bots_no_delete ON coolbet_placer_bots;
CREATE TRIGGER coolbet_placer_bots_no_delete
    BEFORE DELETE ON coolbet_placer_bots
    FOR EACH ROW EXECUTE FUNCTION public.control_tables_no_delete();
DROP TRIGGER IF EXISTS coolbet_placer_bots_no_truncate ON coolbet_placer_bots;
CREATE TRIGGER coolbet_placer_bots_no_truncate
    BEFORE TRUNCATE ON coolbet_placer_bots
    FOR EACH STATEMENT EXECUTE FUNCTION public.control_tables_no_delete();

-- The two fleet START transitions get the same guard: arming (false -> true) and resuming
-- placement (true -> false) happen only inside admin_arm_real_money / admin_set_control. Every
-- STOP (pause, disarm) stays open to every writer — Telegram, the engine, an operator's psql —
-- because stopping is always safe and must never depend on the audit path.
CREATE OR REPLACE FUNCTION public.coolbet_session_state_start_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF coalesce(current_setting('oddsintel.control_fn', true), '') = 'on' THEN
        RETURN NEW;
    END IF;
    -- A re-pause over an existing pause never rewrites WHY it is paused (review 2026-09-24): a
    -- "daemon self-pause: …" or a Telegram /pause written over the strategic OWN-PATH-VERDICT stop
    -- turned it into a pause the engine auto-clears or the normal RESUME PLACEMENT phrase clears.
    -- The original reason and time are kept, whoever writes; the audited function never re-pauses
    -- (a pause over a pause is a no-op there).
    IF TG_OP = 'UPDATE' THEN
        IF coalesce(OLD.placement_paused, false) AND coalesce(NEW.placement_paused, false) THEN
            NEW.placement_paused_reason := OLD.placement_paused_reason;
            NEW.placement_paused_at     := OLD.placement_paused_at;
        END IF;
        IF coalesce(OLD.publishing_paused, false) AND coalesce(NEW.publishing_paused, false) THEN
            NEW.publishing_paused_reason := OLD.publishing_paused_reason;
            NEW.publishing_paused_at     := OLD.publishing_paused_at;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF coalesce(NEW.real_money_armed, false) OR NOT coalesce(NEW.placement_paused, false) THEN
            RAISE EXCEPTION 'coolbet_session_state: a new row must start paused and NOT armed '
                            '(arm / resume through /admin/bots)';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.real_money_armed AND NOT coalesce(OLD.real_money_armed, false) THEN
        RAISE EXCEPTION 'coolbet_session_state: real money is armed only through admin_arm_real_money '
                        '(/admin/bots, owner, typed ARM REAL MONEY + reason)';
    END IF;
    IF coalesce(OLD.placement_paused, false) AND NOT coalesce(NEW.placement_paused, false) THEN
        RAISE EXCEPTION 'coolbet_session_state: placement is resumed only through admin_set_control '
                        '(/admin/bots typed confirmation + reason, or the engine auto-clearing its own self-pause)';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS coolbet_session_state_start_guard ON coolbet_session_state;
CREATE TRIGGER coolbet_session_state_start_guard
    BEFORE INSERT OR UPDATE OF real_money_armed, placement_paused, placement_paused_reason, placement_paused_at,
                               publishing_paused, publishing_paused_reason, publishing_paused_at
    ON coolbet_session_state
    FOR EACH ROW EXECUTE FUNCTION public.coolbet_session_state_start_guard();
DROP TRIGGER IF EXISTS coolbet_session_state_no_delete ON coolbet_session_state;
CREATE TRIGGER coolbet_session_state_no_delete
    BEFORE DELETE ON coolbet_session_state
    FOR EACH ROW EXECUTE FUNCTION public.control_tables_no_delete();
DROP TRIGGER IF EXISTS coolbet_session_state_no_truncate ON coolbet_session_state;
CREATE TRIGGER coolbet_session_state_no_truncate
    BEFORE TRUNCATE ON coolbet_session_state
    FOR EACH STATEMENT EXECUTE FUNCTION public.control_tables_no_delete();

-- Belt and braces at the privilege level: no API role inserts, deletes or truncates control
-- rows. Checked before revoking: the web only UPDATEs these tables (the Telegram /pause fallback,
-- the legacy OFF-only placer toggle, the daemons-pause route); seeding is done by migrations as
-- the table owner, which a REVOKE does not touch.
REVOKE INSERT, DELETE, TRUNCATE ON coolbet_session_state FROM service_role, authenticated, anon;
REVOKE INSERT, DELETE, TRUNCATE ON coolbet_placer_bots FROM service_role, authenticated, anon;

COMMENT ON TABLE coolbet_placer_bots IS
    'Real-money ELIGIBILITY list (owner decision 4, migration 413). One row per bot that may be '
    'selected to bet; ui_place_enabled = the per-bot switch on /admin/bots. Effective allowlist = '
    'bots with a placement path (code rule placement_gate.placement_path_reason over bot_config) ∩ '
    'rows here WHERE ui_place_enabled AND '
    'locked_reason IS NULL AND the bot is not retired. Rows are added by migration (inserted OFF); '
    'the page only UPDATEs, through admin_set_control. Read errors fail CLOSED (empty set).';

-- ── 2. Mac placer heartbeat (owner decision 7) ────────────────────────────────────────────────
-- The page cannot see launchd. Each placer run writes a row here, so the page can show
-- Alive / Stale / Not reported next to the money switches and never "on" for a process that
-- is not running.

CREATE TABLE IF NOT EXISTS placer_heartbeats (
    placer             text PRIMARY KEY,          -- 'coolbet_ui_placer' | 'best_price_router'
    host               text,
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    execute_requested  boolean NOT NULL,          -- launched with --execute
    execute_effective  boolean NOT NULL,          -- the run gate let it stake this run
    refused_reason     text,                      -- why the run gate refused, if it did
    result             jsonb                      -- caps, counts: whatever the placer reports
);

COMMENT ON TABLE placer_heartbeats IS
    'Last run of each real-money placer on the Mac (migration 413). Written best-effort at every '
    'run; read by /admin/bots. A stale row means the process has not run, not that it is off.';

ALTER TABLE placer_heartbeats ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON placer_heartbeats FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON placer_heartbeats TO service_role;

-- ── 3. the audit log (append-only) ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS control_changes (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    actor         text NOT NULL,          -- email | 'telegram:<chat>' | 'engine:<module>' | 'migration:NNN'
    actor_user_id uuid,                   -- auth.users id when from the web
    source        text NOT NULL CHECK (source IN ('web','telegram','engine','cli','migration')),
    control       text NOT NULL CHECK (control IN (
                    'placement_paused','publishing_paused','daemons_paused','real_money_armed',
                    'placer_enabled','show_on_picks','retire','unretire','maturity_label','display_name')),
    bot_name      text,                   -- NULL for fleet controls; no FK (names outlive rows, I18)
    old_value     jsonb,
    new_value     jsonb,                  -- the value applied, or the value asked for when refused
    reason        text CHECK (reason IS NULL OR length(reason) <= 500),
    outcome       text NOT NULL CHECK (outcome IN ('applied','noop','refused','conflict')),
    refusal       text,
    request_id    uuid
);

CREATE INDEX IF NOT EXISTS control_changes_bot_idx     ON control_changes (bot_name, created_at DESC);
CREATE INDEX IF NOT EXISTS control_changes_control_idx ON control_changes (control, created_at DESC);

COMMENT ON TABLE control_changes IS
    'Append-only audit of every control change (migration 413): page, Telegram, engine setters and '
    'migrations. UPDATE / DELETE / TRUNCATE raise. Written by admin_set_control / '
    'admin_arm_real_money in the same transaction as the change.';

CREATE OR REPLACE FUNCTION public.control_changes_append_only()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    RAISE EXCEPTION 'control_changes is append-only (% refused)', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS control_changes_no_update ON control_changes;
CREATE TRIGGER control_changes_no_update
    BEFORE UPDATE OR DELETE ON control_changes
    FOR EACH ROW EXECUTE FUNCTION public.control_changes_append_only();

DROP TRIGGER IF EXISTS control_changes_no_truncate ON control_changes;
CREATE TRIGGER control_changes_no_truncate
    BEFORE TRUNCATE ON control_changes
    FOR EACH STATEMENT EXECUTE FUNCTION public.control_changes_append_only();

ALTER TABLE control_changes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON control_changes FROM PUBLIC, anon, authenticated;
REVOKE ALL ON control_changes FROM service_role;
GRANT SELECT ON control_changes TO service_role;   -- INSERT only through the functions
REVOKE ALL ON SEQUENCE control_changes_id_seq FROM PUBLIC, anon, authenticated;

-- ── 4. the single write function ──────────────────────────────────────────────────────────────
--
-- Verdicts (spec §1): the SAFE direction (pause, disarm, OFF, resume publishing) needs no
-- reason and no expected value, so it works even when the page could not read the state. The
-- START direction (resume placement, placer ON, /picks ON, and pausing the customer channel from
-- the page) needs a reason of >= 10 characters, a typed confirmation and the expected current
-- value. Arming is not possible here at all.

CREATE OR REPLACE FUNCTION public.admin_set_control(
    p_control       text,
    p_bot           text,
    p_value         jsonb,
    p_reason        text,
    p_confirm       text,
    p_actor         text,
    p_actor_user_id uuid,
    p_source        text,
    p_expected      jsonb,
    p_request_id    uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_reason   text := nullif(btrim(coalesce(p_reason, '')), '');
    v_confirm  text := btrim(coalesce(p_confirm, ''));
    v_on       boolean;
    v_strict   boolean := false;   -- the start / (b) direction
    v_old      jsonb;
    v_outcome  text;
    v_refusal  text;
    v_cur_why  text;
    v_state    coolbet_session_state%ROWTYPE;
    v_placer   coolbet_placer_bots%ROWTYPE;
    v_bot      bots%ROWTYPE;
    v_cfg      bot_config%ROWTYPE;
    v_row      control_changes%ROWTYPE;
    v_last     control_changes%ROWTYPE;
BEGIN
    IF p_source IS NULL OR p_source NOT IN ('web','telegram','engine','cli','migration') THEN
        RAISE EXCEPTION 'admin_set_control: bad source %', p_source;
    END IF;
    IF nullif(btrim(coalesce(p_actor, '')), '') IS NULL THEN
        RAISE EXCEPTION 'admin_set_control: actor required';
    END IF;
    IF p_control NOT IN ('placement_paused','publishing_paused','daemons_paused','real_money_armed',
                         'placer_enabled','show_on_picks','retire','unretire','maturity_label','display_name') THEN
        RAISE EXCEPTION 'admin_set_control: unknown control %', p_control;
    END IF;
    IF v_reason IS NOT NULL AND length(v_reason) > 500 THEN
        v_reason := left(v_reason, 500);
    END IF;

    <<decide>>
    BEGIN
        IF p_control IN ('retire','unretire','maturity_label','display_name') THEN
            v_outcome := 'refused';
            v_refusal := p_control || ' is not available yet (phase B of the control panel)';
            EXIT decide;
        END IF;
        IF p_value IS NULL OR jsonb_typeof(p_value) <> 'boolean' THEN
            v_outcome := 'refused';
            v_refusal := 'value must be true or false';
            EXIT decide;
        END IF;
        v_on := (p_value #>> '{}')::boolean;

        -- ── fleet switches (singleton row) ──
        IF p_control IN ('placement_paused','publishing_paused','daemons_paused','real_money_armed') THEN
            IF p_control = 'real_money_armed' AND v_on THEN
                v_outcome := 'refused';
                v_refusal := 'arming is not possible through admin_set_control; it is an owner action '
                             '(admin_arm_real_money: typed ARM REAL MONEY + written reason)';
                EXIT decide;
            END IF;

            SELECT * INTO v_state FROM coolbet_session_state WHERE id = 1 FOR UPDATE;
            IF NOT FOUND THEN
                v_outcome := CASE WHEN (p_control = 'real_money_armed' AND NOT v_on)
                                    OR (p_control IN ('placement_paused','daemons_paused') AND v_on)
                                  THEN 'noop' ELSE 'refused' END;
                v_refusal := 'coolbet_session_state row missing (readers already fail closed)';
                EXIT decide;
            END IF;

            v_old := CASE p_control
                WHEN 'placement_paused'  THEN to_jsonb(v_state.placement_paused)
                WHEN 'publishing_paused' THEN to_jsonb(v_state.publishing_paused)
                WHEN 'daemons_paused'    THEN to_jsonb(v_state.daemons_paused)
                WHEN 'real_money_armed'  THEN to_jsonb(v_state.real_money_armed) END;
            v_cur_why := CASE p_control
                WHEN 'placement_paused'  THEN v_state.placement_paused_reason
                WHEN 'publishing_paused' THEN v_state.publishing_paused_reason
                WHEN 'daemons_paused'    THEN v_state.daemons_paused_reason
                WHEN 'real_money_armed'  THEN v_state.real_money_armed_reason END;

            v_strict := (p_control = 'placement_paused' AND NOT v_on AND p_source <> 'engine')
                     OR (p_control = 'publishing_paused' AND v_on AND p_source = 'web');
        -- ── per-bot real-money eligibility ──
        ELSIF p_control = 'placer_enabled' THEN
            SELECT * INTO v_placer FROM coolbet_placer_bots WHERE bot_name = p_bot FOR UPDATE;
            IF NOT FOUND THEN
                v_outcome := CASE WHEN v_on THEN 'refused' ELSE 'noop' END;
                v_refusal := 'no coolbet_placer_bots row for ' || coalesce(p_bot, '(null)')
                          || ': eligibility rows are added by a reviewed migration (the page only updates)';
                EXIT decide;
            END IF;
            v_old := to_jsonb(v_placer.ui_place_enabled);
            v_strict := v_on;
        -- ── /picks visibility (model arm) ──
        ELSIF p_control = 'show_on_picks' THEN
            SELECT * INTO v_bot FROM bots WHERE name = p_bot FOR UPDATE;
            IF NOT FOUND THEN
                v_outcome := CASE WHEN v_on THEN 'refused' ELSE 'noop' END;
                v_refusal := 'no bots row for ' || coalesce(p_bot, '(null)');
                EXIT decide;
            END IF;
            v_old := to_jsonb(coalesce(v_bot.show_on_picks, false));
            v_strict := v_on;
        END IF;

        -- optimistic concurrency: the caller's view of the current value must still hold
        IF p_expected IS NOT NULL AND p_expected <> v_old THEN
            v_outcome := 'conflict';
            v_refusal := 'the value changed since the page read it';
            EXIT decide;
        END IF;
        IF v_old = p_value THEN
            v_outcome := 'noop';
            EXIT decide;
        END IF;

        IF v_strict THEN
            -- Pausing the customer channel is typed + reasoned, but it is still a STOP for the
            -- channel, so it stays available when the page could not read the state.
            IF p_expected IS NULL AND p_control <> 'publishing_paused' THEN
                v_outcome := 'refused';
                v_refusal := 'this change needs the expected current value (state must be readable to start anything)';
                EXIT decide;
            END IF;
            IF v_reason IS NULL OR length(v_reason) < 10 THEN
                v_outcome := 'refused';
                v_refusal := 'a written reason of at least 10 characters is required';
                EXIT decide;
            END IF;
        END IF;

        -- control-specific rules for the start direction
        IF p_control = 'placement_paused' AND NOT v_on AND p_source = 'engine' THEN
            -- The one non-page resume: the engine auto-clearing a pause IT set on itself (exact
            -- marker, as coolbet_state.is_daemon_self_pause). An operator or strategic pause is
            -- never auto-cleared.
            IF coalesce(v_cur_why, '') NOT LIKE 'daemon self-pause:%'
               OR coalesce(v_cur_why, '') ~* '(strategic|OWN-PATH-VERDICT)' THEN
                v_outcome := 'refused';
                v_refusal := 'the engine may only clear its own daemon self-pause; resume this pause on /admin/bots';
                EXIT decide;
            END IF;
        ELSIF p_control = 'placement_paused' AND NOT v_on THEN
            IF p_source <> 'web' THEN
                v_outcome := 'refused';
                v_refusal := 'placement is resumed only from /admin/bots (owner decision 3: Telegram is stop-only)';
                EXIT decide;
            END IF;
            IF coalesce(v_cur_why, '') ~* '(strategic|OWN-PATH-VERDICT)' THEN
                IF v_confirm <> 'RESUME STRATEGIC' THEN
                    v_outcome := 'refused';
                    v_refusal := 'the current pause is a strategic stop: type RESUME STRATEGIC to clear it (I5, migration 343)';
                    EXIT decide;
                END IF;
            ELSIF v_confirm <> 'RESUME PLACEMENT' THEN
                v_outcome := 'refused';
                v_refusal := 'type RESUME PLACEMENT to confirm';
                EXIT decide;
            END IF;
        ELSIF p_control = 'publishing_paused' AND v_on AND p_source = 'web' THEN
            IF v_confirm <> 'PAUSE PICKS' THEN
                v_outcome := 'refused';
                v_refusal := 'type PAUSE PICKS to confirm';
                EXIT decide;
            END IF;
        ELSIF p_control = 'placer_enabled' AND v_on THEN
            IF p_source <> 'web' THEN
                v_outcome := 'refused';
                v_refusal := 'real-money eligibility is switched on only from /admin/bots';
                EXIT decide;
            END IF;
            IF v_confirm <> p_bot THEN
                v_outcome := 'refused';
                v_refusal := 'type the bot name to confirm';
                EXIT decide;
            END IF;
            IF v_placer.locked_reason IS NOT NULL THEN
                v_outcome := 'refused';
                v_refusal := 'locked: ' || v_placer.locked_reason;
                EXIT decide;
            END IF;
            SELECT * INTO v_bot FROM bots WHERE name = p_bot;
            IF NOT FOUND OR NOT coalesce(v_bot.is_active, false) OR v_bot.retired_at IS NOT NULL THEN
                v_outcome := 'refused';
                v_refusal := 'the bot is retired or has no bots row';
                EXIT decide;
            END IF;
            SELECT * INTO v_cfg FROM bot_config WHERE bot_name = p_bot;
            IF NOT FOUND OR v_cfg.exported_at IS NULL OR v_cfg.exported_at < now() - interval '36 hours' THEN
                v_outcome := 'refused';
                v_refusal := 'no fresh bot_config row (> 36 h or missing): cannot confirm the placement path';
                EXIT decide;
            END IF;
            -- the same rule as placement_gate.placement_path_reason (smoke-pinned)
            IF coalesce(v_cfg.family, '') IN ('inplay', 'forward_test', 'control')
               OR coalesce(v_cfg.ledger, '') <> 'shadow_bets'
               OR NOT coalesce(v_cfg.books && ARRAY['Coolbet', 'Unibet-Site']::text[], false) THEN
                v_outcome := 'refused';
                v_refusal := 'the bot has no placement path (placers read pre-match shadow_bets picks priced at Coolbet or Unibet-Site)';
                EXIT decide;
            END IF;
        ELSIF p_control = 'show_on_picks' AND v_on THEN
            IF p_source <> 'web' THEN
                v_outcome := 'refused';
                v_refusal := 'show on /picks is switched on only from /admin/bots';
                EXIT decide;
            END IF;
            IF v_confirm <> p_bot THEN
                v_outcome := 'refused';
                v_refusal := 'type the bot name to confirm';
                EXIT decide;
            END IF;
            IF NOT coalesce(v_bot.is_active, false) OR v_bot.retired_at IS NOT NULL THEN
                v_outcome := 'refused';
                v_refusal := 'the bot is retired';
                EXIT decide;
            END IF;
            SELECT * INTO v_cfg FROM bot_config WHERE bot_name = p_bot;
            IF NOT FOUND OR v_cfg.exported_at IS NULL OR v_cfg.exported_at < now() - interval '36 hours' THEN
                v_outcome := 'refused';
                v_refusal := 'no fresh bot_config row (> 36 h or missing): cannot confirm the ledger';
                EXIT decide;
            END IF;
            IF coalesce(v_cfg.ledger, '') <> 'simulated_bets' OR coalesce(v_cfg.family, '') IN ('forward_test','control') THEN
                v_outcome := 'refused';
                v_refusal := 'only sim-ledger bots can be shown on /picks (shadow-ledger bots cannot reach customers by design, I13; '
                             'pre-registered bots publish by rule)';
                EXIT decide;
            END IF;
        END IF;

        -- ── apply ── (the table guards admit a START transition only with this transaction-local
        -- flag; it is cleared again straight after the UPDATE so it never covers anything else)
        PERFORM set_config('oddsintel.control_fn', 'on', true);
        IF p_control = 'placement_paused' THEN
            UPDATE coolbet_session_state
               SET placement_paused        = v_on,
                   placement_paused_at     = CASE WHEN v_on THEN now() END,
                   placement_paused_reason = CASE WHEN v_on THEN coalesce(v_reason, 'paused via ' || p_source || ' (' || p_actor || ')') END
             WHERE id = 1;
        ELSIF p_control = 'publishing_paused' THEN
            UPDATE coolbet_session_state
               SET publishing_paused        = v_on,
                   publishing_paused_at     = CASE WHEN v_on THEN now() END,
                   publishing_paused_reason = CASE WHEN v_on THEN coalesce(v_reason, 'paused via ' || p_source || ' (' || p_actor || ')') END
             WHERE id = 1;
        ELSIF p_control = 'daemons_paused' THEN
            UPDATE coolbet_session_state
               SET daemons_paused        = v_on,
                   daemons_paused_at     = CASE WHEN v_on THEN now() END,
                   daemons_paused_reason = CASE WHEN v_on THEN coalesce(v_reason, 'paused via ' || p_source || ' (' || p_actor || ')') END
             WHERE id = 1;
        ELSIF p_control = 'real_money_armed' THEN   -- v_on is false here: disarm only
            UPDATE coolbet_session_state
               SET real_money_armed        = false,
                   real_money_armed_at     = NULL,
                   real_money_armed_reason = coalesce(v_reason, 'disarmed via ' || p_source || ' (' || p_actor || ')')
             WHERE id = 1;
        ELSIF p_control = 'placer_enabled' THEN
            -- The note is rewritten with the change, so it can never again disagree with the flag
            -- the way migration 343 found it; the full history is in control_changes.
            UPDATE coolbet_placer_bots
               SET ui_place_enabled = v_on,
                   updated_at       = now(),
                   note             = left(CASE WHEN v_on THEN 'ON ' ELSE 'OFF ' END
                                           || to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC via '
                                           || p_source || ' (' || p_actor || ')'
                                           || coalesce(': ' || v_reason, ''), 1000)
             WHERE bot_name = p_bot;
        ELSIF p_control = 'show_on_picks' THEN
            UPDATE bots SET show_on_picks = v_on WHERE name = p_bot;
        END IF;
        PERFORM set_config('oddsintel.control_fn', '', true);
        v_outcome := 'applied';
    END decide;

    INSERT INTO control_changes (actor, actor_user_id, source, control, bot_name, old_value, new_value,
                                 reason, outcome, refusal, request_id)
    VALUES (left(btrim(p_actor), 200), p_actor_user_id, p_source, p_control, p_bot, v_old, p_value,
            v_reason, v_outcome, v_refusal, p_request_id)
    RETURNING * INTO v_row;

    IF v_outcome = 'conflict' THEN
        SELECT * INTO v_last FROM control_changes
         WHERE control = p_control AND bot_name IS NOT DISTINCT FROM p_bot
           AND outcome = 'applied' AND id <> v_row.id
         ORDER BY id DESC LIMIT 1;
    END IF;

    RETURN jsonb_build_object(
        'outcome', v_outcome,
        'refusal', v_refusal,
        'old',     v_old,
        'current', CASE WHEN v_outcome = 'applied' THEN p_value ELSE v_old END,
        'at',      v_row.created_at,
        'id',      v_row.id,
        'changed_by', v_last.actor,
        'changed_at', v_last.created_at);
END;
$$;

REVOKE ALL ON FUNCTION public.admin_set_control(text, text, jsonb, text, text, text, uuid, text, jsonb, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_set_control(text, text, jsonb, text, text, text, uuid, text, jsonb, uuid)
    TO service_role;

-- ── 5. arming: the one "start money" switch ───────────────────────────────────────────────────
-- Owner decision 1: two steps on the page (typed ARM REAL MONEY, then a written reason), no
-- Telegram code; decision 2: never expires. The web route checks the caller is the owner
-- (OWNER_USER_IDS) before calling this. The function re-checks everything the UI asked for.

CREATE OR REPLACE FUNCTION public.admin_arm_real_money(
    p_reason        text,
    p_confirm       text,
    p_actor         text,
    p_actor_user_id uuid,
    p_expected      jsonb,
    p_request_id    uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_reason  text := nullif(btrim(coalesce(p_reason, '')), '');
    v_old     jsonb;
    v_outcome text;
    v_refusal text;
    v_state   coolbet_session_state%ROWTYPE;
    v_row     control_changes%ROWTYPE;
BEGIN
    IF nullif(btrim(coalesce(p_actor, '')), '') IS NULL THEN
        RAISE EXCEPTION 'admin_arm_real_money: actor required';
    END IF;
    IF v_reason IS NOT NULL AND length(v_reason) > 500 THEN
        v_reason := left(v_reason, 500);
    END IF;

    <<decide>>
    BEGIN
        SELECT * INTO v_state FROM coolbet_session_state WHERE id = 1 FOR UPDATE;
        IF NOT FOUND THEN
            v_outcome := 'refused'; v_refusal := 'coolbet_session_state row missing';
            EXIT decide;
        END IF;
        v_old := to_jsonb(v_state.real_money_armed);
        IF p_expected IS NULL OR p_expected <> v_old THEN
            v_outcome := 'conflict'; v_refusal := 'the armed state changed since the page read it';
            EXIT decide;
        END IF;
        IF v_state.real_money_armed THEN
            v_outcome := 'noop';
            EXIT decide;
        END IF;
        IF btrim(coalesce(p_confirm, '')) <> 'ARM REAL MONEY' THEN
            v_outcome := 'refused'; v_refusal := 'type ARM REAL MONEY to confirm';
            EXIT decide;
        END IF;
        IF v_reason IS NULL OR length(v_reason) < 20 THEN
            v_outcome := 'refused'; v_refusal := 'arming needs a written reason of at least 20 characters';
            EXIT decide;
        END IF;
        PERFORM set_config('oddsintel.control_fn', 'on', true);
        UPDATE coolbet_session_state
           SET real_money_armed        = true,
               real_money_armed_at     = now(),
               real_money_armed_reason = v_reason
         WHERE id = 1;
        PERFORM set_config('oddsintel.control_fn', '', true);
        v_outcome := 'applied';
    END decide;

    INSERT INTO control_changes (actor, actor_user_id, source, control, bot_name, old_value, new_value,
                                 reason, outcome, refusal, request_id)
    VALUES (left(btrim(p_actor), 200), p_actor_user_id, 'web', 'real_money_armed', NULL, v_old,
            'true'::jsonb, v_reason, v_outcome, v_refusal, p_request_id)
    RETURNING * INTO v_row;

    RETURN jsonb_build_object(
        'outcome', v_outcome,
        'refusal', v_refusal,
        'old',     v_old,
        'current', CASE WHEN v_outcome = 'applied' THEN 'true'::jsonb ELSE v_old END,
        'at',      v_row.created_at,
        'id',      v_row.id);
END;
$$;

REVOKE ALL ON FUNCTION public.admin_arm_real_money(text, text, text, uuid, jsonb, uuid)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_arm_real_money(text, text, text, uuid, jsonb, uuid)
    TO service_role;

-- ── 6. baseline: the log starts from a known state ────────────────────────────────────────────

INSERT INTO control_changes (actor, source, control, bot_name, old_value, new_value, reason, outcome)
SELECT 'migration:413', 'migration', c.control, NULL, NULL, c.v,
       left('state at audit start' || coalesce(': ' || c.why, ''), 500), 'applied'
  FROM coolbet_session_state s
 CROSS JOIN LATERAL (VALUES
        ('placement_paused',  to_jsonb(s.placement_paused),  s.placement_paused_reason),
        ('real_money_armed',  to_jsonb(s.real_money_armed),  s.real_money_armed_reason),
        ('publishing_paused', to_jsonb(s.publishing_paused), s.publishing_paused_reason),
        ('daemons_paused',    to_jsonb(s.daemons_paused),    s.daemons_paused_reason)
       ) AS c(control, v, why)
 WHERE s.id = 1;

INSERT INTO control_changes (actor, source, control, bot_name, old_value, new_value, reason, outcome)
SELECT 'migration:413', 'migration', 'placer_enabled', p.bot_name, NULL, to_jsonb(p.ui_place_enabled),
       left('state at audit start' || coalesce(': ' || p.note, '')
            || coalesce(' [locked: ' || p.locked_reason || ']', ''), 500), 'applied'
  FROM coolbet_placer_bots p;

COMMIT;
