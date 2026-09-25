-- 459 — #162 W4 CLOSED (2026-09-25): the placement checks are unified, so the money-gate contract
-- goes 0 -> 1 together with the code constant coolbet_state.GATE_CONTRACT = 1 (same commit).
--
-- WHAT W4 DELIVERED (why the lock can lift): one per-bot placement floor for every executor
-- (placement_floor.pick_clears, W4.3) · the daily cap across every book + the shared run lock
-- (W4.2) · per-match exposure and the floor re-checked inside the last gate assert_may_place
-- (W4.2) · flat EUR 10 (W4.3a) · real-money supply by bot name (W4.4) · real_bets in three states
-- with one writer (W4.1, W4.5) · the retired second money paths deleted (W4.6) · the source-of-truth
-- doc rewritten (W4.7) · and the pre-lock money review's two fixes (uncertain clicks block a retry;
-- Unibet applies the whole rule at the live price). Reviewed: no blocker.
--
-- WHAT THIS DOES NOT DO: it arms nothing, resumes nothing and switches no bot on. Placement stays
-- PAUSED, real money stays NOT ARMED and all 11 per-bot switches stay OFF; each is still the owner's
-- typed, audited action on /admin/bots (and the executors' launchd jobs stay parked). It only stops
-- the DB refusing those actions. A Mac checkout older than this commit refuses to stake (contract
-- mismatch) until it is updated — fail closed.
--
-- psql applies this file without a wrapping transaction, so it carries its own: the guard
-- (migration 436) admits a raise only with the transaction-local migration flag.

BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL oddsintel.migration = 'on';
UPDATE coolbet_session_state SET money_gate_contract = 1 WHERE id = 1 AND money_gate_contract = 0;
COMMIT;
