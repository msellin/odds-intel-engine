-- 479 — #191 prerequisite: an APPEND-ONLY history of the latest model predictions (2026-09-26).
--
-- WHY. `rating_1x2_predictions` (NEW+ 1X2, r1x2_comb_v1 / r1x2_d8plus_v1) and `ou_model_predictions`
-- (combined O/U, ou_comb_v1) keep ONE row per (match, [market,] version) and are overwritten by the
-- 30-min refresh (workers/jobs/rating_1x2_shadow.refresh) — historically sometimes even after kick-off.
-- So the value a bot would have seen at T-3h is gone by settlement, and no MODEL-based OWN bot can be
-- backtested point-in-time (#191 strategy memo).
--
-- WHAT. Every write to those two tables also appends here — PRE-KICK-OFF ONLY (matches.date > now()
-- and status 'scheduled', enforced in the INSERT … SELECT in the writer) and ONLY WHEN `probs` differs
-- from the latest history row for (match, model_version, market). Never updated, never deleted by code.
--   market   '1x2' for the rating table; over_under_15 / _25 / _35 for the O/U table
--   probs    1x2: {"home","draw","away"};  O/U: {"over" (served), "comb", "pin"}
--   grp      the combiner's availability group ('P&C' | 'P' | 'C' | 'none'), not part of the change test
--   minutes_to_kickoff   (kickoff - written_at) in minutes, stored for cheap "as of T-x" queries
-- Writer: workers/jobs/rating_1x2_shadow._append_history. ACCESS: service_role only (#072).
SET lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS public.model_prediction_history (
    id                  bigserial   PRIMARY KEY,
    match_id            uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    model_version       text        NOT NULL,
    market              text        NOT NULL,
    probs               jsonb       NOT NULL,
    grp                 text,
    minutes_to_kickoff  numeric     NOT NULL CHECK (minutes_to_kickoff > 0),
    written_at          timestamptz NOT NULL DEFAULT now()
);
-- The change test reads the latest row per key; point-in-time reads use the same order.
CREATE INDEX IF NOT EXISTS model_prediction_history_key
    ON public.model_prediction_history (match_id, model_version, market, written_at DESC);

ALTER TABLE public.model_prediction_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.model_prediction_history FROM anon, authenticated;
GRANT ALL ON public.model_prediction_history TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.model_prediction_history_id_seq TO service_role;
