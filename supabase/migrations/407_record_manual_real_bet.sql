-- 407 — #022 (a) (2026-09-24): an ATOMIC "log a bet I placed by hand".
--
-- WHY. /api/admin/real-bet de-duplicated with SELECT-then-INSERT across two PostgREST
-- calls, so a double-click, or a race with the placer, could insert the same
-- (match, market, selection) twice on a day — 5 historical duplicate groups exist.
--
-- WHY NOT A UNIQUE INDEX. The row asked for a partial unique index, which needs the 5
-- duplicate groups DELETED first — those are real-money records and deleting them is the
-- owner's call, not a migration's. An index would also constrain the placer and the
-- account reconciler, which can legitimately write more than one row for a selection.
-- This function serialises only the MANUAL path: a transaction-scoped advisory lock keyed
-- on (match, market, selection), the same-day check, and the insert — one transaction.
--
-- Called by the web route with the service key; not executable by anon/PUBLIC
-- (default privileges since migration 405).
CREATE OR REPLACE FUNCTION public.record_manual_real_bet(
    p_match_id uuid, p_market text, p_selection text, p_bookmaker text,
    p_actual_odds numeric, p_stake numeric,
    p_captured_odds numeric DEFAULT NULL, p_notes text DEFAULT NULL,
    p_bot_id uuid DEFAULT NULL, p_simulated_bet_id uuid DEFAULT NULL,
    p_shadow_bet_id uuid DEFAULT NULL)
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
    INSERT INTO real_bets (simulated_bet_id, shadow_bet_id, bot_id, match_id, market, selection,
                           bookmaker, captured_odds, actual_odds, stake, notes, placed_real)
    VALUES (p_simulated_bet_id, p_shadow_bet_id, p_bot_id, p_match_id, p_market, v_sel,
            p_bookmaker, p_captured_odds, p_actual_odds, p_stake, p_notes, NULL)
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('id', v_id);
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_manual_real_bet(uuid, text, text, text, numeric, numeric,
    numeric, text, uuid, uuid, uuid) TO service_role;
