-- OU35-MODEL-SHADOW-BOT-2026-09-08 — register a standalone Over/Under 3.5
-- model-edge shadow bot so the line the OU-LINES-EDGE-TEST flagged (O/U 3.5
-- mirrors the live 2.5: +7.8% Coolbet-executable calibrated model-edge @edge>=8%)
-- gets a FORWARD paper track record on /admin/shadow-bots, independently of the
-- 2.5 bot.
--
-- Why a SEPARATE bot (not folded into bot_coolbet_ou_model_v1): that bot is
-- real-money ON and only ever produces 2.5 (the calibrated cohort has no 3.5
-- picks). Bolting 3.5 onto it would place 3.5 with REAL MONEY before it is
-- fold-robust. 3.5 is only "not-robust +7.8%" on ~6mo of Coolbet history, so it
-- must accrue as PAPER first and be gated on fold-robustness before any money.
--
-- Pick generation: workers/jobs/ou35_model_shadow.py fits isotonic calibration
-- on settled `over35` predictions (the model pipeline does NOT calibrate 3.5),
-- then writes qualifying upcoming picks (calibrated edge vs the single-book
-- Coolbet price >= 8%, §55: single-book executable, NEVER best-of-books) into
-- shadow_bets as market='over_under_35'.
--
-- Settlement: over_under_35 is a STANDARD goals O/U market — the generic shadow
-- settler (_r_ou_goals via the resolver registry) grades it from the final
-- score. No settler branch, and the corners_ou_% skip does not touch it.
--
-- Real money: NONE. This bot is intentionally NOT in the placer's PLACEABLE_BOTS
-- whitelist and has NO coolbet_placer_bots row, so it can never stake money — it
-- is a pure paper tracker and a 👥 PICKS candidate. Promotion (to Coolbet OWN or
-- to a public pick) is a later, owner-gated step once it is fold-robust.

ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper',
                                      'coolbet_ou_model','coolbet_1x2_model','ou35_model'])
           OR shadow_cohort ~ '^[0-9]{4}$');

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll
) VALUES (
    'bot_ou35_model_v1',
    'Over/Under 3.5 model-edge shadow bot — calibrated model P(over/under 3.5) vs the single-book Coolbet price, edge>=8%. Forward paper tracker for the line OU-LINES-EDGE-TEST flagged (mirrors the live 2.5, +7.8% not-yet-robust on ~6mo). PAPER ONLY — never stakes money; a PICKS/OWN promotion candidate once fold-robust (owner-gated).',
    'model_edge_ou35',
    'Fits isotonic calibration on settled over35 predictions, then for each upcoming match with a Coolbet over_under_35 price computes calibrated edge = cal_prob - 1/coolbet_price per side and writes the qualifying side (edge>=0.08) into shadow_bets (market=''over_under_35''). Single-book executable price only (§55). Writes shadow_bets only — no bankroll, no placement. Settled by the generic goals-O/U resolver.',
    TRUE, 'experimental', 1.00, 1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE, maturity_label = 'experimental',
    description = EXCLUDED.description, strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL, retired_reason = NULL, updated_at = NOW();
