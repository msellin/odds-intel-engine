-- 394 — THIN (3–4-book) CONSENSUS CLOSE, labelled and kept APART ([[#116]], 2026-09-24)
--
-- Migration 393 judges every settled leg against a >=5-book consensus close
-- (clv_cons). Legs whose fixture has only 3–4 non-Pinnacle books at the close get
-- cons_status = 'no_consensus' and no consensus CLV at all.
--
-- These columns hold the SAME measure against a 3–4-book consensus close
-- (workers/utils/anchor.py, min_thin_books=3; Pinnacle and the leg's own book
-- excluded), filled ONLY for legs WITHOUT a >=5-book close. So a leg carries at most
-- one of clv_cons / clv_cons_thin, and the two can never be averaged together by
-- accident. MEASUREMENT ONLY: never a staking input, never a gate, never published
-- pooled with clv_cons. Quality measured in ANALYSIS_GOTCHAS §74.

ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS p_close_cons_thin  numeric;
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS clv_cons_thin      numeric;   -- odds × p_close_cons_thin − 1
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS cons_thin_n_books  integer;   -- 3 or 4
ALTER TABLE leg_clv_sharp ADD COLUMN IF NOT EXISTS cons_thin_status   text;      -- 'ok_thin' | 'too_few_books' | NULL = not attempted / has a >=5-book close
