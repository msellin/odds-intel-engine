-- 466 — [[#153]] ADMIN-MODELS-PAGE: forward accuracy of every production probability source, and each
-- bot's rule / model history. Both PRIVATE (service_role only) — read by /admin/models server-side.
--
-- model_accuracy: written daily by workers/jobs/model_accuracy.py (job `model_accuracy`, 02:40 UTC).
--   One row per (model, market, window) over settled kickoffs, only probabilities written BEFORE kickoff:
--   n, mean log-loss, Brier, the window's base-rate log-loss ("guessing"), and Pinnacle's de-vigged close
--   scored on the SAME rows (pin_n, logloss_pin_rows = the model there, pin_logloss = Pinnacle there).
-- bot_rule_history: per bot, every (rule_version, model_version) its picks carry, with first/last pick
--   and n — "when did this bot's model or rule change" from the ledger itself (rule_version since
--   migration 453; NULL = before tagging). (The older hand-written log is table bot_config_history,
--   migration 281 — 16 rows, last 2026-08-24; the page shows it beside this view.)
SET lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS public.model_accuracy (
  model            text        NOT NULL,
  market           text        NOT NULL,
  win              text        NOT NULL,
  n                integer     NOT NULL,
  logloss          double precision NOT NULL,
  brier            double precision NOT NULL,
  base_logloss     double precision,
  pin_n            integer     NOT NULL DEFAULT 0,
  logloss_pin_rows double precision,
  pin_logloss      double precision,
  first_kickoff    timestamptz,
  last_kickoff     timestamptz,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model, market, win)
);
COMMENT ON TABLE public.model_accuracy IS
  '[[#153]] Forward accuracy per probability source / market / window (7d/30d/90d); job model_accuracy. Private.';

CREATE OR REPLACE VIEW public.bot_rule_history AS
SELECT bot_name,
       rule_version,
       model_version,
       min(pick_time) AS first_pick,
       max(pick_time) AS last_pick,
       count(*)       AS picks
  FROM public.bot_ledger
 GROUP BY bot_name, rule_version, model_version;
COMMENT ON VIEW public.bot_rule_history IS
  '[[#153]] Per bot: each (rule_version, model_version) its picks carry, first/last pick, n. Private.';

REVOKE ALL ON public.model_accuracy, public.bot_rule_history FROM anon, authenticated;
GRANT SELECT ON public.model_accuracy, public.bot_rule_history TO service_role;
GRANT INSERT, UPDATE, DELETE ON public.model_accuracy TO service_role;
