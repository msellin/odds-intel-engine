-- 478 — [[#190]] remove the leg_clv_sharp rows left behind by migration 475 ([[#182]]: the owner deleted the
-- pre-2026-09-26 real_bets ledger — "a delete, not a filter"). 173 rows with ledger = 'real_bets' point at no bet;
-- they are DERIVED (workers/jobs/clv_sharp.py recomputes CLV for any real bet that exists) and nothing can read them
-- through a join. 475 should have removed them in the same step — this finishes it. Smoke CLV-SHARP-REAL-BETS was red.
SET lock_timeout = '5s';
DELETE FROM leg_clv_sharp l
 WHERE l.ledger = 'real_bets'
   AND NOT EXISTS (SELECT 1 FROM real_bets r WHERE r.id = l.leg_id);
-- verify: NOT EXISTS (SELECT 1 FROM leg_clv_sharp l WHERE l.ledger = 'real_bets' AND NOT EXISTS (SELECT 1 FROM real_bets r WHERE r.id = l.leg_id))
