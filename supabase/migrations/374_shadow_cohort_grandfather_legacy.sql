-- 374 — grandfather the legacy cohort values ([[#024]], 2026-09-22)
--
-- FIXING A DEFECT I SHIPPED THIS MORNING. Migration 370 replaced
-- `shadow_bets_shadow_cohort_check` with an allow-list and marked it NOT VALID,
-- reasoning that NOT VALID leaves the 156,795 legacy rows alone.
--
-- NOT VALID skips validation of existing rows AT ALTER TIME. It does NOT exempt
-- them from the constraint afterwards: any UPDATE that touches such a row
-- re-checks it and fails. So migration 370 quietly made 156,795 rows
-- **un-updatable** — not just un-backfillable, but un-re-settleable, which is a
-- far worse property for a ledger than a permissive constraint ever was.
--
-- It surfaced within hours: the [[#024]] CLV backfill died on
--   CheckViolation ... Failing row contains (... 1210 ...)
-- where `1210` is a legacy cohort. The row was not being given a bad cohort; it
-- merely HAD one, and the UPDATE was about clv.
--
-- THE FIX. The legacy values are all the same shape — 84 distinct values, every
-- one matching ^[0-9]{3,4}$ (verified, zero exceptions): clock times like '0010',
-- '1210', '2129', from when the column meant "the half-hour slot this ran in"
-- rather than a cohort. They are grandfathered by PATTERN rather than by listing
-- 84 literals, so the intent stays readable.
--
-- The guarantee migration 370 wanted is unchanged and still enforced: a NEW
-- cohort must be a known name. A typo'd 'trigger_1x2_shrap' is still refused.
-- What is no longer refused is editing a row that predates the scheme.
--
-- VALIDATED, not NOT VALID, this time — every existing row satisfies this
-- predicate by construction, so there is nothing to defer and deferring is what
-- hid the problem.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (
        shadow_cohort = ANY (ARRAY[
            'morning','midday','pre_ko','corners_paper','coolbet_ou_model',
            'coolbet_1x2_model','ou35_model','coolbet_trigger','unibet_trigger',
            'team_total_paper','fh_1x2_paper','wide_1x2_model','wide_ou_model',
            'trigger_1x2_model','trigger_ou_model','trigger_1x2_sharp',
            'trigger_ou_sharp','trigger_1x2_sharp_tight','inplay_slowstate',
            'inplay_slowstate_afctl','unified_gate_1x2'
        ]::text[])
        -- grandfathered: pre-scheme rows whose cohort is a clock time
        OR shadow_cohort ~ '^[0-9]{3,4}$'
    );
