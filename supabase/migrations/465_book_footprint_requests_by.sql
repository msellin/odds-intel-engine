-- 465 — [[#142]] REQUESTS-BY-CALLER: who spent a book's hourly request budget.
--
-- WHY. Coolbet sat at its 500/h budget in every hour of 2026-09-25/26 and the must-run callers
-- (near_kickoff_capture = the closing price, health_ping) were refused 18-184 times an hour, while
-- the #151 priority reserve was supposed to keep 20% for them. book_footprint only held totals, so
-- nothing said which caller used the budget and the reserve could not be tuned. Each process now
-- attributes every counted request to the scheduler job on that thread (footprint.set_caller from
-- scheduler._run_job) or else its script name, and merges {caller: n} here at flush.
SET lock_timeout = '3s';
ALTER TABLE public.book_footprint ADD COLUMN IF NOT EXISTS requests_by jsonb;
COMMENT ON COLUMN public.book_footprint.requests_by IS
  '[[#142]] {caller: requests} for the hour — scheduler job name, else process script name.';
