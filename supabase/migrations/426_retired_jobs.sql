-- 426 — retired_jobs: scheduler jobs that were unregistered on purpose (2026-09-25, #139 admin Jobs).
--
-- Why: pipeline_job_latest (migration 417) shows each job's latest run over 35 days. A job that was
-- UNREGISTERED keeps its last failed run in that window, so /admin/ops (Jobs) and the Overview
-- attention inbox kept listing it as "failing" for up to 35 days — prune_anon_users was retired by
-- #146 on 2026-09-24 (auth.users is in Supabase, not the VPS DB) and still read as an urgent failure
-- the next morning. The scheduler's job ids and the names jobs log under are different vocabularies
-- (only ~70 of 140 logged names match an id), so "is it still registered?" cannot be derived; the
-- retirement is recorded explicitly instead. When you unregister a job, add a row here.
CREATE TABLE IF NOT EXISTS public.retired_jobs (
    job_name   text PRIMARY KEY,
    retired_at timestamptz NOT NULL DEFAULT now(),
    reason     text NOT NULL
);
REVOKE ALL ON public.retired_jobs FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.retired_jobs TO service_role;

INSERT INTO public.retired_jobs (job_name, retired_at, reason) VALUES
    ('prune_anon_users', '2026-09-24 20:27+00',
     '#146 SCHEDULER-DEAD-JOBS: unregistered — auth.users lives in Supabase, not the VPS Postgres; no anon users are created since PRODUCT-COLLAPSE')
ON CONFLICT (job_name) DO NOTHING;

CREATE OR REPLACE VIEW public.pipeline_job_latest AS
WITH r AS (
    SELECT job_name, status, started_at, error_message,
           max(started_at) FILTER (WHERE status = 'completed') OVER (PARTITION BY job_name) AS last_ok_at,
           row_number() OVER (PARTITION BY job_name ORDER BY started_at DESC)             AS rn
      FROM public.pipeline_runs
     WHERE started_at > now() - interval '35 days'
       AND job_name NOT IN ('hist_backfill', 'backfill_coaches', 'backfill_transfers')
       AND job_name NOT IN (SELECT job_name FROM public.retired_jobs)
), f AS (
    SELECT job_name,
           -- distinct minutes, not rows: some jobs write two rows per run (prune_anon_users)
           count(DISTINCT date_trunc('minute', started_at)) FILTER (WHERE status = 'failed' AND started_at > coalesce(last_ok_at, '-infinity')) AS fail_streak,
           min(started_at) FILTER (WHERE status = 'failed' AND started_at > coalesce(last_ok_at, '-infinity')) AS failing_since
      FROM r
     GROUP BY job_name
)
SELECT r.job_name, r.status, r.started_at, r.error_message, r.last_ok_at, f.fail_streak, f.failing_since
  FROM r JOIN f USING (job_name)
 WHERE r.rn = 1;
