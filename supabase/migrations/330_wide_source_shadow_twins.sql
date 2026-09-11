-- PICK-GENERATOR / WIDE-SOURCE TWINS (2026-09-11)
--
-- Register PAPER twins of the two real-money model bots that differ in exactly
-- ONE configuration value: `prob_source='predictions'` instead of `'pipeline'`.
--
-- WHY. The real-money mirrors take their candidates from `simulated_bets`, i.e.
-- only fixtures the PIPELINE already picked — and that pick list is built from
-- AF API odds, which are not current and contain NO COOLBET AT ALL. So it is
-- narrow for reasons that have nothing to do with our edge. Measured at one
-- moment on 2026-09-11: 1 candidate from the pipeline source against 81 from
-- predictions; by day, ~300-400 fixtures predicted (284/151/388 on 09-09/10/11)
-- times 3 selections, roughly 1,000 candidate selections against the pipeline's
-- ~10 picks. That ~100x gap is why the trigger bots raised 318 picks in 7 days
-- where the mirrors raised 27, and it is the idea the trigger bots were built
-- on in the first place.
--
-- WHY TWINS RATHER THAN FLIPPING THE REAL-MONEY BOTS. The wider source is also
-- a DIFFERENT probability: it re-calibrates raw predictions here, where the
-- pipeline's `calibrated_prob` is the number the real-money bots were validated
-- on. Switching them would silently change what we stake on. Running both on
-- the SAME mechanism, differing only in that one field, turns the question into
-- a measurement instead of an argument — and that is the whole point of the
-- pick_generator refactor.
--
-- Same gates as their real-money counterparts: registry floors
-- (1x2 home-underdogs 10% / odds 2.80, o/u 8% / 1.80), same placeable books.
-- PAPER ONLY: not in scripts/place_coolbet_ui.PLACEABLE_BOTS, so the UI placer
-- cannot stake them even if a toggle were flipped.

ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY[
               'morning','midday','pre_ko','corners_paper',
               'coolbet_ou_model','coolbet_1x2_model','ou35_model',
               'coolbet_trigger','unibet_trigger','team_total_paper','fh_1x2_paper',
               'wide_1x2_model','wide_ou_model'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES
(
    'bot_wide_1x2_model_v1',
    'PAPER twin of bot_coolbet_1x2_model_v1 differing in ONE config value: candidates come from every fixture we model (predictions, re-calibrated per selection) instead of only fixtures the AF-odds-driven pipeline already picked. Same gates, same books.',
    'wide_source_1x2_model',
    'pick_generator BotConfig prob_source=predictions, markets=(1x2,), selections=(home,), books=(Coolbet, Unibet-Site), floors from the engine registry (home-underdog 10%, odds 2.80). Probability = per-selection isotonic calibration of predictions.model_probability (pick_triggers._fit_calibrator, made per-selection 2026-09-11 after the pooled fit was found to under-estimate HOME by 10-15pp). Exists to measure whether the ~100x wider candidate source beats the pipeline source on CLV before any real-money bot is switched to it.',
    TRUE, 'experimental', 1.00, 1.00
),
(
    'bot_wide_ou_model_v1',
    'PAPER twin of bot_coolbet_ou_model_v1 on the wide candidate source. Placeholder until the predictions source supports O/U — it currently covers 1x2 only and refuses rather than guessing an O/U calibration.',
    'wide_source_ou_model',
    'pick_generator BotConfig prob_source=predictions, markets=(o/u, over_under_25, over_under_35), books=(Coolbet, Unibet-Site), floors from the registry (8% / 1.80). Generates nothing until _candidates_from_predictions gains an O/U calibrator + line vocabulary; registered now so the comparison is wired when it does.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental', description=EXCLUDED.description,
    strategy=EXCLUDED.strategy, strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();
