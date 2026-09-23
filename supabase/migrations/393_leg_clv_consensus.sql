-- 393 — CONSENSUS CLOSE beside the Pinnacle close on every settled leg ([[#113]], 2026-09-23)
--
-- leg_clv_sharp (386) judges legs against the de-vigged PINNACLE close. 36% of legs
-- (61,896 of 171k) have no fresh Pinnacle close — no_fresh_close, or BTTS, which
-- Pinnacle never quotes through our feed — so their CLV is NULL. #113 measured a
-- de-vigged consensus of >=5 books ties AF-Pinnacle on outcome log-loss (120 d,
-- 19,697 fixtures) and is a peer of Pinnacle as a closing-line predictor.
--
-- These columns hold the SAME measure against that consensus close, for EVERY leg
-- (including those with a Pinnacle close, so the two can be compared). clv_sharp keeps
-- its definition; nothing here overwrites it. Consensus = workers/utils/anchor.py:
-- >=5 books, Pinnacle and the leg's own book EXCLUDED, one complete set per book, each
-- within 60 min of kickoff, ratio-guarded, Shin-de-vigged mean.

ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS p_close_cons   numeric;
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS clv_cons       numeric;   -- odds × p_close_cons − 1
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS cons_n_books   integer;
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS cons_status    text;      -- 'ok' | 'no_consensus' | 'unsupported_market'
