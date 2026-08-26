-- MODEL-PROMOTION-AUDIT-DRIFT-2026-08-26
--
-- model_versions.promoted_at is meant to answer "what is live, and since when".
-- It had stopped doing that. Before this migration:
--
--   v20260607  promoted 2026-06-07, demoted_at NULL   <- DB believed this was live
--   v20260712  promoted_at NULL                       <- actually serving (global)
--   v20260719  promoted_at NULL                       <- actually serving (OU 2.5)
--
-- Production is set by env vars on the VPS (MODEL_VERSION, MODEL_VERSION_OU,
-- MODEL_VERSION_OU_T1, MODEL_VERSION_1X2), and those had been changed directly
-- without running scripts/promote_model.py, which is what writes this column.
-- The audit trail was two model generations behind, so `promote_model.py
-- --dry-run` reported "current production: v20260607" while the pipeline had
-- been serving v20260712 since July.
--
-- Reconciled against what the live scheduler process actually had in its
-- environment on 2026-08-26 (read from /proc/<pid>/environ, not from a config
-- file that might itself be stale):
--
--   MODEL_VERSION=v20260712        -> BTTS, goals, AH, and any unrouted head
--   MODEL_VERSION_OU=v20260719     -> OU
--   MODEL_VERSION_OU_T1=v20260719  -> OU tier 1
--   MODEL_VERSION_1X2=v20260823    -> 1X2   (promoted today, already recorded)
--
-- promoted_at is backdated to each version's known go-live: v20260712 to its
-- training date, v20260719 to 2026-07-19 when the OU override was set. Both are
-- approximations of a date nobody recorded at the time — better than NULL, and
-- the notes say so rather than implying false precision.
--
-- Note this column cannot express per-market promotion, which is what production
-- actually does. Marking three versions live simultaneously is the honest
-- representation available; the per-market detail lives in the notes.

UPDATE model_versions
   SET promoted_at = COALESCE(promoted_at, '2026-07-12 00:00:00+00'::timestamptz),
       notes = COALESCE(notes, '') ||
               ' | Reconciled 2026-08-26 (MODEL-PROMOTION-AUDIT-DRIFT): serving as '
               'the GLOBAL MODEL_VERSION since ~2026-07-12 - BTTS, goals, AH and '
               'any head without a per-market override. promoted_at backdated to '
               'the training date; the true go-live was never recorded.'
 WHERE version = 'v20260712' AND promoted_at IS NULL;

UPDATE model_versions
   SET promoted_at = COALESCE(promoted_at, '2026-07-19 00:00:00+00'::timestamptz),
       notes = COALESCE(notes, '') ||
               ' | Reconciled 2026-08-26 (MODEL-PROMOTION-AUDIT-DRIFT): serving OU '
               'via MODEL_VERSION_OU / MODEL_VERSION_OU_T1 since ~2026-07-19. '
               'promoted_at backdated; the true go-live was never recorded.'
 WHERE version = 'v20260719' AND promoted_at IS NULL;

-- v20260607 has not been live since v20260712 took the global slot.
UPDATE model_versions
   SET demoted_at = COALESCE(demoted_at, '2026-07-12 00:00:00+00'::timestamptz),
       notes = COALESCE(notes, '') ||
               ' | Demoted 2026-08-26 (MODEL-PROMOTION-AUDIT-DRIFT): superseded by '
               'v20260712 as the global model ~2026-07-12. The demotion was never '
               'recorded, so promote_model.py kept reporting this as production.'
 WHERE version = 'v20260607' AND demoted_at IS NULL;

UPDATE model_versions
   SET notes = COALESCE(notes, '') ||
               ' | Promoted 2026-08-26 for the 1X2 head only (MODEL_VERSION_1X2). '
               'Held-out eval n=5,581 vs v20260712: 1x2_away -4.55 pct, 1x2_draw '
               '-1.43 pct, 1x2_home -0.83 pct log-loss. OU deliberately left on '
               'v20260719 - the eval scored OU against the wrong baseline '
               '(WEEKLY-EVAL-BASELINE-2026-08-26), so that comparison is not yet made.'
 WHERE version = 'v20260823';
