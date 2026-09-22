-- 370 — UNIFIED-GATE 1x2 paper instrument ([[#033]], 2026-09-22)
--
-- WHAT QUESTION IT EXISTS TO ANSWER, and why nothing else can.
--
-- The owner's hypothesis: instead of a per-selection edge floor plus an
-- exclusion list, use ONE rule — every 1x2 selection, edge >= 10%, odds >= 2.80.
-- The mechanism is right: a home favourite prices under ~2.0, so the odds floor
-- excludes home-favs automatically, no exclusion list required.
--
-- The DRAW/AWAY half of it has never been testable. The calibrated cohort at
-- odds >= 2.80 is **236 HOME out of 240**, because our own home-only mirror
-- stopped generating draws and aways. So every "draws lose" / "aways win"
-- number produced so far is computed on a population that is ~98% home, and the
-- honest answer to the hypothesis is not "yes" or "no" but "we cannot tell".
-- Measuring harder on the same rows cannot fix that; only new rows can.
--
-- This bot generates those rows. It stakes nothing and publishes nothing —
-- `show_on_picks` FALSE, paper only, not in PLACEABLE_BOTS. Its entire purpose
-- is to make a currently-unanswerable question answerable in a few weeks.
--
-- ⚠️ IT COULD NOT HAVE BEEN BUILT CORRECTLY BEFORE TODAY. The instrument needs a
-- FLAT 10% floor on all three selections — that IS the hypothesis. Until
-- SHARP-FLOOR-STACKED-ON-MODEL-FLOOR was fixed this morning ([[#007]]),
-- `best_price_router.decide_book` re-imposed the selection-aware registry floor
-- on top of any bot's explicit `edge_floor`, so draws and aways would silently
-- have run at 13% while the bot's config claimed 10%. It would have produced
-- numbers, and they would have answered a different question than the one asked.
--
-- NO EDGE CEILING, deliberately: this is MODEL-anchored, where a 20% edge is
-- ordinary (the registry floor is 13%). Ceilings belong on sharp-anchored bots,
-- where fair value is near-true and a large overlay means a broken price.
INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks
) VALUES (
    'bot_unified_gate_1x2_paper_v1',
    'UNIFIED-GATE instrument ([[#033]]) — every 1x2 selection (home, draw AND away) at a FLAT 10% model edge and odds >= 2.80. Exists to generate the draw/away evidence the calibrated cohort cannot provide: at odds >= 2.80 that cohort is 236 HOME of 240, so the owner''s "keep draws and aways, let the 2.80 floor exclude home-favs" hypothesis has never actually been tested. PAPER, publishes nothing, stakes nothing.',
    'unified_gate_1x2',
    'pick_generator BotConfig: prob_source=predictions (every fixture we model, per-selection isotonic calibration), markets=(1x2,), ALL selections, books=(Coolbet, Unibet-Site), edge_floor=0.10 set EXPLICITLY so the flat floor applies to draws and aways too — the selection-aware registry floor (10% home / 13% draw+away) is exactly what this instrument is testing against and must not be inherited. odds_floor=2.80. No edge ceiling (model-anchored). Edge computed at each book price at decision time; best clearing book wins and is recorded in recommended_bookmaker. PAPER.',
    TRUE, 'experimental', 1.00, 1.00, FALSE
)
ON CONFLICT (name) DO NOTHING;

-- THE COHORT MUST BE IN THE CHECK CONSTRAINT, or every insert for this bot
-- fails. `shadow_bets.shadow_cohort` is an explicit allow-list — the right
-- design (a typo'd cohort cannot quietly create a new population), and also the
-- reason registering a bot is two steps, not one. Migration 331's smoke test
-- says exactly this in its own failure message, and that comment is what caught
-- it here before the bot ever ran.
--
-- ⚠️ ADDED `NOT VALID`, AND THAT IS NOT LAZINESS. `shadow_bets` holds **156,795
-- rows whose cohort is not in the list at all** — 84 distinct legacy values that
-- are CLOCK TIMES ('0810', '1440', '2129'), from before the column meant a
-- cohort. A plain ADD CONSTRAINT re-validates the whole table and would fail on
-- every one of them, taking the migration — and this deploy — down with it.
-- NOT VALID enforces the allow-list on every NEW row, which is the entire point,
-- and leaves history alone. That is also the state the existing constraint is
-- effectively in, so this preserves behaviour rather than changing it.
--
-- The list is a strict SUPERSET of the live one (verified before writing: zero
-- cohorts dropped). It also adds `trigger_1x2_sharp_tight` and
-- `inplay_slowstate_afctl`, which are registered bots whose cohorts were never
-- added — today they write under borrowed cohort names
-- (`bot_trigger_1x2_sharp_tight_v1` splits across 'coolbet_trigger'/'unibet_trigger'),
-- so listing them costs nothing now and unblocks fixing that separately.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;
ALTER TABLE shadow_bets ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (shadow_cohort = ANY (ARRAY[
        'morning','midday','pre_ko','corners_paper','coolbet_ou_model',
        'coolbet_1x2_model','ou35_model','coolbet_trigger','unibet_trigger',
        'team_total_paper','fh_1x2_paper','wide_1x2_model','wide_ou_model',
        'trigger_1x2_model','trigger_ou_model','trigger_1x2_sharp',
        'trigger_ou_sharp','trigger_1x2_sharp_tight','inplay_slowstate',
        'inplay_slowstate_afctl','unified_gate_1x2'
    ]::text[])) NOT VALID;
