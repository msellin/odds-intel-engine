-- COOLBET-MODEL-OU-SHADOW-BOT-2026-09-08 — register the model-edge Over/Under
-- strategy as a proper SHADOW bot so it tracks on /admin/shadow-bots like every
-- other experimental bot, and is automatically kept OFF the public
-- performance/picks pages (those read simulated_bets / the calibrated cohort;
-- shadow_bets never feeds them).
--
-- Why this bot exists (COOLBET-OWN-UNIFIED-FLOW-EPIC decision (a)): the strategy
-- backtest found the model-edge O/U instrument is +15% (fold-robust) at
-- executable prices where the line-shop bot's O/U is -17% (negative every month,
-- n~1100 — a live money leak). This bot MIRRORS the calibrated model's O/U picks
-- (edge>=8% on calibrated_prob) into shadow_bets so they place through the PROVEN
-- Coolbet UI placer with the validated per-market gates (edge>=8%, odds>=1.80).
--
-- Pick generation: workers/jobs/coolbet_model_ou_shadow.py mirrors qualifying
-- calibrated simulated_bets rows (market='o/u', result='pending', future kickoff)
-- into shadow_bets, converting the vocabulary to the line-shop form
-- (market='over_under_25'/'over_under_35', selection='over'/'under') so it reuses
-- the line-shop O/U search/place path in coolbet_ui_placer.
--
-- Settlement: over_under_25/35 are STANDARD goals O/U markets — the generic
-- shadow settler (settlement.py _r_ou_goals via the resolver registry) grades
-- them from the goal score exactly as it grades the line-shop bot's identical
-- markets. No dedicated settler branch, and the _PENDING_SHADOW_BETS_SQL
-- corners_ou skip does NOT apply here (that pattern is corners_ou_%, not
-- over_under_%).
--
-- Real-money placement is OFF BY DEFAULT: the UI placer only adds this bot to
-- EXECUTE_ALLOWED_BOTS when env COOLBET_UI_MODEL_EDGE_OU=1. Paper/staged until a
-- human sets that flag with explicit authorization.

-- shadow_cohort was constrained to the timing cohorts + 'corners_paper'. This
-- bot is not a timing experiment — it mirrors the model once per refresh and
-- wants ONE row per (match, line, side) via the existing unique constraint
-- (shadow_cohort, bot_id, match_id, market, selection). Give it a dedicated,
-- self-identifying cohort value. KEEP all previously-allowed values.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper','coolbet_ou_model'])
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
    'bot_coolbet_ou_model_v1',
    'Model-edge Over/Under shadow bot — mirrors the calibrated model''s O/U picks (edge>=8% on calibrated_prob) into shadow_bets so they place through the Coolbet UI placer with the validated per-market gates (edge>=8%, odds>=1.80). Real-money placement gated behind COOLBET_UI_MODEL_EDGE_OU.',
    'model_edge_ou',
    'Fires on the calibrated cohort''s O/U picks (simulated_bets market=''o/u'', result=''pending'', edge_percent>=0.08, calibrated_prob NOT NULL) for lines 2.5 and 3.5 only. Mirrors each qualifying pick into shadow_bets in the line-shop vocabulary (over_under_25/over_under_35, selection over/under), carrying the model''s calibrated_prob + edge so the placer''s live-edge gate (1/(cal_prob-threshold)) and the per-market odds floor (1.80) apply. Writes only to shadow_bets — never places or affects bankroll on its own; real money only via the Coolbet UI placer when COOLBET_UI_MODEL_EDGE_OU=1. Settled by the generic goals O/U resolver.',
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
