-- 409 — allow the per-book trigger cohorts 'epicbet_trigger' / 'tonybet_trigger' (2026-09-24).
--
-- FIXING A DEFECT I SHIPPED THIS MORNING (635be831, #125). `pick_trigger_matcher._cohort_for`
-- was changed to give every trigger book its own shadow_cohort (Coolbet and Epicbet legs of
-- the pooled sharp-tight bot were overwriting each other on the upsert key), and the new
-- names 'epicbet_trigger' and 'tonybet_trigger' were never added to the allow-list from
-- migration 374. Every Epicbet / Tonybet leg of the ACTIVE bot_trigger_1x2_sharp_tight_v1
-- has failed this CHECK since that deploy, and `match_and_emit` swallows the error by design
-- ("never raises") — so the bot silently lost those two books. Found by the #139 5a
-- reader/writer inventory: zero rows in either cohort since the deploy.
--
-- Same shape as 374 (validated, grandfathered clock-time pattern kept); two names added.
-- Smoke SHADOW-COHORT-COVERS-TRIGGER-BOOKS pins every book in BOOK_MARKET_BOTS to this list.
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
            'inplay_slowstate_afctl','unified_gate_1x2'
        ]::text[])
        -- grandfathered + still written by the pipeline's 30-min shadow runs: clock-time cohorts
        OR shadow_cohort ~ '^[0-9]{3,4}$'
    );
