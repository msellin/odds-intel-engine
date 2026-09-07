-- CORNERS-PAPER-FORWARD-2026-09-07 — register the corners line-shop strategy
-- as a proper SHADOW bot so it tracks on /admin/shadow-bots like every other
-- experimental bot, and is automatically kept off the public performance/picks
-- pages (those read simulated_bets / the calibrated cohort; shadow_bets never
-- feeds them).
--
-- Why this replaces the earlier private corners_paper_picks table: gotcha 18 is
-- "one ledger per bot". Rather than invent a second ledger the UI would have to
-- learn to read, the bot writes to shadow_bets (market='corners_ou_<line>',
-- selection over/under). The generic goals-based shadow settler is taught to
-- SKIP corners_ou_% (it grades on the goal score and would silently VOID them);
-- workers/jobs/corners_paper_bot.py settles them from match_stats corners.
--
-- Strategy: for an upcoming fixture, take the best price among the books we can
-- actually place corners at (Betano, Unibet) for a corners O/U selection and
-- record a paper pick when that price beats de-vigged Pinnacle fair value
-- (edge = price * devig_p - 1 >= 0). EUR 10 nominal, same as every shadow bot.
--
-- Paper, not real money: the historical +20.99% did NOT reproduce at executable
-- prices (audit z=+0.33..+3.62, edge Betano/Unibet-only, no dose-response, one
-- 9-day pre-collapse window). Validate forward on real executable prices first.

-- The throwaway table from the first cut of this task (never committed, only
-- ever held local test rows). Idempotent so CI is safe whether or not it exists.
DROP TABLE IF EXISTS corners_paper_picks;

-- shadow_cohort was constrained to the timing cohorts (morning/midday/pre_ko or
-- an HHMM string). This bot is not a timing experiment — it fires 4x/day and
-- wants ONE row per (match, line, side) via the existing unique constraint
-- (shadow_cohort, bot_id, match_id, market, selection). Give it a dedicated,
-- self-identifying cohort value.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY['morning','midday','pre_ko','corners_paper'])
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
    'bot_corners_paper_shadow_v1',
    'Shadow bot for the corners line-shop strategy — logs a hypothetical EUR10 pick when the best Betano/Unibet corners O/U price beats de-vigged Pinnacle fair value. Settled from match_stats corners.',
    'corners_ou_line_shop',
    'Fires on corners_ou_<line> markets for upcoming fixtures. Fair value = de-vigged two-way Pinnacle. Records the best price among the books we can place corners at (Betano, Unibet) when price x devig_p - 1 >= 0. Writes only to shadow_bets — never places or affects bankroll. Settled from match_stats.corners_home + corners_away (over/under, .5 lines never push). Purpose: prove the corners edge forward at executable prices before any real-money consideration.',
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
