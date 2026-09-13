-- EDGE-PERCENT-UNIT-FIX (2026-09-13) — the three paper shadow bots stored
-- `edge_percent` 100x too large. Backfill their historical rows.
--
-- WHY. `shadow_bets.edge_percent` holds a FRACTION by contract: every reader in
-- the codebase multiplies by 100 to display it (coolbet_signaler.py:219,
-- coolbet_placer.py:324/2010/2321/2600, coolbet_prekickoff_alert.py:195,
-- email_digest.py:161). A normal bot's median is 0.041-0.127 (4-13%).
--
-- These three wrote `round(edge * 100.0, 4)` instead, giving medians of
-- 1.65-2.83 and maxima up to 96.5 — i.e. an apparent "9,652% edge":
--
--   bot                              median   max     >= 0.20   n
--   bot_corners_paper_shadow_v1       2.834   30.66   366/381
--   bot_team_total_paper_shadow_v1    2.626   96.53   287/301
--   bot_1h_1x2_paper_shadow_v1        1.650   71.56   156/171
--
-- CONSEQUENCE. The live generation gate is NOT affected — it compares the raw
-- `edge` to EDGE_FLOOR before the x100 — so no wrong pick was ever made. What
-- broke is every downstream analysis: an `edge >= 13%` filter means
-- `edge >= 0.13`, which on these bots retained 97% of picks instead of ~8%. The
-- 2026-09-13 verdict sweep therefore reported "no floor helps" for these three
-- on floors that were never actually applied. Re-measured after this fix, still
-- no floor reaches |t| >= 2 — but on team totals and corners ROI now falls
-- MONOTONICALLY as the edge floor rises, which is the signature of a claimed
-- edge that is anti-predictive, and was invisible while the units were wrong.
--
-- Code fixed in the same commit: workers/jobs/{corners,team_total,
-- first_half_1x2}_paper_bot.py now store the fraction.
--
-- IDEMPOTENCE. Every pre-cutoff row is divided, including the small ones —
-- corners' minimum stored value is 0.0000 and its 25th percentile 1.302, so a
-- "only divide the big ones" guard would silently leave the low tail 100x
-- wrong. Instead the update is pinned to rows created before the cutoff (the
-- code fix ships in the same commit, so later rows are already correct), and
-- gated on a DO-block check of the MAX over that same fixed row set: while the
-- data is still in percentage points that max is 96.53, and once corrected it
-- is 0.97, so the guard can never fire twice. Guard and update read the same
-- rows, which is what makes this stable rather than merely unlikely to re-run.

DO $$
DECLARE
    pre_cutoff_max numeric;
    rows_fixed     integer;
BEGIN
    SELECT MAX(sb.edge_percent) INTO pre_cutoff_max
      FROM shadow_bets sb JOIN bots b ON b.id = sb.bot_id
     WHERE b.name IN ('bot_corners_paper_shadow_v1',
                      'bot_team_total_paper_shadow_v1',
                      'bot_1h_1x2_paper_shadow_v1')
       AND sb.created_at < TIMESTAMPTZ '2026-09-14 00:00:00+00';

    IF pre_cutoff_max IS NULL OR pre_cutoff_max <= 1.5 THEN
        RAISE NOTICE 'EDGE-PERCENT-UNIT-FIX: already in fraction units (max=%), nothing to do',
                     pre_cutoff_max;
        RETURN;
    END IF;

    UPDATE shadow_bets sb
       SET edge_percent = sb.edge_percent / 100.0
      FROM bots b
     WHERE b.id = sb.bot_id
       AND b.name IN ('bot_corners_paper_shadow_v1',
                      'bot_team_total_paper_shadow_v1',
                      'bot_1h_1x2_paper_shadow_v1')
       AND sb.edge_percent IS NOT NULL
       AND sb.created_at < TIMESTAMPTZ '2026-09-14 00:00:00+00';

    GET DIAGNOSTICS rows_fixed = ROW_COUNT;
    RAISE NOTICE 'EDGE-PERCENT-UNIT-FIX: divided % rows by 100 (was max=%)',
                 rows_fixed, pre_cutoff_max;
END $$;

COMMENT ON COLUMN shadow_bets.edge_percent IS
    'Model/sharp edge as a FRACTION (0.127 = 12.7%), never percentage points. Readers multiply by 100 to display. EDGE-PERCENT-UNIT-FIX 2026-09-13: the three paper shadow bots stored this x100 until that date; migration 334 backfilled them.';
