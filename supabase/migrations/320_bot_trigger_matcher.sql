-- BOOK-AGNOSTIC-EDGE-ENGINE Stage B (2026-09-09) — register the paper bot that
-- the trigger matcher emits into. See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.
--
-- Stage B joins each book's latest swept odds (odds_snapshots) against the Stage A
-- trigger windows (pick_triggers) and writes a shadow_bet for any price landing in
-- [min_odds, max_odds] — the CORRECT Coolbet-native selection (evaluated at
-- Coolbet's own odds), to run PAPER alongside the current simulated_bets-copy
-- mirror so we can measure the wider universe's real ROI before trusting it.
--
-- PAPER ONLY: not in the placer's PLACEABLE_BOTS whitelist, no coolbet_placer_bots
-- row — it can never stake money. Promotion (replace the mirror) is owner-gated on
-- an OOS backtest of the Coolbet-native set. One bot per book so each book's
-- trigger selection is measured independently (Unibet adds its own later).

ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper',
                                      'coolbet_ou_model','coolbet_1x2_model','ou35_model',
                                      'coolbet_trigger'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES (
    'bot_coolbet_trigger_v1',
    'BOOK-AGNOSTIC-EDGE-ENGINE Stage B (paper) — emits a pick whenever Coolbet''s live price lands in the model''s trigger window (pick_triggers). The CORRECT Coolbet-native selection: edge evaluated at Coolbet''s OWN odds, not the /picks reference odds. Runs paper alongside the simulated_bets-copy mirror to measure the wider universe before promotion. Never stakes money.',
    'trigger_match_coolbet',
    'For each upcoming 1x2/O-U-2.5 fixture with a Coolbet price, joins the latest pre-match Coolbet odds against pick_triggers and writes a shadow_bet when min_odds <= coolbet_odds <= max_odds. Carries calibrated_prob + the edge at Coolbet''s price. Settled by the generic 1x2 / goals-O/U resolvers. PAPER — not placeable.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE, maturity_label = 'experimental',
    description = EXCLUDED.description, strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL, retired_reason = NULL, updated_at = NOW();
