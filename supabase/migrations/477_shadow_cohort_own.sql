-- 477 — [[#182]] allow shadow_cohort 'own' (bot_own_1x2_v1, workers/jobs/own_bots.py). Found by the 2026-09-26 queue
-- audit: the OWN bot's first qualifying pick (19:53 UTC) failed with shadow_bets_shadow_cohort_check — the cohort
-- list is closed and 'own' was never added. Same list as migration 409 + 'own'.
SET lock_timeout = '5s';
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (
        shadow_cohort = ANY (ARRAY[
            'morning','midday','pre_ko','corners_paper','coolbet_ou_model',
            'coolbet_1x2_model','ou35_model','coolbet_trigger','unibet_trigger',
            'epicbet_trigger','tonybet_trigger',
            'team_total_paper','fh_1x2_paper','wide_1x2_model','wide_ou_model',
            'trigger_1x2_model','trigger_ou_model','trigger_1x2_sharp',
            'trigger_ou_sharp','trigger_1x2_sharp_tight','inplay_slowstate',
            'inplay_slowstate_afctl','unified_gate_1x2','own'
        ]::text[])
        OR shadow_cohort ~ '^[0-9]{3,4}$'
    ) NOT VALID;
ALTER TABLE shadow_bets VALIDATE CONSTRAINT shadow_bets_shadow_cohort_check;
-- verify: EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shadow_bets_shadow_cohort_check' AND pg_get_constraintdef(oid) LIKE '%''own''%')
