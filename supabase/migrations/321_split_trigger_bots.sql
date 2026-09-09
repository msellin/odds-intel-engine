-- BOOK-AGNOSTIC-EDGE-ENGINE (2026-09-09) — split the single per-book trigger bot
-- into one bot per (book × market), so each market's ROI is tracked and gated
-- independently. The backtest showed 1x2 (−21%) and O/U (+4%) diverge sharply;
-- blending them in one bot hides where money is lost. Owner request:
-- "1 bot per book + market combo ... easily track where we are losing money."
--
-- Retires bot_coolbet_trigger_v1 (single, replaced) and its paper picks; adds
-- bot_coolbet_trigger_1x2_v1 + bot_coolbet_trigger_ou_v1. Both PAPER — not in
-- PLACEABLE_BOTS, no coolbet_placer_bots toggle; can never stake money.

-- new per-market trigger bots
INSERT INTO bots (name, description, strategy, strategy_description,
                  is_active, maturity_label, starting_bankroll, current_bankroll)
VALUES
 ('bot_coolbet_trigger_1x2_v1',
  'BOOK-AGNOSTIC-EDGE-ENGINE Stage B (paper) — Coolbet 1x2 trigger matcher. Emits a pick whenever Coolbet''s live 1x2 price lands in the model''s trigger window (edge≥13% at Coolbet''s OWN odds, odds≥2.80). Paper; measured against bot_coolbet_1x2_model_v1 (the live mirror) and bot_v10_all. Never stakes money.',
  'trigger_match_coolbet_1x2',
  'Joins latest pre-match Coolbet 1x2 odds against pick_triggers (strategy=model_1x2) and writes a shadow_bet when min_odds<=coolbet_odds<=max_odds. Carries the edge at Coolbet''s price. Settled by the generic 1x2 resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00),
 ('bot_coolbet_trigger_ou_v1',
  'BOOK-AGNOSTIC-EDGE-ENGINE Stage B (paper) — Coolbet O/U 2.5 trigger matcher. Emits a pick whenever Coolbet''s live over_under_25 price lands in the model''s trigger window (edge≥8% at Coolbet''s OWN odds, odds≥1.80). Paper; measured against bot_coolbet_ou_model_v1 (the live mirror) and bot_v10_all. Never stakes money.',
  'trigger_match_coolbet_ou',
  'Joins latest pre-match Coolbet over_under_25 odds against pick_triggers (strategy=model_ou25) and writes a shadow_bet when min_odds<=coolbet_odds<=max_odds. Carries the edge at Coolbet''s price. Settled by the generic goals-O/U resolver. PAPER — not placeable.',
  TRUE, 'experimental', 1.00, 1.00)
ON CONFLICT (name) DO UPDATE
SET is_active=TRUE, maturity_label='experimental',
    description=EXCLUDED.description, strategy=EXCLUDED.strategy,
    strategy_description=EXCLUDED.strategy_description,
    retired_at=NULL, retired_reason=NULL, updated_at=NOW();

-- retire the single blended bot + drop its paper picks (just created today, no value)
DELETE FROM shadow_bets WHERE bot_id = (SELECT id FROM bots WHERE name='bot_coolbet_trigger_v1');
UPDATE bots SET is_active=FALSE, retired_at=NOW(),
       retired_reason='Split into per-(book×market) bots 2026-09-09 (trigger_1x2 + trigger_ou)'
 WHERE name='bot_coolbet_trigger_v1';
