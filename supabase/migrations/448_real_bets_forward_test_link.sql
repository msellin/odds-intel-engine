-- 448 — #162 W4.5 (2026-09-25): real_bets can link to the /picks forward-test pick it backed.
--
-- WHY. real_bets links to simulated_bets (simulated_bet_id) and shadow_bets (shadow_bet_id), but not
-- to picks_forward_test — so a bet placed by hand on a /picks pick could not be tied to it, and
-- "which public picks did we actually back, at what price" had no answer (audit D §7). This adds the
-- third link. Nullable: an account-sync ticket or a manual bet on anything has no pick.
--
-- The manual writer record_manual_real_bet (migration 407) gains p_forward_test_pick_id. Its
-- eleven-argument form is DROPPED, not overloaded: two overloads with defaults make a named-argument
-- PostgREST call ambiguous. Every existing caller passes named arguments without the new one, which
-- still resolves (it defaults to NULL). Body otherwise unchanged: same advisory lock, same same-day
-- check, placed_real NULL (= unverified until the account reconcile confirms it; migration 440).

SET lock_timeout = '3s';

ALTER TABLE real_bets
    ADD COLUMN IF NOT EXISTS forward_test_pick_id uuid
        REFERENCES picks_forward_test(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS real_bets_forward_test_pick_idx
    ON real_bets (forward_test_pick_id) WHERE forward_test_pick_id IS NOT NULL;

DROP FUNCTION IF EXISTS public.record_manual_real_bet(uuid, text, text, text, numeric, numeric,
    numeric, text, uuid, uuid, uuid);

CREATE FUNCTION public.record_manual_real_bet(
    p_match_id uuid, p_market text, p_selection text, p_bookmaker text,
    p_actual_odds numeric, p_stake numeric,
    p_captured_odds numeric DEFAULT NULL, p_notes text DEFAULT NULL,
    p_bot_id uuid DEFAULT NULL, p_simulated_bet_id uuid DEFAULT NULL,
    p_shadow_bet_id uuid DEFAULT NULL, p_forward_test_pick_id uuid DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_sel text := lower(p_selection);
    v_existing uuid;
    v_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('real_bet|' || p_match_id::text || '|' || p_market || '|' || v_sel));
    SELECT id INTO v_existing
      FROM real_bets
     WHERE match_id = p_match_id AND market = p_market AND selection = v_sel
       AND placed_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
     LIMIT 1;
    IF v_existing IS NOT NULL THEN
        RETURN jsonb_build_object('error', 'already_placed', 'existing_id', v_existing);
    END IF;
    INSERT INTO real_bets (simulated_bet_id, shadow_bet_id, forward_test_pick_id, bot_id, match_id,
                           market, selection, bookmaker, captured_odds, actual_odds, stake, notes,
                           placed_real)
    VALUES (p_simulated_bet_id, p_shadow_bet_id, p_forward_test_pick_id, p_bot_id, p_match_id,
            p_market, v_sel, p_bookmaker, p_captured_odds, p_actual_odds, p_stake, p_notes, NULL)
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('id', v_id);
END;
$$;

REVOKE ALL ON FUNCTION public.record_manual_real_bet(uuid, text, text, text, numeric, numeric,
    numeric, text, uuid, uuid, uuid, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_manual_real_bet(uuid, text, text, text, numeric, numeric,
    numeric, text, uuid, uuid, uuid, uuid) TO service_role;
