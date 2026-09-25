-- 440_real_bets_placed_real_backfill.sql — #162 W4.1 (owner decision 2C, 2026-09-25): label the 849 real_bets
-- rows that carried no paper/real state (placed_real IS NULL).
--
-- WHY. placed_real is the table's three-state: TRUE = a confirmed real stake, FALSE = paper (never placed),
-- NULL = unverified. 849 rows (€4,925 of stake) sat at NULL, so /admin/real-bets, the Overview, the per-pick
-- "Bet made" column and the exposure/cap reads (placed_real IS NOT FALSE) counted every one as real — €3.1k
-- of it provably paper. Placement is NOT affected either way: all 849 are settled bets on past matches.
--
-- CLASSIFICATION (evidence, #162 money review 2026-09-25):
--   PAPER (→ FALSE), 464 rows — code paths that never POSTed a bet:
--     * 'auto ticket=None…' from 2026-05-23 08:00 UTC (379): DUPE-FIX-2 (acfa21a2, 07:15) stopped every
--       ticket=None write and paper writes only came back with 0fc822bd (07:46) — rows 07:17–07:23 come from
--       the older code that still recorded FAILED execute attempts during a live run, so they stay
--       unverified (evidence review 2026-09-25: 29 rows, €168; cutoff 07:15 → 08:00);
--     * 'inplay-auto…' (55) and 'auto-combo…' (30): those executors never placed (every revision checked).
--   REAL (→ TRUE), 31 rows — ticket evidence in the row itself:
--     * 'auto ticket=<digits>…' (23): the ticket prefix is the placed_at hour, same format as the trial;
--     * the 2026-05-20 first-execute trial, the four manual Coolbet UI tickets #473-476 and the two
--       coolbet-account-sync tickets (all self-verified from the account), and the Unibet placer test (€10).
--   UNVERIFIED (stay NULL), 354 rows — could be either; to be checked against the May Coolbet statement:
--     * 'auto ticket=None…' before 2026-05-23 08:00 (83: a failed execute POST still wrote such a row then);
--     * no note, 2026-06-09 … 06-12 (10): the web /admin/place modal logged manual real bets with notes NULL
--       when its box was left empty (evidence review) — NOT provably paper;
--     * no note, 2026-05-11 … 05-23 (259: SELF-USE-VALIDATION Phase 2, when this table held manual real bets);
--     * the 2 'manual via shadow-bots' bets of 2026-09-15 still awaiting account reconcile (the admin to-do).
--
-- SAFETY. Every UPDATE is guarded by its own expected count; any drift aborts the whole migration. Only
-- placed_real changes (plus an audit line in notes); no amount, result or pnl is touched.

BEGIN;

DO $$
DECLARE
    n integer;
BEGIN
    -- PAPER: post-DUPE-FIX-2 ticket=None
    UPDATE real_bets SET placed_real = false,
           notes = notes || ' | #162 W4.1 2026-09-25: labelled PAPER (ticket=None after DUPE-FIX-2)'
     WHERE placed_real IS NULL AND notes LIKE 'auto ticket=None%' AND placed_at >= '2026-05-23 08:00+00';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 379 THEN RAISE EXCEPTION 'W4.1: expected 379 post-DUPE-FIX-2 ticket=None rows, got %', n; END IF;

    UPDATE real_bets SET placed_real = false,
           notes = notes || ' | #162 W4.1 2026-09-25: labelled PAPER (in-play executor never placed)'
     WHERE placed_real IS NULL AND notes LIKE 'inplay-auto%';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 55 THEN RAISE EXCEPTION 'W4.1: expected 55 inplay-auto rows, got %', n; END IF;

    UPDATE real_bets SET placed_real = false,
           notes = notes || ' | #162 W4.1 2026-09-25: labelled PAPER (combo executor never placed)'
     WHERE placed_real IS NULL AND notes LIKE 'auto-combo%';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 30 THEN RAISE EXCEPTION 'W4.1: expected 30 auto-combo rows, got %', n; END IF;

    -- REAL: ticket evidence
    UPDATE real_bets SET placed_real = true,
           notes = notes || ' | #162 W4.1 2026-09-25: labelled REAL (Coolbet ticket id)'
     WHERE placed_real IS NULL AND notes ~ '^auto ticket=[0-9]';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 23 THEN RAISE EXCEPTION 'W4.1: expected 23 ticketed auto rows, got %', n; END IF;

    UPDATE real_bets SET placed_real = true,
           notes = notes || ' | #162 W4.1 2026-09-25: labelled REAL (ticket / self-verified from the account)'
     WHERE placed_real IS NULL
       AND (notes LIKE 'REAL MONEY first-execute trial%'
            OR notes LIKE 'manual via Coolbet UI ticket #%'
            OR notes LIKE 'coolbet-account-sync ticket #%'
            OR notes LIKE 'UNIBET-UI-PLACER test%');
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 8 THEN RAISE EXCEPTION 'W4.1: expected 8 ticketed manual/trial rows, got %', n; END IF;

    -- what is left NULL before 2026-09-15 must be exactly the 313 historical unverified rows (the 2 manual bets
    -- of 09-15 are excluded: account reconcile may confirm them any time; newer NULL rows — manual Place, an
    -- uncertain router placement — are live state, not history) — review 2026-09-25.
    SELECT count(*) INTO n FROM real_bets WHERE placed_real IS NULL AND placed_at < '2026-09-15';
    IF n <> 352 THEN RAISE EXCEPTION 'W4.1: expected 352 pre-09-15 rows left unverified, got %', n; END IF;
END;
$$;

COMMIT;
