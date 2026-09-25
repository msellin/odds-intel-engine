-- 456 — candidate_funnel_7d: the first READER of candidate_funnel (#162 W7.5, audit B-R11).
--
-- candidate_funnel (migration 384, [[#082]]) records every near-floor or gate-rejected
-- candidate with the step that decided it, and until now nothing read it. This view is the
-- aggregate behind the "Why not picked (last 7 days)" panel on the /admin/bots bot sheet:
-- per bot, per source, per step — how many candidate rows and how many distinct matches.
--
-- `source` is kept in the grain on purpose: 'pipeline' (the live run) and 'pipeline_shadow'
-- (the shadow run over ALL bots) must never be pooled (ANALYSIS_GOTCHAS #72 rule 4), and a
-- publisher or ou_sharp row measures a different edge from a pipeline row.
--
-- PRIVATE: admin-only, read server-side with the service-role client. NO anon grant — the
-- anon role reads only what the public site reads (migration 404, #072).
SET lock_timeout = '3s';

CREATE OR REPLACE VIEW public.candidate_funnel_7d AS
SELECT bot,
       source,
       step,
       count(*)::int                 AS n,
       count(DISTINCT match_id)::int AS n_matches,
       max(last_seen)                AS last_seen
  FROM public.candidate_funnel
 WHERE day >= (now() AT TIME ZONE 'UTC')::date - 6
 GROUP BY bot, source, step;

COMMENT ON VIEW public.candidate_funnel_7d IS
  '#162 W7.5: candidate_funnel rows per (bot, source, step) over the last 7 UTC days, incl. today. Admin-only (service_role); never granted to anon.';

REVOKE ALL ON public.candidate_funnel_7d FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.candidate_funnel_7d TO service_role;
