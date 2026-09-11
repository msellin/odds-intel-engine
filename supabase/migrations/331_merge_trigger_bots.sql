-- MERGE-TRIGGER-BOTS (2026-09-11) — 8 trigger bots collapse to 4 configs, and
-- two bots registered an hour earlier turn out to be redundant.
--
-- WHY MERGE. The eight trigger bots were 2 anchors x 2 books x 2 markets. The
-- BOOK is not a strategy, it is a venue, and `pick_generator` already compares
-- across every book a bot may use — so the per-book split bought nothing and
-- would have become 12 bots the moment Epicbet joined. Bot identity should mean
-- STRATEGY. The per-book question does not disappear: `recommended_bookmaker`
-- is now recorded on every pick (it was NULL on 100% of trigger rows until
-- 2026-09-11), so book is a COLUMN to group by rather than an identity.
--
-- WHY THE WIDE TWINS GO. `bot_wide_1x2_model_v1` was registered in migration
-- 330 about an hour before this one, to ask "does the wide candidate source
-- beat the pipeline source?". `bot_trigger_1x2_model_v1` below asks the same
-- thing on the same source and adds draw/away, so the wide twin is a strict
-- SUBSET of it — running both would write duplicate home rows under two names.
-- That redundancy is a consequence of building this in two steps, and it is
-- cheaper to retire it now (they have generated ~nothing) than to ship a known
-- duplicate. The wide-vs-pipeline comparison survives unchanged: it is
-- `bot_trigger_1x2_model_v1` filtered to home, against
-- `bot_coolbet_1x2_model_v1`.
--
-- ⚠️ WHY THE EIGHT OLD TRIGGER BOTS ARE **NOT** RETIRED HERE.
-- `scripts/trigger_calibrator_check.py` is mid-measurement: it compares trigger
-- picks made under the POOLED calibrator (biased, HOME under-estimated 10-15pp)
-- against those made under the per-selection fit, and that verdict GATES the
-- rest of the convergence epic. Retiring the old bots now would stop them
-- accumulating post-fix picks, so the comparison would span a bot change AND a
-- calibrator change — two variables, answering neither. They keep running in
-- parallel until the verdict lands (trigger_calibrator_watch pages it), and a
-- follow-up migration retires them then. Active bot count therefore goes 16 ->
-- 18 temporarily, dropping to 10 once they are retired.
--
-- The new bots' picks are stamped with the calibrator revision by
-- pick_generator, so they cannot be misfiled into the pre-fix bucket.

ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY[
               'morning','midday','pre_ko','corners_paper',
               'coolbet_ou_model','coolbet_1x2_model','ou35_model',
               'coolbet_trigger','unibet_trigger','team_total_paper','fh_1x2_paper',
               'wide_1x2_model','wide_ou_model',
               'trigger_1x2_model','trigger_ou_model',
               'trigger_1x2_sharp','trigger_ou_sharp'])
           OR shadow_cohort ~ '^[0-9]{4}$');

-- 1. The four merged trigger bots. Book is a field, not an identity.
INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES
(
    'bot_trigger_1x2_model_v1',
    'Model-anchored 1x2 trigger across BOTH placeable books. Replaces bot_coolbet_trigger_1x2_v1 + bot_unibet_trigger_1x2_v1 — the book is recorded per pick instead of splitting the bot.',
    'trigger_1x2_model',
    'pick_generator BotConfig: prob_source=predictions (every fixture we model, per-selection isotonic calibration), markets=(1x2,), all selections, books=(Coolbet, Unibet-Site), floors from the engine registry (home-underdog 10% / odds 2.80, others pooled 13%). Edge computed at each book price at decision time; best clearing book wins and is stored in recommended_bookmaker. PAPER.',
    TRUE, 'experimental', 1.00, 1.00
),
(
    'bot_trigger_ou_model_v1',
    'Model-anchored O/U 2.5 trigger across both placeable books. Replaces bot_coolbet_trigger_ou_v1 + bot_unibet_trigger_ou_v1. Inert until the predictions candidate source supports O/U (it covers 1x2 only and refuses rather than guessing a calibration).',
    'trigger_ou_model',
    'pick_generator BotConfig: prob_source=predictions, markets=(over_under_25,), books=(Coolbet, Unibet-Site), registry floors (8% / 1.80). PAPER.',
    TRUE, 'experimental', 1.00, 1.00
),
(
    'bot_trigger_1x2_sharp_v1',
    'SHARP-anchored 1x2 trigger across both books: fair value is the Shin-de-vigged Pinnacle line, not our model. The only trigger family with POSITIVE CLV so far (+8.2% / +9.7%) though on n=13-30. Replaces the two per-book sharp 1x2 bots.',
    'trigger_1x2_sharp',
    'pick_generator BotConfig: prob_source=sharp_devig (Shin de-vig of the complete Pinnacle 1x2 line; a partial line is skipped rather than de-vigged), edge_floor=0.03 and odds_floor=1.01 set EXPLICITLY — a sharp edge is measured against a near-true line so 3% is a real overlay, where inheriting the registry 13% model floor would demand a 13% overlay on Pinnacle (max observed +6.6%) and the bot would never fire. PAPER, observational.',
    TRUE, 'experimental', 1.00, 1.00
),
(
    'bot_trigger_ou_sharp_v1',
    'SHARP-anchored O/U 2.5 trigger across both books. Replaces the two per-book sharp O/U bots.',
    'trigger_ou_sharp',
    'pick_generator BotConfig: prob_source=sharp_devig, markets=(over_under_25,), edge_floor=0.03, odds_floor=1.01 (see the 1x2 sharp bot for why these are explicit). PAPER, observational.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental', description=EXCLUDED.description,
    strategy=EXCLUDED.strategy, strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();

-- 2. Retire the two redundant wide twins from migration 330.
UPDATE bots
   SET is_active = FALSE,
       retired_at = NOW(),
       retired_reason = 'Redundant: bot_trigger_1x2_model_v1 uses the same '
                        'prob_source=predictions and adds draw/away, so this '
                        'home-only bot is a strict subset and would duplicate '
                        'its home rows. The wide-vs-pipeline comparison it was '
                        'created for is unchanged — it is the merged trigger bot '
                        'filtered to home, against bot_coolbet_1x2_model_v1. '
                        'Registered in migration 330 about an hour earlier; '
                        'retired before generating anything meaningful.',
       updated_at = NOW()
 WHERE name IN ('bot_wide_1x2_model_v1', 'bot_wide_ou_model_v1');
