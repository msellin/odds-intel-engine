-- 436_money_gate_ready.sql — #162 W0.2 (2026-09-25): real-money switches stay LOCKED until the
-- placement-gate refactor (#162 W4) is complete.
--
-- WHY. The #162 audits found that a bot switched ON today would stake a different, looser strategy
-- than the one it is scored on: per-bot edge floors cover 2 of 11 capable bots (the other 9 fall back
-- to 3%), the UI placer and the best-price router apply different floors, the router's daily cap counts
-- Coolbet only (dev/active/bot-refactor-audit/D-surfaces-money.md §0). The plan says "finish W4 before
-- any switch is ON" — this makes that a database refusal instead of a sentence. It ONLY tightens:
-- all 11 switches are OFF and placement is paused (baseline 2026-09-25).
--
-- HOW. coolbet_session_state.money_gate_contract (int, default 0) = the version of the placement-check
-- contract the DATABASE has signed off. 0 = W4 not done. A trigger refuses each START transition — a
-- bot's ui_place_enabled false→true, and real_money_armed false→true — while it is 0, whoever writes
-- (the audited functions included: their UPDATE fires the trigger), on INSERT as well as UPDATE.
-- Every STOP stays open, exactly like migration 413's start guards. The contract is raised only inside
-- a reviewed migration (SET LOCAL oddsintel.migration = 'on') — the one that closes W4 sets 1.
-- The engine gate (placement_gate.assert_run_may_place) refuses unless the DB contract is >= 1 AND
-- equals the code's GATE_CONTRACT, so placer code that predates W4 (e.g. a stale checkout on the Mac)
-- cannot stake even after the DB is raised (review 2026-09-25: a plain boolean would let it through).
--
-- Separate trigger (not an edit of 413's guard functions or admin_set_control) so the audited function
-- bodies stay untouched; the refusal surfaces as the trigger's message on /admin/bots.
--
-- KNOWN LIMIT (same as 413): the engine connects as the table owner, which can SET LOCAL
-- oddsintel.migration or disable triggers. The lock stops accidents and every normal path, not a
-- deliberate owner-role bypass (RELIABILITY_LEDGER §25).

-- the kill-switch table is read every few seconds: never queue behind this for long (review 2026-09-25)
SET lock_timeout = '3s';

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS money_gate_contract integer NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION public.money_gate_ready_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
    v_contract     integer;
    v_was_on       boolean;
    v_old_contract integer;
BEGIN
    IF TG_TABLE_NAME = 'coolbet_placer_bots' THEN
        IF TG_OP = 'INSERT' THEN
            v_was_on := false;
        ELSE
            v_was_on := coalesce(OLD.ui_place_enabled, false);
        END IF;
        IF coalesce(NEW.ui_place_enabled, false) AND NOT v_was_on THEN
            SELECT money_gate_contract INTO v_contract FROM coolbet_session_state WHERE id = 1;
            IF coalesce(v_contract, 0) < 1 THEN
                RAISE EXCEPTION 'Real-money switches are locked until the placement checks are unified '
                                '(#162 W4: one per-bot floor, one daily cap across books). % stays OFF.', NEW.bot_name;
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    -- coolbet_session_state
    -- (plain variables, not CASE inside IF: PL/pgSQL would read CASE's THEN as the IF's THEN)
    IF TG_OP = 'INSERT' THEN
        v_was_on := false;
        v_old_contract := 0;
    ELSE
        v_was_on := coalesce(OLD.real_money_armed, false);
        v_old_contract := coalesce(OLD.money_gate_contract, 0);
    END IF;
    IF coalesce(NEW.real_money_armed, false) AND NOT v_was_on AND coalesce(NEW.money_gate_contract, 0) < 1 THEN
        RAISE EXCEPTION 'Real money cannot be armed until the placement checks are unified (#162 W4).';
    END IF;
    IF coalesce(NEW.money_gate_contract, 0) > v_old_contract
       AND coalesce(current_setting('oddsintel.migration', true), '') <> 'on' THEN
        RAISE EXCEPTION 'coolbet_session_state: money_gate_contract is raised only by the reviewed migration '
                        'that closes #162 W4 (SET LOCAL oddsintel.migration = ''on'')';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS coolbet_placer_bots_money_gate ON coolbet_placer_bots;
CREATE TRIGGER coolbet_placer_bots_money_gate
    BEFORE INSERT OR UPDATE OF ui_place_enabled ON coolbet_placer_bots
    FOR EACH ROW EXECUTE FUNCTION public.money_gate_ready_guard();

DROP TRIGGER IF EXISTS coolbet_session_state_money_gate ON coolbet_session_state;
CREATE TRIGGER coolbet_session_state_money_gate
    BEFORE INSERT OR UPDATE OF real_money_armed, money_gate_contract ON coolbet_session_state
    FOR EACH ROW EXECUTE FUNCTION public.money_gate_ready_guard();

REVOKE ALL ON FUNCTION public.money_gate_ready_guard() FROM PUBLIC, anon, authenticated;

RESET lock_timeout;
