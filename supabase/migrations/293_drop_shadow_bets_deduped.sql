-- SHADOW-VIEW-DUPLICATE-2026-09-02 — revert migration 290.
--
-- Migration 290 created `shadow_bets_deduped` to fix the shadow-bots page
-- truncation. The fix was right; the new view was not. `shadow_bets_unique`
-- already existed (migrations 282/283) with identical semantics — same
-- DISTINCT ON (bot_id, match_id, market, selection), same earliest-pick_time
-- rule. Verified before dropping: 0 rows differ between the two.
--
-- ANALYSIS_GOTCHAS #5 says plainly "use the shadow_bets_unique view" and ends
-- with "one definition beats two". Shipping a second one is the exact thing it
-- warns against, and it was avoidable by reading the gotcha first.
--
-- The pages now read shadow_bets_unique.

DROP VIEW IF EXISTS shadow_bets_deduped;

NOTIFY pgrst, 'reload schema';
