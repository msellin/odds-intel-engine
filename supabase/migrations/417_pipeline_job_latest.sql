-- 417 — pipeline_job_latest: the latest run of every scheduled job, with its failure streak
-- (#139 admin Overview attention inbox, 2026-09-24).
--
-- Why: the web helper getLatestJobStatuses() takes the newest 300 pipeline_runs rows and keeps the
-- newest per job. In production that is ~2 hours of history covering ~50 of ~230 job names, so a
-- nightly/weekly/monthly job that fails is never seen (league_draw_rate, line_velocity and
-- league_season_phase had failed every night since 09-07 and the page showed 0 failures).
--
-- Window: 35 days — long enough for monthly jobs (aln_auto_tune), short enough that jobs no longer
-- scheduled (cs2_*, wc_odds_sweep: last ran June/July) drop out on their own. fail_streak and
-- failing_since count FAILED runs since the last COMPLETED one (last_ok_at NULL = no success inside the
-- window, so failing_since is only the window edge — a floor), so an outage reads as "failing
-- since 09-07 · 17 runs", not "20 h".
--
-- Admin-only: no anon grant (migration 404 — new relations are private by default); read with the
-- service role by the web admin.
CREATE OR REPLACE VIEW public.pipeline_job_latest AS
WITH r AS (
    SELECT job_name, status, started_at, error_message,
           max(started_at) FILTER (WHERE status = 'completed') OVER (PARTITION BY job_name) AS last_ok_at,
           row_number() OVER (PARTITION BY job_name ORDER BY started_at DESC)             AS rn
      FROM public.pipeline_runs
     WHERE started_at > now() - interval '35 days'
       AND job_name NOT IN ('hist_backfill', 'backfill_coaches', 'backfill_transfers')
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

REVOKE ALL ON public.pipeline_job_latest FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.pipeline_job_latest TO service_role;
