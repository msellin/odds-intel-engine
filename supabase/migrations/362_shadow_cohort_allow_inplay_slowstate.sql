-- 362 — allow the 'inplay_slowstate' shadow cohort
--
-- WHY. Phase 1b (INPLAY-VIABILITY-GATE) shipped `workers/jobs/inplay_collector.py`,
-- two bots (`bot_inplay_slowstate_v1` + its AF control arm) and migration 357's
-- `inplay_book_quotes` — but never extended `shadow_bets_shadow_cohort_check`,
-- which whitelists `shadow_cohort` values. The collector writes its picks with
-- shadow_cohort='inplay_slowstate', so EVERY pick insert has raised CheckViolation
-- since the rig launched on 2026-09-15.
--
-- The failure was invisible where it mattered: `write_pick` catches the exception
-- and returns False, so the cycle line reports `picks 0` — indistinguishable from
-- "the triggers did not fire". It logged 2,694 warnings to the collector log that
-- nothing alerts on. Measured cost: the T1 trigger (0-0, 35'-54', under 2.5 at
-- <= 2.20) was satisfied at 1,501 instants across 63 fixtures in the first three
-- days, and the T2 window on 69 more. All of it was dropped. The measurement rig
-- that the whole in-play question depends on recorded nothing for three days while
-- looking healthy.
--
-- This is the RELIABILITY_LEDGER "a second code path inheriting no gates" pattern
-- in reverse: a new writer inheriting a whitelist nobody remembered was there.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;

ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
  CHECK (
    shadow_cohort = ANY (ARRAY[
      'morning', 'midday', 'pre_ko', 'corners_paper',
      'coolbet_ou_model', 'coolbet_1x2_model', 'ou35_model',
      'coolbet_trigger', 'unibet_trigger',
      'team_total_paper', 'fh_1x2_paper',
      'wide_1x2_model', 'wide_ou_model',
      'trigger_1x2_model', 'trigger_ou_model',
      'trigger_1x2_sharp', 'trigger_ou_sharp',
      'inplay_slowstate'
    ])
    OR shadow_cohort ~ '^[0-9]{4}$'
  );
