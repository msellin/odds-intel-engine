-- 375_v10_split_and_bot_display_names.sql
--
-- V10-SPLIT-BY-MARKET ([[#040]]) + BOT-NAMES-AND-LABELS ([[#069]]), 2026-09-22.
--
-- Two queue rows, one migration, because #069's own row says the split must land
-- FIRST "or the split immediately makes the new names wrong". Naming a bot that is
-- about to become two bots is wasted work done twice.
--
-- ============================================================================
-- WHY THE SPLIT IS NOT "ACCOUNTING ONLY"
-- ============================================================================
-- #040 was filed as an organisational change: "the model/picks do NOT change".
-- Measuring the two halves separately before writing it showed otherwise.
--
-- De-vigged Pinnacle CLV (`simulated_bets.clv_pinnacle_devig`), settled rows.
-- CLV rather than ROI per ANALYSIS_GOTCHAS section 8 -- it converges ~200x faster:
--
--   market           n     CLV       95% CI              ROI
--   1x2            335   +2.50%   [+0.41, +4.60]      +12.80%  (n=400)
--   over_under_25  181   -3.85%   [-5.01, -2.69]       -0.54%  (n=252)
--
-- Both CIs exclude zero, on opposite sides. The public "+11-13% calibrated
-- reference bot" is ONE MARKET CARRYING THE OTHER.
--
-- It is not a calibration artefact (gotcha 39, "never measure across a
-- calibration change"). The O/U half is negative in EVERY month --
--   May -4.53%  Jun -3.74%  Jul -3.55%  Aug -4.84%  Sep -1.76%
-- and in EVERY model version --
--   v14 -3.48%  v20260524_market -6.12%  v20260607 -3.44%  v20260621 -2.75%
--   v20260705 -4.04%  v20260712 -2.73%  v9a -0.97%
-- so it predates and outlives OU-CALIBRATOR-DOMAIN-MISMATCH (migration 335).
-- That bug made a bad half worse; it did not create it.
--
-- The 1x2 half is weaker than its pooled number looks, and the label below is
-- chosen knowing that: monthly CLV runs May -0.87%, Jun -2.15%, Jul +8.35%,
-- Aug +8.24%, Sep +7.25%. The positive pooled figure is entirely July-onward
-- (n=142 positive era vs n=193 before it). `calibrated` is defensible on current
-- evidence; it is a three-month record, not a five-month one.
--
-- CONSEQUENCE FOR LABELS: `bot_v10_ou` ships `beta`, NOT `calibrated`. The
-- /performance legend sells `calibrated` as proven. A half whose CI sits entirely
-- below zero cannot carry it.
--
-- ⚠️ THE `beta` LABEL IS NOT COSMETIC -- BE CLEAR ABOUT WHAT IT TURNS OFF.
-- `maturity_label` is read as a GATE in four places, and dropping the O/U half
-- from `calibrated` to `beta` closes all four to it:
--   * coolbet_placer._allowed_maturity_labels() -- .env has
--     COOLBET_RECORD_ALLOWED_MATURITY=calibrated (already moot for O/U: the
--     placeable bot_coolbet_ou_model_v1 has been toggled OFF since 2026-09-13)
--   * coolbet_signaler's `group_has_calibrated` -- the PUBLIC TELEGRAM gate
--   * coolbet_prekickoff_alert's allowed list
--   * pick_generator's `b.maturity_label = ANY(%s)` candidate filter
-- So this is an identity change AND a publication change, and saying otherwise
-- would be the "config edited but not deployed" pattern in reverse -- a real
-- behaviour change hidden inside a refactor's description.
--
-- MEASURED COST OF THAT TODAY: ZERO PICKS. `picks_public_all` shows
-- bot_v10_all/over_under_25 last published 2026-09-13 -- the day migration 335
-- deleted the broken O/U calibrator. Nine days of silence before this migration
-- was written, so nothing that is currently flowing stops flowing. That matters
-- because "we dont dry up picks for telegram users" is the binding constraint on
-- this whole block of work.
--
-- `show_on_picks` is deliberately left TRUE on both halves, so /picks is
-- unchanged and the O/U half can resume the moment it is re-promoted under the
-- rule in docs/SYSTEM_MAP.md. Whether the O/U half should publish AT ALL on a
-- -3.85% CLV is a product decision and it belongs to the owner, flagged on the
-- queue row rather than silently taken here.
--
-- ============================================================================
-- WHY `bots.name` IS NOT RENAMED
-- ============================================================================
-- `bots.name` is the join key for `simulated_bets`, `shadow_bets`, `real_bets`,
-- `picks_public_all`, `ENGINE_BOT_FLOORS` and every analysis script in the repo.
-- The 2026-09 audits are full of rows lost to identity changes. `display_name` is
-- a NEW, ADDITIVE, DISPLAY-ONLY column. Nothing may ever join on it.
--
-- The split DOES change `bot_id` on historical rows -- that is the point of it --
-- and it is safe against both unique indexes because both contain `market`:
--   uq_bet_per_bot_match_market_selection (bot_id, match_id, market, selection)
--   uq_shadow_bet_per_cohort (shadow_cohort, bot_id, match_id, market, selection)
-- and the two target bots take DISJOINT market sets. No row can collide with
-- another. The counts are asserted below regardless.
--
-- `bankroll_after` on the re-attributed rows becomes a discontinuous series
-- within each new bot. Accepted and documented: it is a per-bot running total
-- whose only consumer is the equity chart, which re-derives the curve from
-- `starting_bankroll` plus that bot's own bets.
--
-- ============================================================================
-- `testing` BECOMES A REAL LABEL (#069 part c)
-- ============================================================================
-- Audited 2026-09-22: `testing` is not a database value at all. It exists only as
-- a string `/performance/page.tsx:441` stamps on the injected forward-test rows,
-- so the page's own legend documents three maturity tiers of which one has no
-- backing field. Added to the CHECK and stamped on the two bots that earned it.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. display_name -- display only, NEVER a join key
-- ---------------------------------------------------------------------------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS display_name text;

COMMENT ON COLUMN bots.display_name IS
  'Human-readable label for customer surfaces (/performance, /picks). DISPLAY '
  'ONLY -- never join, filter or key on this. `bots.name` is the identity and is '
  'referenced by simulated_bets, shadow_bets, real_bets, picks_public_all and '
  'ENGINE_BOT_FLOORS. Added by migration 375 ([[#069]]) precisely so a bot can be '
  'renamed for readers without destroying its attribution.';

-- ---------------------------------------------------------------------------
-- 2. `testing` becomes a legal maturity label
-- ---------------------------------------------------------------------------
ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_maturity_label_check;
ALTER TABLE bots ADD CONSTRAINT bots_maturity_label_check
  CHECK (maturity_label IS NULL OR maturity_label = ANY (ARRAY[
    'experimental',  -- writes shadow_bets; hidden from /performance by design
    'beta',          -- public, but explicitly "still accumulating"
    'calibrated',    -- public and promoted; see the rule in docs/SYSTEM_MAP.md
    'testing',       -- published forward test: readers RECEIVE these picks, and
                     -- the record is pre-registered, but n is not yet callable
    'retired'
  ]));

-- ---------------------------------------------------------------------------
-- 3. The two halves. ON CONFLICT DO UPDATE so this is idempotent AND so it wins
--    if `ensure_bots()` raced ahead and created them with NULL maturity_label
--    (which would silently hide them from /performance -- isPublicBot(null) is
--    false).
--
--    Bankrolls are seeded at 1000 and RECOMPUTED from the ledger in step 4b, not
--    hardcoded. Smoke `BOT-BANKROLL-DRIFT` asserts
--    current_bankroll = starting_bankroll + sum(pnl) within EUR0.50 for every
--    active bot, and `settlement.py:3667` does a read-modify-write on this column
--    -- so whatever value it holds after this migration becomes the permanent
--    baseline. It is also a live STAKING input (daily_pipeline_v2.py:2597), so a
--    wrong figure changes real stake sizes, and it renders on /performance for
--    elite readers. Too many consumers to eyeball a constant.
--    (For the record, at write time: 1x2 +360.17 and O/U -12.91 reconstruct
--    bot_v10_all's 1347.26 exactly.)
-- ---------------------------------------------------------------------------
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES
  ('bot_v10_1x2', 'Match result',
   'v10 model, all target leagues, tier-adjusted thresholds -- 1x2 only. Split '
   'from bot_v10_all by migration 375. De-vigged Pinnacle CLV +2.50% (n=335, 95% '
   'CI [+0.41, +4.60]); the positive record is July-2026 onward.',
   1000.00, 1000.00, true, 'calibrated', true),
  ('bot_v10_ou', 'Goals over/under 2.5',
   'v10 model, all target leagues, tier-adjusted thresholds -- over/under 2.5 '
   'only. Split from bot_v10_all by migration 375. BETA NOT CALIBRATED: de-vigged '
   'Pinnacle CLV -3.85% (n=181, 95% CI [-5.01, -2.69]), negative in all 5 months '
   'and all 7 model versions. Published nothing since 2026-09-13.',
   1000.00, 1000.00, true, 'beta', true)
ON CONFLICT (name) DO UPDATE SET
  display_name      = EXCLUDED.display_name,
  strategy          = EXCLUDED.strategy,
  starting_bankroll = EXCLUDED.starting_bankroll,
  -- current_bankroll deliberately NOT updated here: step 4b derives it from the
  -- ledger, and re-running this migration must not reset it to the 1000 seed.
  is_active         = EXCLUDED.is_active,
  maturity_label    = EXCLUDED.maturity_label,
  show_on_picks     = EXCLUDED.show_on_picks;

-- ---------------------------------------------------------------------------
-- 4. Re-attribute history by market, with a hard assertion on both sides.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  v_old uuid;
  v_1x2 uuid;
  v_ou  uuid;
  n_sim_before  int; n_sim_1x2 int; n_sim_ou int; n_sim_left int;
  n_shd_before  int; n_shd_1x2 int; n_shd_ou int; n_shd_left int;
BEGIN
  SELECT id INTO v_old FROM bots WHERE name = 'bot_v10_all';
  SELECT id INTO v_1x2 FROM bots WHERE name = 'bot_v10_1x2';
  SELECT id INTO v_ou  FROM bots WHERE name = 'bot_v10_ou';

  IF v_old IS NULL THEN
    RAISE NOTICE 'bot_v10_all absent -- already split, nothing to re-attribute';
    RETURN;
  END IF;

  SELECT count(*) INTO n_sim_before FROM simulated_bets WHERE bot_id = v_old;
  SELECT count(*) INTO n_shd_before FROM shadow_bets    WHERE bot_id = v_old;

  UPDATE simulated_bets SET bot_id = v_1x2 WHERE bot_id = v_old AND market = '1x2';
  GET DIAGNOSTICS n_sim_1x2 = ROW_COUNT;
  UPDATE simulated_bets SET bot_id = v_ou  WHERE bot_id = v_old AND market = 'over_under_25';
  GET DIAGNOSTICS n_sim_ou = ROW_COUNT;

  UPDATE shadow_bets SET bot_id = v_1x2 WHERE bot_id = v_old AND market = '1x2';
  GET DIAGNOSTICS n_shd_1x2 = ROW_COUNT;
  UPDATE shadow_bets SET bot_id = v_ou  WHERE bot_id = v_old AND market = 'over_under_25';
  GET DIAGNOSTICS n_shd_ou = ROW_COUNT;

  SELECT count(*) INTO n_sim_left FROM simulated_bets WHERE bot_id = v_old;
  SELECT count(*) INTO n_shd_left FROM shadow_bets    WHERE bot_id = v_old;

  -- The assertion that makes this migration safe to run unattended. A row left
  -- behind means bot_v10_all traded a market this split does not know about, and
  -- silently orphaning it is exactly how the 2026-09 audits lost history.
  IF n_sim_left <> 0 OR n_shd_left <> 0 THEN
    RAISE EXCEPTION
      'V10 split incomplete: % simulated_bets and % shadow_bets remain on '
      'bot_v10_all in markets other than 1x2/over_under_25. Extend this migration '
      'rather than dropping them.', n_sim_left, n_shd_left;
  END IF;

  IF n_sim_1x2 + n_sim_ou <> n_sim_before OR n_shd_1x2 + n_shd_ou <> n_shd_before THEN
    RAISE EXCEPTION 'V10 split lost rows: sim %+% <> %, shadow %+% <> %',
      n_sim_1x2, n_sim_ou, n_sim_before, n_shd_1x2, n_shd_ou, n_shd_before;
  END IF;

  RAISE NOTICE 'V10 split: simulated_bets % -> 1x2 % / ou %; shadow_bets % -> 1x2 % / ou %',
    n_sim_before, n_sim_1x2, n_sim_ou, n_shd_before, n_shd_1x2, n_shd_ou;

  -- 5. Retire the parent. The ROW stays: a dozen migration headers and every
  --    historical analysis reference it by name, and `retired_at` is how the
  --    frontend already hides a bot (bot-aggregates.ts filters `!b.retiredAt`).
  UPDATE bots
     SET is_active     = false,
         retired_at    = now(),
         maturity_label = 'retired',
         show_on_picks = false,
         display_name  = 'Match result + Goals (split 2026-09-22)',
         retired_reason =
           'SPLIT by migration 375 into bot_v10_1x2 (calibrated, CLV +2.50% n=335) '
           'and bot_v10_ou (beta, CLV -3.85% n=181). Not a retirement on '
           'performance -- every row it owned was re-attributed by market, so its '
           'record lives on in the two halves. Nothing should write to this bot '
           'again; it is removed from daily_pipeline_v2.BOTS_CONFIG in the same '
           'commit.'
   WHERE id = v_old;
END $$;

-- ---------------------------------------------------------------------------
-- 4b. Recompute the bankroll columns the split just invalidated.
--
-- `bots.current_bankroll` has FOUR consumers and they are not all cosmetic:
--   * daily_pipeline_v2.py:2597 loads it as the live STAKING baseline
--   * settlement.py:3667 read-modify-writes it, so today's value is permanent
--   * /performance renders it per bot for elite readers
--   * smoke BOT-BANKROLL-DRIFT asserts starting + sum(pnl) within EUR0.50
-- Leaving the parent's EUR1347.26 on one half and a EUR1000 seed on the other
-- would fail that test AND mis-size real stakes.
--
-- `simulated_bets.bankroll_after` is rebuilt as a per-bot running total ordered
-- by pick_time. Neither equity chart reads it -- both recompute from `pnl`
-- (BOT-MODAL-CHART-VOID-BUG, 2026-06-06) -- but it IS shipped in the elite
-- payload (engine-data.ts:1592), and a column that ships must not be nonsense.
-- Voids carry pnl 0 and so leave the series flat, which is correct.
-- ---------------------------------------------------------------------------
UPDATE bots b
   SET current_bankroll = b.starting_bankroll + COALESCE((
         SELECT sum(s.pnl) FROM simulated_bets s
          WHERE s.bot_id = b.id AND s.result IN ('won', 'lost')), 0)
 WHERE b.name IN ('bot_v10_1x2', 'bot_v10_ou', 'bot_v10_all');

WITH running AS (
  SELECT s.id,
         b.starting_bankroll + sum(COALESCE(s.pnl, 0))
           OVER (PARTITION BY s.bot_id ORDER BY s.pick_time, s.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS ba
    FROM simulated_bets s
    JOIN bots b ON b.id = s.bot_id
   WHERE b.name IN ('bot_v10_1x2', 'bot_v10_ou')
)
UPDATE simulated_bets s SET bankroll_after = r.ba
  FROM running r WHERE r.id = s.id;

-- ---------------------------------------------------------------------------
-- 6. Display names for the rest of the fleet, and the real `testing` label.
--
--    NAMING RULE: the /performance method chip (MODEL / SHARP LINE / CONSENSUS)
--    already tells a reader what a bot prices AGAINST. So the name says what it
--    BETS. A display name that repeats the chip spends the only line a reader
--    actually reads on information already on screen.
-- ---------------------------------------------------------------------------
UPDATE bots SET display_name = v.dn FROM (VALUES
  ('bot_high_roi_global_v2',           'High-odds match result'),
  ('bot_sharp_forward_test_v1',        'Sharp-line picks'),
  ('bot_consensus_anchor_v1',          'Consensus picks'),
  ('bot_unified_gate_1x2_paper_v1',    'Unified-gate instrument'),
  ('bot_ou35_model_v1',                'Goals over/under 3.5'),
  ('bot_coolbet_1x2_model_v1',         'Coolbet match result'),
  ('bot_coolbet_ou_model_v1',          'Coolbet goals over/under'),
  ('bot_coolbet_trigger_sharp_1x2_v1', 'Coolbet match result (sharp trigger)'),
  ('bot_coolbet_trigger_sharp_ou_v1',  'Coolbet goals (sharp trigger)'),
  ('bot_unibet_trigger_sharp_1x2_v1',  'Unibet match result (sharp trigger)'),
  ('bot_unibet_trigger_sharp_ou_v1',   'Unibet goals (sharp trigger)'),
  ('bot_trigger_1x2_sharp_v1',         'Match result (sharp trigger, all books)'),
  ('bot_trigger_ou_sharp_v1',          'Goals (sharp trigger, all books)'),
  ('bot_trigger_1x2_sharp_tight_v1',   'Match result (sharp trigger, tight)'),
  ('bot_inplay_slowstate_v1',          'In-play slow state'),
  ('bot_inplay_slowstate_afctl_v1',    'In-play slow state (control)')
) AS v(nm, dn) WHERE bots.name = v.nm;

-- The two bots whose picks readers actually RECEIVE. Their ledger is
-- `picks_forward_test`, not simulated_bets, and /performance injects them after
-- the maturity filter -- so this label is what the page should READ rather than
-- hardcode at page.tsx:441.
UPDATE bots SET maturity_label = 'testing'
 WHERE name IN ('bot_sharp_forward_test_v1', 'bot_consensus_anchor_v1');

COMMIT;
