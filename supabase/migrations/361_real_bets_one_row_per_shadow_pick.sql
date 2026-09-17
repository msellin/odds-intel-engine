-- 361_real_bets_one_row_per_shadow_pick.sql
-- 2026-09-17. Owner: "if we found duplicated or errored data, we should fix the
-- bug and also clear up the duplicates so next agent doesn't run on same false
-- queries and findings."
--
-- WHAT WAS AUDITED. `real_bets` had no uniqueness beyond its primary key. The
-- audit found 16 duplicate groups on (match_id, market, selection, stake) out of
-- 992 rows, but split by how far apart they were:
--
--     < 60s      3 groups   -- almost certainly a double-write
--     1-10 min   8 groups
--     > 10 min   5 groups   -- plausibly a genuine repeat bet
--
-- NOTHING IS DELETED HERE, deliberately. This is the operator's money ledger;
-- 13 of the 16 pairs are more than a minute apart and a repeat bet on the same
-- selection is a legitimate thing to do. Deleting a financial record on a
-- heuristic is not a fix, it is data loss. The 16 are filed for owner review.
--
-- WHAT IS ENFORCED. Exactly one real_bets row per shadow pick. That is the one
-- case where a duplicate is unambiguously a bug: the shadow-bots Place action
-- logs a specific pick, and logging the same pick twice double-counts exposure
-- on a page the operator uses to decide what to stake by hand. Measured before
-- writing this: ZERO duplicate shadow_bet_id groups exist today, so the index
-- creates cleanly and is a guard against recurrence rather than a cleanup.
--
-- Partial (WHERE NOT NULL) because rows from the automated placer legitimately
-- carry no shadow link.
--
-- Re-appliable.

CREATE UNIQUE INDEX IF NOT EXISTS real_bets_one_per_shadow_pick
    ON real_bets (shadow_bet_id)
    WHERE shadow_bet_id IS NOT NULL;

COMMENT ON INDEX real_bets_one_per_shadow_pick IS
    'One real_bets row per shadow pick. The Place action on /admin/shadow-bots must never '
    'log the same pick twice — that double-counts exposure on the page the operator uses to '
    'decide what to stake. Partial: automated-placer rows carry no shadow link. 2026-09-17.';
