-- BOOK-AGNOSTIC-EDGE-ENGINE (2026-09-09) — SHARP-ANCHOR trigger variant.
-- See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.
--
-- The existing trigger bots (bot_coolbet_trigger_1x2_v1 / _ou_v1) anchor edge on
-- OUR model: edge = cal_prob − 1/coolbet_odds. This adds the SHARP anchor: the
-- same window math, but the fair value comes from the Shin-de-vigged Pinnacle
-- price instead of the model: edge = P_sharp − 1/coolbet_odds. Same fixtures,
-- same Coolbet odds, DIFFERENT reference number.
--
-- Kept as SEPARATE bots (not a flag on the existing ones) because a bot is the
-- unit of P&L accounting here — every ROI/CLV number, backtest fold and
-- /shadow-bots row aggregates per bot. Two anchors = two ROI lines, tracked and
-- (later) graduated independently. Owner decision 2026-09-09.
--
-- Scope: 1x2 + O/U 2.5 only — mirrors exactly what the model trigger covers, so
-- model-vs-sharp is a clean same-market comparison. O/U 3.5 is out (different
-- odds band, no validated floor, and it already has bot_ou35_model_v1).
--
-- Both PAPER — NOT in coolbet_placer.PLACEABLE_BOTS, no coolbet_placer_bots
-- toggle; can never stake money. They only write shadow_bets.

INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
 ('bot_coolbet_trigger_sharp_1x2_v1',
  'BOOK-AGNOSTIC-EDGE-ENGINE Stage B (paper) — Coolbet 1x2 trigger matcher, SHARP anchor. Emits a pick whenever Coolbet''s live 1x2 price lands in the trigger window whose fair value is the Shin-de-vigged Pinnacle price (edge≥13% at Coolbet''s OWN odds vs Pinnacle, odds≥2.80). Paper; measured head-to-head against bot_coolbet_trigger_1x2_v1 (the model-anchored twin). Never stakes money.',
  'trigger_match_coolbet_1x2_sharp',
  'Joins latest pre-match Coolbet 1x2 odds against pick_triggers (strategy=sharp_1x2, cal_prob = de-vigged Pinnacle prob) and writes a shadow_bet when min_odds<=coolbet_odds<=max_odds. Carries edge = P_sharp − 1/coolbet_odds. Settled by the generic 1x2 resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00),
 ('bot_coolbet_trigger_sharp_ou_v1',
  'BOOK-AGNOSTIC-EDGE-ENGINE Stage B (paper) — Coolbet O/U 2.5 trigger matcher, SHARP anchor. Emits a pick whenever Coolbet''s live over_under_25 price lands in the trigger window whose fair value is the de-vigged Pinnacle price (edge≥8% at Coolbet''s OWN odds vs Pinnacle, odds≥1.80). Paper; measured head-to-head against bot_coolbet_trigger_ou_v1 (the model-anchored twin). Never stakes money.',
  'trigger_match_coolbet_ou_sharp',
  'Joins latest pre-match Coolbet over_under_25 odds against pick_triggers (strategy=sharp_ou25, cal_prob = de-vigged Pinnacle prob) and writes a shadow_bet when min_odds<=coolbet_odds<=max_odds. Carries edge = P_sharp − 1/coolbet_odds. Settled by the generic goals-O/U resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental',
    description=EXCLUDED.description, strategy=EXCLUDED.strategy,
    strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();
