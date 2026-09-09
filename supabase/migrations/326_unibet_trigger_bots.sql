-- UNIBET-TRIGGER-BOTS (2026-09-09, COOLBET-PICK-TABLE-AUDIT Stage 3b) — extend the
-- book-agnostic trigger engine to a SECOND book. The matcher already evaluates each
-- book's own odds_snapshots against the shared pick_triggers windows; these are the
-- Unibet twins of the 4 Coolbet trigger bots, reading bookmaker='Unibet-Site' (the
-- broad site sweep). ALL PAPER — not in PLACEABLE_BOTS, no coolbet_placer_bots toggle,
-- can never stake money. They accumulate settled picks so a future, data-gated,
-- owner-approved promotion has a track record to judge (Family 2 lifecycle).
--
-- Note (ANALYSIS_GOTCHAS §57): the DRAW edge the model structurally can't see lives on
-- the SHARP anchor here (soft-book draw mispricing vs de-vig Pinnacle).

-- 1) allow the new shadow_cohort so the Unibet trigger picks group + settle separately
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper',
                                      'coolbet_ou_model','coolbet_1x2_model','ou35_model',
                                      'coolbet_trigger','unibet_trigger'])
           OR shadow_cohort ~ '^[0-9]{4}$');

-- 2) register the 4 Unibet trigger bots (model + sharp × 1x2 + O/U), PAPER
INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
 ('bot_unibet_trigger_1x2_v1',
  'Stage 3b (paper) — Unibet 1x2 MODEL trigger. Emits a pick when Unibet''s live 1x2 site price (bookmaker=Unibet-Site) lands in the model trigger window (edge≥13% at Unibet''s OWN odds, odds≥2.80). Paper; the Unibet twin of bot_coolbet_trigger_1x2_v1.',
  'trigger_match_unibet_1x2',
  'Joins latest pre-match Unibet-Site 1x2 odds against pick_triggers (strategy=model_1x2) and writes a shadow_bet (cohort=unibet_trigger) when min_odds<=odds<=max_odds. Edge at Unibet''s price. Generic 1x2 resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00),
 ('bot_unibet_trigger_ou_v1',
  'Stage 3b (paper) — Unibet O/U 2.5 MODEL trigger. Emits when Unibet''s live over_under_25 site price lands in the model window (edge≥8% at Unibet''s OWN odds, odds≥1.80). Paper; twin of bot_coolbet_trigger_ou_v1.',
  'trigger_match_unibet_ou',
  'Joins latest pre-match Unibet-Site over_under_25 odds against pick_triggers (strategy=model_ou25). cohort=unibet_trigger. Generic goals-O/U resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00),
 ('bot_unibet_trigger_sharp_1x2_v1',
  'Stage 3b (paper) — Unibet 1x2 SHARP trigger. Fires when Unibet''s 1x2 site price beats the de-vigged Pinnacle line by ≥3% (no odds floor — observing all bands). Paper; twin of bot_coolbet_trigger_sharp_1x2_v1. This is where the DRAW edge the model can''t see should surface (ANALYSIS_GOTCHAS §57).',
  'trigger_match_unibet_sharp_1x2',
  'Joins latest pre-match Unibet-Site 1x2 odds against pick_triggers (strategy=sharp_1x2, fair value=Shin-de-vig Pinnacle). cohort=unibet_trigger. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00),
 ('bot_unibet_trigger_sharp_ou_v1',
  'Stage 3b (paper) — Unibet O/U 2.5 SHARP trigger. Fires when Unibet''s over_under_25 site price beats the de-vigged Pinnacle line by ≥3%. Paper; twin of bot_coolbet_trigger_sharp_ou_v1.',
  'trigger_match_unibet_sharp_ou',
  'Joins latest pre-match Unibet-Site over_under_25 odds against pick_triggers (strategy=sharp_ou25, fair value=Shin-de-vig Pinnacle). cohort=unibet_trigger. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental',
    description=EXCLUDED.description, strategy=EXCLUDED.strategy,
    strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();
