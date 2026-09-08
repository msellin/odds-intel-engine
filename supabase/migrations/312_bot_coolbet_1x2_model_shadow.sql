-- COOLBET-MODEL-1X2-SHADOW-BOT-2026-09-08 — register the model-edge 1x2
-- strategy as a proper SHADOW bot so it tracks on /admin/shadow-bots like every
-- other experimental bot, and is automatically kept OFF the public
-- performance/picks pages (those read simulated_bets / the calibrated cohort;
-- shadow_bets never feeds them).
--
-- Why this bot exists: it REPLACES the paused line-shop 1x2 real-money path. The
-- line-shop 1x2 raw signal loses out-of-sample; the model-edge 1x2 instrument at
-- edge>=13% (calibrated_prob) AND odds>=2.80 HOLDS out-of-sample at +48% on the
-- test fold. This bot MIRRORS the calibrated model's 1x2 picks into shadow_bets
-- so they place through the PROVEN Coolbet UI placer with the validated 2D gate
-- (edge>=13%, odds>=2.80).
--
-- Pick generation: workers/jobs/coolbet_model_1x2_shadow.py mirrors qualifying
-- calibrated simulated_bets rows (market='1x2', result='pending', future
-- kickoff, edge_percent>=0.13, calibrated_prob NOT NULL) into shadow_bets. UNLIKE
-- the O/U mirror there is NO vocabulary conversion: 1x2 is already the placer's
-- market and home/draw/away are already the placer's selections — write them
-- straight through.
--
-- Settlement: '1x2' is a STANDARD match-result market. The generic shadow
-- settler (settlement.py, matched by the resolver registry's 1x2 predicate)
-- grades it from the goal score exactly as it grades the line-shop bot's
-- identical 1x2 market. No dedicated settler branch, and the corners_ou_% skip
-- in _PENDING_SHADOW_BETS_SQL does NOT touch '1x2'.
--
-- Real-money placement is OFF BY DEFAULT: the seeded coolbet_placer_bots row is
-- ui_place_enabled=false, and the placer's effective allowlist is
-- PLACEABLE_BOTS ∩ (rows here WHERE ui_place_enabled=true). Paper/staged until a
-- human flips the toggle with explicit authorization.

-- shadow_cohort was constrained to the timing cohorts + 'corners_paper' +
-- 'coolbet_ou_model' (migration 309). This bot is not a timing experiment — it
-- mirrors the model once per refresh and wants ONE row per (match, market,
-- selection) via the existing unique constraint
-- (shadow_cohort, bot_id, match_id, market, selection). Give it a dedicated,
-- self-identifying cohort value. KEEP all previously-allowed values.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper','coolbet_ou_model','coolbet_1x2_model'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name,
    description,
    strategy,
    strategy_description,
    is_active,
    maturity_label,
    starting_bankroll,
    current_bankroll
) VALUES (
    'bot_coolbet_1x2_model_v1',
    'Model-edge 1x2 shadow bot — mirrors the calibrated model''s 1x2 picks (edge>=13% on calibrated_prob) into shadow_bets so they place through the Coolbet UI placer with the validated 2D gate (edge>=13%, odds>=2.80, OOS-validated +48% test). Replaces the paused line-shop 1x2. Real-money placement gated by the coolbet_placer_bots toggle (OFF by default).',
    'model_edge_1x2',
    'Fires on the calibrated cohort''s 1x2 picks (simulated_bets market=''1x2'', result=''pending'', edge_percent>=0.13, calibrated_prob NOT NULL). Mirrors each qualifying pick into shadow_bets WITHOUT vocabulary conversion (market stays ''1x2'', selection stays home/draw/away), carrying the model''s calibrated_prob + edge so the placer''s live-edge gate (1/(cal_prob-threshold)) and the per-market odds floor (2.80) apply. Writes only to shadow_bets — never places or affects bankroll on its own; real money only via the Coolbet UI placer when its coolbet_placer_bots toggle is ui_place_enabled=true. Settled by the generic 1x2 match-result resolver.',
    TRUE,
    'experimental',
    -- Bankroll must be > 0 per chk_bots_bankroll_positive. Nominal 1.00 —
    -- never touched (shadow_bets uses a fixed 10u nominal stake).
    1.00,
    1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE,
    maturity_label = 'experimental',
    description = EXCLUDED.description,
    strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL,
    retired_reason = NULL,
    updated_at = NOW();

-- COOLBET-PLACER-CONTROL seed: add the runtime real-money toggle row for this
-- bot, OFF by default. Effective real-money allowlist is the code-level
-- PLACEABLE_BOTS ∩ rows here WHERE ui_place_enabled=true, so this bot cannot
-- place until BOTH it is added to PLACEABLE_BOTS (done in the placer) AND a
-- human flips this row ON. ON CONFLICT DO NOTHING so re-applying never clobbers
-- a value a human has since changed.
INSERT INTO coolbet_placer_bots (bot_name, ui_place_enabled, note, updated_at) VALUES
    ('bot_coolbet_1x2_model_v1', false, 'Coolbet 1x2 model-edge — OFF pending dry-run', now())
ON CONFLICT (bot_name) DO NOTHING;
