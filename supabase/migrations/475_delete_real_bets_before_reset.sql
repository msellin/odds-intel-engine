-- 475 — [[#182]] delete the real_bets ledger rows from before the 2026-09-26 restart. Owner, 2026-09-26:
-- "we really don't need the old rows … I haven't made any real bets for days, it seems weird to keep them"
-- and "just do it". 992 rows (174 account-confirmed, €6,355 staked, none pending), May–Sep 2026. Nothing
-- reads them any more: no limit or exposure check (all settled), and the analyses built on them are written
-- up (SELF-USE-VALIDATION, DIRECT-BOOK-CLV). Copy kept OUTSIDE git (public repo; these are the owner's bets):
-- dev/archive/real_bets_before_2026-09-26.csv on the operator's Mac + the nightly DB dumps (90 days).
-- The two tables that point at real_bets are unlinked first (both columns nullable, FK NO ACTION):
-- coolbet_placement_attempts (143 rows keep their own record of each attempt), promo_ledger (0 rows).
SET lock_timeout = '5s';
UPDATE coolbet_placement_attempts SET real_bet_id = NULL
 WHERE real_bet_id IN (SELECT id FROM real_bets WHERE placed_at < '2026-09-26 00:00:00+00');
UPDATE promo_ledger SET real_bet_id = NULL
 WHERE real_bet_id IN (SELECT id FROM real_bets WHERE placed_at < '2026-09-26 00:00:00+00');
DELETE FROM real_bets WHERE placed_at < '2026-09-26 00:00:00+00';
-- verify: NOT EXISTS (SELECT 1 FROM real_bets WHERE placed_at < '2026-09-26 00:00:00+00')
