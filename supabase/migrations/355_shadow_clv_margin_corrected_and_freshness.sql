-- 355_shadow_clv_margin_corrected_and_freshness.sql
-- OWN Phase 1a + Phase 6 engine side (2026-09-15) — dev/active/own-implementation-plan.md
--
-- WHY (three things, one migration because the same view carries them all):
--
-- 1. `shadow_bets.clv` is a RAW price ratio (odds_at_pick / own-book close − 1)
--    whose break-even is the closing book's margin (~7–9pp), not zero. Every bot
--    verdict in the pre-registrations is on the MARGIN-CORRECTED number
--    `(1+clv)/(1+m) − 1`, but that number was computed in ad-hoc scripts and in
--    JavaScript on /admin/shadow-bots over the full ledger on every page load.
--    `real_bets` and `picks_forward_test` already store it per row; shadow_bets
--    — the ledger every bot is judged on — did not. Settlement now writes
--    `closing_margin` (the closing book's own overround on that fixture+market,
--    `settlement.closing_book_margin()`, NULL when any leg is missing — never an
--    average) and `clv_margin_corrected` at settle time.
--
-- 2. `decision_quote_age_min` — how old the book quote was when the instrument
--    decided. OWN-ANCHOR-GATE-VERIFICATION (2026-09-14) showed the sharp-tight
--    slope reads +1.31 on stale quotes and +0.35 when the decision quote is
--    ≤60 min old; nothing recorded the age, so the two could not be told apart
--    per pick. The trigger matcher now records it and REFUSES stale legs for
--    the instrument (SHARP-TIGHT-FRESHNESS-REFUSES-STALE).
--
-- 3. `shadow_bets_own_book_clv` — a cheap projection the admin page can read
--    instead of recomputing margins in JS over 14k rows (Phase 6).
--
-- `shadow_bets_unique` is re-created with the three new columns APPENDED
-- (CREATE OR REPLACE VIEW may only add trailing columns).
--
-- Re-appliable (RE-APPLIABLE-MIGRATIONS 2026-09-14).

ALTER TABLE shadow_bets
    ADD COLUMN IF NOT EXISTS closing_margin         NUMERIC,
    ADD COLUMN IF NOT EXISTS clv_margin_corrected   NUMERIC,
    ADD COLUMN IF NOT EXISTS decision_quote_age_min NUMERIC;

COMMENT ON COLUMN shadow_bets.closing_margin IS
    'Overround of the CLOSING BOOK''S OWN closing market on this fixture (SUM(1/close_i) − 1 '
    'over the full complement of selections). NULL when any leg is missing — never an average. '
    'settlement.closing_book_margin(). OWN Phase 1a, 2026-09-15.';
COMMENT ON COLUMN shadow_bets.clv_margin_corrected IS
    '(1 + clv) / (1 + closing_margin) − 1. The DECISION VARIABLE for every bot verdict: raw clv '
    'breaks even at the book''s margin, this breaks even at 0. NULL whenever clv or closing_margin is NULL.';
COMMENT ON COLUMN shadow_bets.decision_quote_age_min IS
    'Minutes between the book quote the pick was priced at and the moment the pick was made. '
    'Written by the trigger matcher; the sharp-tight instrument refuses legs > 60 min.';

-- ⚠️ RE-APPLIABILITY (fixed 2026-09-15 after the conformance verifier).
-- `CREATE OR REPLACE VIEW` cannot DROP columns, and migration 358 later widened
-- `shadow_bets_unique` with the three `inplay_*` columns — so re-running this
-- file verbatim failed with "cannot drop columns from view". The view build is
-- therefore SKIPPED when a later migration has already produced a superset.
-- 358 is the authoritative definition of this view; this block only bootstraps
-- it on a database that has never seen 358.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'shadow_bets_unique' AND column_name = 'inplay_minute') THEN
        RAISE NOTICE 'shadow_bets_unique already widened by migration 358 — skipping the 355 view build';
    ELSE
        EXECUTE $v$
CREATE OR REPLACE VIEW shadow_bets_unique AS
 SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection) sb.id,
    sb.shadow_run_id, sb.shadow_cohort, sb.bot_id, sb.match_id, sb.market, sb.selection,
    sb.odds_at_pick, sb.pick_time, sb.stake, sb.model_probability, sb.calibrated_prob,
    sb.edge_percent, sb.recommended_bookmaker, sb.kelly_fraction, sb.timing_cohort,
    sb.model_version, sb.closing_odds, sb.clv, sb.result, sb.pnl, sb.created_at,
    sb.meta_clv_score, sb.strategy_profile, sb.void_reason, sb.clv_pinnacle,
    sb.closing_bookmaker, sb.pair_gap_hours, sb.odds_at_pick_live, sb.clv_live,
    sb.clv_pinnacle_live,
    b.retired_at AS bot_retired_at, b.is_active AS bot_is_active, b.name AS bot_name,
    sb.closing_margin, sb.clv_margin_corrected, sb.decision_quote_age_min
   FROM shadow_bets sb
     LEFT JOIN bots b ON b.id = sb.bot_id
  ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time
        $v$;
    END IF;
END $$;

-- The page-facing projection: settled, own-book-closed rows only, with the
-- margin-corrected number already computed. Rows that came through the
-- retired arbitrary-book fallback (closing_bookmaker IS NULL) are excluded by
-- construction — they must never feed a verdict (SHADOW-CLV-NO-ARBITRARY-FALLBACK).
CREATE OR REPLACE VIEW shadow_bets_own_book_clv AS
 SELECT u.id, u.bot_id, u.bot_name, u.match_id, u.market, u.selection,
        u.pick_time, u.result, u.pnl, u.odds_at_pick, u.odds_at_pick_live,
        u.recommended_bookmaker, u.closing_bookmaker, u.closing_odds,
        u.clv, u.closing_margin, u.clv_margin_corrected, u.clv_pinnacle,
        u.edge_percent, u.calibrated_prob, u.decision_quote_age_min,
        (u.decision_quote_age_min IS NOT NULL AND u.decision_quote_age_min <= 60) AS decision_quote_fresh
   FROM shadow_bets_unique u
  WHERE u.result IN ('won', 'lost')
    AND u.closing_bookmaker IS NOT NULL;

COMMENT ON VIEW shadow_bets_own_book_clv IS
    'Settled shadow picks with an own-book close and the margin-corrected CLV precomputed. '
    'Read this on /admin/shadow-bots instead of recomputing margins in the browser. '
    'Break-even for clv_margin_corrected is 0; for clv it is the closing book''s margin.';
