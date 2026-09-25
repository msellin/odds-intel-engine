-- 446_bot_public_record.sql — [[#157]] PERFORMANCE-RETIRED-BOTS-AND-TOTALS (2026-09-25, owner-approved shape)
--
-- WHY. /performance showed ~450 picks (the two headline bots) while we have actually tested ~16,900
-- unique picks across ~95 strategies since May. The owner wants the page to show that work — and to
-- show that the bots we keep running are the proven ones — WITHOUT letting retired losers (or winners)
-- leak into the active ROI. Three separate, labelled things:
--   1. WORK DONE  (bot_public_work_done, 1 row)  every strategy ever scored, retired included: picks,
--      settled, distinct selections, strategies / retired / public / experimental counts. Retired picks
--      keep counting here (handover rule 3: anything recorded is counted).
--   2. ACTIVE ROI  — unchanged: the BETA + CALIBRATED headline (engine-data getPublicCohortBotNames,
--      retired_at IS NULL). Nothing in this migration feeds it.
--   3. RETIRED     (bot_public_record_group + bot_public_record.is_representative) a COLLAPSED section:
--      one row per FAMILY that has retired bots, aggregating EVERY retired bot in it (losers included,
--      so nothing is hidden), plus up to 2 representative bots per family chosen by a rule that never
--      looks at ROI or CLV (below). Owner: "we don't need to add all of the bots — the research is also
--      to find what is most useful to add".
--
-- ONE COMPUTATION. Every ROI / CLV here is bot_performance's (#159, migration 433): roi_public = flat
-- 1-unit return at the best price AVAILABLE AT PICK TIME on all publishable books (owner answer 2,
-- 2026-09-25); CLV = sharp-anchor close. A family's ROI is Σ pnl_units_public / Σ settled over its bots'
-- bot_performance rows and its CLV the clv_n-weighted mean of clv_public — a composition of the same
-- per-bot numbers, never a second ROI path. Smoke PERFORMANCE-RETIRED-PARITY pins both.
--
-- FAMILIES (public_group). Frozen by a rule on the bot's name, exported family and the markets it
-- actually picked (bot_ledger), because 15 bots carry family 'unknown' in bot_config and the retired
-- ones will never be re-exported. Order matters (first match wins):
--   inplay         in-play (inplay_%, bot_inplay_%, family inplay)            — no CLV basis (§14)
--   forward_test   the pre-registered published forward test (+ its junk control)
--   acca_legs      accumulator legs
--   new_markets    corners / team totals / first half                          — markets we cannot yet publish
--   trigger_sharp  soft-book trigger bots whose fair value is the SHARP line
--   trigger_model  soft-book trigger bots whose fair value is OUR MODEL
--   sharp_lineshop sharp-generator bots (price beats de-vigged Pinnacle / consensus)
--   model_goals    model bots picking only goals markets (O/U, BTTS)
--   model_result   every other model bot (1X2, double chance, handicap, DNB, mixed)
-- The trigger_model vs trigger_sharp split IS the family lesson (owner answer 3): same fixtures, same
-- soft-book prices, opposite verdict — the anchor, not the book, separates them. Shown as ONE family
-- line; no experimental bot's own record is published.
--
-- REPRESENTATIVES (fixed rule, stated before looking — research data/models/_research/perf157/):
-- retired · >= 150 settled · a sharp-anchor close on >= 50% of settled legs (in-play exempt: it has
-- no CLV basis) · at most 2 per family, largest sample first, ties by name. Never by ROI or CLV.
--
-- DATA-QUALITY FLAGS (counts, never exclusions — the reader sees them beside the record):
--   n_swap_window  model 1X2-derived legs (model_result / trigger_model; 1x2, DC, DNB, AH) picked
--                  2026-05-10 .. 2026-09-14 while the served 1X2 model was partly home/away-swapped
--                  (#065, fix 50ec7347). bot_v10_1x2's detail view prints its count (owner answer 1).
--   n_ou_calbug    model O/U legs picked 2026-09-03 .. 2026-09-13 (OU-CALIBRATOR-DOMAIN-MISMATCH).
--   n_pre_mid_july legs picked before 2026-07-15, when Pinnacle O/U closes were mostly the opening
--                  row (ANALYSIS_GOTCHAS §83) — their CLV is weaker evidence.
--
-- ALSO (section 0 below): in-play legs are priced at their recorded in-play odds in bot_ledger (basis
-- 'inplay') and their stored pnl re-restated — found while building the retired section, where the
-- pre-match quote a pre-#159 backfill had left on them read inplay_e at +22.9% instead of +3.4%.
--
-- All three views PRIVATE (service_role only, #072 / migration 404); /performance reads them through the
-- server-side service client (odds-intel-web src/lib/performance-work-done.ts).

BEGIN;

-- ── (0) IN-PLAY PRICE FIX (found building this; root cause, one computation) ─────────────────────
-- bot_ledger priced an in-play SIM leg at odds_at_pick_available / odds_at_pick_live like a pre-match
-- one. The producer has skipped in-play legs since #159 (pick_price._PREMATCH), but a pre-#159 live
-- backfill had already written odds_at_pick_live on 1,104 of the 1,490 settled in-play sim legs — a PRE-MATCH
-- quote for a pick made at minute 35 (a different market, ANALYSIS_GOTCHAS §14). Measured: inplay_e
-- read +22.9% "public" ROI against +1.9% at its own recorded in-play odds; inplay_c −64.5% vs −7.5%.
-- Fix: an in-play leg's public AND own price is its recorded in-play odds, basis 'inplay' (not
-- 'recorded' — that label means "no quote at pick time" and is flagged). Stored simulated_bets.pnl
-- (restated at the public price by 441) is re-restated for those legs so stored pnl == 10 ×
-- pnl_unit_public still holds (smoke ONE-ROI-CLV-PARITY), bankroll_after re-sequenced for the
-- affected bots, current_bankroll = starting + Σ pnl. Shadow in-play legs get the same rule
-- (they were already at ratio 1.0 — the guard just makes it explicit). No headline number moves:
-- in-play bots are never in the headline.

ALTER TABLE public.simulated_bets DROP CONSTRAINT IF EXISTS simulated_bets_pnl_price_basis_chk;
ALTER TABLE public.simulated_bets ADD CONSTRAINT simulated_bets_pnl_price_basis_chk
  CHECK (pnl_price_basis IS NULL OR pnl_price_basis IN ('available', 'our_books', 'recorded', 'inplay'));

CREATE OR REPLACE VIEW public.bot_ledger AS
 SELECT 'sim'::text AS source,
    s.id AS pick_id,
    b.name AS bot_name,
    s.bot_id,
    s.match_id,
    m.date AS kickoff,
    s.pick_time,
    s.market,
    s.selection,
    s.odds_at_pick AS odds,
    s.recommended_bookmaker AS bookmaker,
    s.result::text AS result,
        CASE s.result::text
            WHEN 'won'::text THEN s.odds_at_pick - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    s.clv AS clv_raw,
    NULL::numeric AS clv_mc,
    s.clv_pinnacle_devig::numeric AS clv_pinnacle,
    s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text AS is_inplay,
    s.model_version,
    NULL::text AS rule_version,
    -- ── [[#159]] appended ──
    x.odds_public,
    x.public_basis,
    CASE s.result::text WHEN 'won' THEN x.odds_public - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
    CASE s.result::text WHEN 'won' THEN x.odds_own - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_own,
    s.stake::numeric AS stake,
    CASE s.result::text WHEN 'won' THEN (x.odds_public - 1) * s.stake WHEN 'lost' THEN -s.stake ELSE 0::numeric END AS pnl_staked_public,
    x.odds_public * a.p_close - 1 AS clv_anchor_public,
    x.odds_own * a.p_close - 1 AS clv_anchor_own,
    a.anchor_source AS clv_anchor_source,
    true AS in_record,
    'counted'::text AS record_state,
    NULL::text AS record_rule_version,
    COALESCE(s.calibrated_prob, s.model_probability)::numeric AS model_prob,
    s.edge_percent::numeric AS edge,
    s.strategy_profile
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     CROSS JOIN LATERAL (SELECT (s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL
                                 OR b.name ~~ 'inplay\_%'::text) AS inplay) ip
     CROSS JOIN LATERAL (SELECT
        -- [[#157]] an IN-PLAY leg is priced at its recorded on-screen in-play odds: the
        -- odds_at_pick_live a pre-#159 backfill left on 1,104 of them is a PRE-MATCH quote
        -- (a different market, ANALYSIS_GOTCHAS §14) — it read inplay_e at +22.9% vs +1.9%.
        CASE WHEN ip.inplay THEN s.odds_at_pick
             WHEN s.odds_at_pick_available > 1 THEN s.odds_at_pick_available
             WHEN s.odds_at_pick_live > 1 THEN s.odds_at_pick_live ELSE s.odds_at_pick END AS odds_public,
        CASE WHEN ip.inplay THEN 'inplay'
             WHEN s.odds_at_pick_available > 1 THEN 'available'
             WHEN s.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS public_basis,
        CASE WHEN ip.inplay THEN s.odds_at_pick
             WHEN s.odds_at_pick_live > 1 THEN s.odds_at_pick_live ELSE s.odds_at_pick END AS odds_own,
        CASE WHEN ip.inplay THEN 'inplay'
             WHEN s.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets' AND c.leg_id = s.id
     CROSS JOIN LATERAL (SELECT
        CASE WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL
             WHEN c.status = 'ok' THEN c.p_close WHEN c.cons_status = 'ok' THEN c.p_close_cons END AS p_close,
        CASE WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL
             WHEN c.status = 'ok' THEN 'pinnacle' WHEN c.cons_status = 'ok' THEN 'consensus' END AS anchor_source) a
  WHERE s.combo_legs IS NULL
UNION ALL
 SELECT 'shadow'::text AS source,
    sh.id AS pick_id,
    b.name AS bot_name,
    sh.bot_id,
    sh.match_id,
    m.date AS kickoff,
    sh.pick_time,
    sh.market,
    sh.selection,
    sh.odds_at_pick AS odds,
    sh.recommended_bookmaker AS bookmaker,
    sh.result::text AS result,
        CASE sh.result::text
            WHEN 'won'::text THEN sh.odds_at_pick - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    sh.clv AS clv_raw,
    sh.clv_margin_corrected AS clv_mc,
    sh.clv_pinnacle::numeric AS clv_pinnacle,
    sh.inplay_minute IS NOT NULL AS is_inplay,
    sh.model_version,
    NULL::text AS rule_version,
    x.odds_public,
    x.public_basis,
    CASE sh.result::text WHEN 'won' THEN x.odds_public - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
    CASE sh.result::text WHEN 'won' THEN x.odds_own - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_own,
    sh.stake::numeric AS stake,
    CASE sh.result::text WHEN 'won' THEN (x.odds_public - 1) * sh.stake WHEN 'lost' THEN -sh.stake ELSE 0::numeric END AS pnl_staked_public,
    x.odds_public * a.p_close - 1 AS clv_anchor_public,
    x.odds_own * a.p_close - 1 AS clv_anchor_own,
    a.anchor_source AS clv_anchor_source,
    true AS in_record,
    'counted'::text AS record_state,
    NULL::text AS record_rule_version,
    COALESCE(sh.calibrated_prob, sh.model_probability)::numeric AS model_prob,
    sh.edge_percent::numeric AS edge,
    sh.strategy_profile
   FROM ( SELECT DISTINCT ON (x_1.bot_id, x_1.match_id, x_1.market, x_1.selection) x_1.id,
            x_1.shadow_run_id,
            x_1.shadow_cohort,
            x_1.bot_id,
            x_1.match_id,
            x_1.market,
            x_1.selection,
            x_1.odds_at_pick,
            x_1.pick_time,
            x_1.stake,
            x_1.model_probability,
            x_1.calibrated_prob,
            x_1.edge_percent,
            x_1.recommended_bookmaker,
            x_1.kelly_fraction,
            x_1.timing_cohort,
            x_1.model_version,
            x_1.closing_odds,
            x_1.clv,
            x_1.result,
            x_1.pnl,
            x_1.created_at,
            x_1.meta_clv_score,
            x_1.strategy_profile,
            x_1.void_reason,
            x_1.clv_pinnacle,
            x_1.closing_bookmaker,
            x_1.pair_gap_hours,
            x_1.odds_at_pick_live,
            x_1.odds_at_pick_available,
            x_1.clv_live,
            x_1.clv_pinnacle_live,
            x_1.closing_margin,
            x_1.clv_margin_corrected,
            x_1.decision_quote_age_min,
            x_1.inplay_minute,
            x_1.inplay_score_home,
            x_1.inplay_score_away,
            x_1.closing_minutes_before_ko
           FROM shadow_bets x_1
          WHERE NOT (EXISTS ( SELECT 1
                   FROM simulated_bets s2
                  WHERE s2.bot_id = x_1.bot_id))
          ORDER BY x_1.bot_id, x_1.match_id, x_1.market, x_1.selection, x_1.pick_time) sh
     JOIN bots b ON b.id = sh.bot_id
     JOIN matches m ON m.id = sh.match_id
     CROSS JOIN LATERAL (SELECT
        CASE WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
             WHEN sh.odds_at_pick_available > 1 THEN sh.odds_at_pick_available
             WHEN sh.odds_at_pick_live > 1 THEN sh.odds_at_pick_live ELSE sh.odds_at_pick END AS odds_public,
        CASE WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'
             WHEN sh.odds_at_pick_available > 1 THEN 'available'
             WHEN sh.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS public_basis,
        CASE WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
             WHEN sh.odds_at_pick_live > 1 THEN sh.odds_at_pick_live ELSE sh.odds_at_pick END AS odds_own,
        CASE WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'
             WHEN sh.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets' AND c.leg_id = sh.id
     CROSS JOIN LATERAL (SELECT
        CASE WHEN sh.inplay_minute IS NOT NULL THEN NULL
             WHEN c.status = 'ok' THEN c.p_close WHEN c.cons_status = 'ok' THEN c.p_close_cons END AS p_close,
        CASE WHEN sh.inplay_minute IS NOT NULL THEN NULL
             WHEN c.status = 'ok' THEN 'pinnacle' WHEN c.cons_status = 'ok' THEN 'consensus' END AS anchor_source) a
UNION ALL
 SELECT 'forward_test'::text AS source,
    p.id AS pick_id,
        CASE
            WHEN p.arm = 'junk_anchor'::text THEN 'control_junk_anchor'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
            WHEN p.arm = 'consensus_anchor'::text THEN 'bot_consensus_b_v1'::text
            WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
            ELSE 'bot_sharp_1x2_v1'::text
        END AS bot_name,
    NULL::uuid AS bot_id,
    p.match_id,
    m.date AS kickoff,
    p.published_at AS pick_time,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
        CASE
            WHEN p.outcome IS NULL THEN 'pending'::text
            ELSE p.outcome
        END AS result,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    p.clv AS clv_raw,
    p.clv_margin_corrected AS clv_mc,
    NULL::numeric AS clv_pinnacle,
    false AS is_inplay,
    NULL::text AS model_version,
    p.rule_version,
    p.odds AS odds_public,
    'published'::text AS public_basis,
    CASE p.outcome WHEN 'won' THEN p.odds - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_public,
    p.odds AS odds_own,
    'published'::text AS own_basis,
    CASE p.outcome WHEN 'won' THEN p.odds - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_unit_own,
    1::numeric AS stake,
    CASE p.outcome WHEN 'won' THEN p.odds - 1 WHEN 'lost' THEN -1::numeric ELSE 0::numeric END AS pnl_staked_public,
    p.odds * (CASE WHEN c.status = 'ok' THEN c.p_close WHEN c.cons_status = 'ok' THEN c.p_close_cons END) - 1 AS clv_anchor_public,
    p.odds * (CASE WHEN c.status = 'ok' THEN c.p_close WHEN c.cons_status = 'ok' THEN c.p_close_cons END) - 1 AS clv_anchor_own,
    CASE WHEN c.status = 'ok' THEN 'pinnacle' WHEN c.cons_status = 'ok' THEN 'consensus' END AS clv_anchor_source,
    -- [[#158]] record for the published arms; the junk control scores its latest rule.
    CASE WHEN p.arm = 'junk_anchor' THEN p.rule_version = jr.rule_version
         ELSE COALESCE(r.record_state IN ('native', 'rechecked_pass'), false) END AS in_record,
    CASE WHEN p.arm = 'junk_anchor' THEN CASE WHEN p.rule_version = jr.rule_version THEN 'native' ELSE 'earlier' END
         ELSE COALESCE(r.record_state, 'unsent') END AS record_state,
    CASE WHEN p.arm = 'junk_anchor' THEN jr.rule_version ELSE r.current_rule_version END AS record_rule_version,
    p.p_sharp AS model_prob,
    p.edge,
    NULL::text AS strategy_profile
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = p.id
     LEFT JOIN LATERAL (SELECT j.rule_version FROM picks_forward_test j
                         WHERE j.arm = 'junk_anchor' ORDER BY j.published_at DESC LIMIT 1) jr ON true
  WHERE p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text, 'junk_anchor'::text]);

UPDATE public.simulated_bets s
   SET pnl = CASE s.result::text WHEN 'won' THEN round((s.odds_at_pick - 1) * 10, 2)
                                 WHEN 'lost' THEN -10 ELSE 0 END,
       pnl_price_basis = 'inplay'
  FROM public.bots b
 WHERE b.id = s.bot_id AND s.combo_legs IS NULL
   AND s.result::text IN ('won', 'lost', 'void')
   AND (s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name LIKE 'inplay\_%');

UPDATE public.simulated_bets s
   SET bankroll_after = r.running
  FROM (SELECT sb.id,
               b.starting_bankroll + SUM(sb.pnl) OVER (PARTITION BY sb.bot_id ORDER BY sb.pick_time, sb.id
                                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running
          FROM public.simulated_bets sb JOIN public.bots b ON b.id = sb.bot_id
         WHERE sb.result::text IN ('won', 'lost', 'void')
           AND sb.bot_id IN (SELECT DISTINCT bot_id FROM public.simulated_bets WHERE pnl_price_basis = 'inplay')) r
 WHERE r.id = s.id;

UPDATE public.bots b
   SET current_bankroll = b.starting_bankroll + t.pnl
  FROM (SELECT bot_id, COALESCE(SUM(pnl) FILTER (WHERE result::text IN ('won', 'lost')), 0) AS pnl
          FROM public.simulated_bets
         WHERE bot_id IN (SELECT DISTINCT bot_id FROM public.simulated_bets WHERE pnl_price_basis = 'inplay')
         GROUP BY bot_id) t
 WHERE t.bot_id = b.id;

DO $$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n FROM public.bot_ledger l JOIN public.simulated_bets s ON s.id = l.pick_id
   WHERE l.source = 'sim' AND l.result IN ('won', 'lost', 'void')
     AND abs(s.pnl - 10 * l.pnl_unit_public) > 0.006;
  IF n > 0 THEN RAISE EXCEPTION '446: % legs where stored pnl <> 10 x pnl_unit_public', n; END IF;
END $$;

COMMENT ON VIEW public.bot_ledger IS
  '#139 unified ledger (one row per leg, every bot). #159: + pnl_unit_public (flat, best price available at pick time, all books) / pnl_unit_own (flat, at our books), clv_anchor_public / clv_anchor_own (sharp-anchor close, #156), in_record (#158 record). #157: in-play legs priced at their recorded in-play odds on both bases (basis inplay). pnl_unit / clv_raw / clv_pinnacle are LEGACY (recorded price, no close-age limit) — never a basis for a shown ROI or CLV. PRIVATE (service_role).';

CREATE OR REPLACE FUNCTION public.bot_public_group(p_bot text, p_family text, p_markets text[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_bot LIKE 'inplay\_%' OR p_bot LIKE 'bot\_inplay\_%' OR p_family = 'inplay'        THEN 'inplay'
    WHEN p_family IN ('forward_test', 'control') OR p_bot = 'control_junk_anchor'            THEN 'forward_test'
    WHEN p_bot LIKE '%acca%'                                                                 THEN 'acca_legs'
    WHEN EXISTS (SELECT 1 FROM unnest(p_markets) m
                  WHERE m LIKE 'corners%' OR m LIKE 'team\_total%' OR m LIKE '%\_1h')         THEN 'new_markets'
    WHEN p_bot LIKE '%trigger%' AND p_family IN ('sharp_trigger', 'sharp_generator')         THEN 'trigger_sharp'
    WHEN p_bot LIKE '%trigger%'                                                              THEN 'trigger_model'
    WHEN p_family = 'sharp_generator' OR p_bot LIKE 'bot\_pin\_%'                            THEN 'sharp_lineshop'
    WHEN p_markets IS NOT NULL AND cardinality(p_markets) > 0
         AND NOT EXISTS (SELECT 1 FROM unnest(p_markets) m
                          WHERE NOT (m LIKE 'over\_under%' OR m IN ('o/u', 'btts')))         THEN 'model_goals'
    ELSE 'model_result'
  END
$$;

COMMENT ON FUNCTION public.bot_public_group(text, text, text[]) IS
  '#157 the /performance family of a bot (rule order in migration 446). Frozen rule: retired bots are never re-exported.';

CREATE OR REPLACE VIEW public.bot_public_record AS
WITH legs AS (
    SELECT bot_name, market, pick_time FROM bot_ledger WHERE in_record
), mk AS (
    SELECT bot_name, array_agg(DISTINCT market ORDER BY market) AS markets FROM legs GROUP BY bot_name
), g AS (
    SELECT p.bot_name,
           public.bot_public_group(p.bot_name, c.family, mk.markets) AS public_group,
           mk.markets
      FROM bot_performance p
      LEFT JOIN bot_config c ON c.bot_name = p.bot_name
      LEFT JOIN mk ON mk.bot_name = p.bot_name
), fl AS (
    SELECT l.bot_name,
           count(*) FILTER (WHERE g.public_group IN ('model_result', 'trigger_model')
                              AND l.market IN ('1x2', 'double_chance', 'draw_no_bet', 'asian_handicap')
                              AND l.pick_time >= '2026-05-10' AND l.pick_time < '2026-09-15')   AS n_swap_window,
           count(*) FILTER (WHERE g.public_group IN ('model_goals', 'trigger_model')
                              AND (l.market LIKE 'over\_under%' OR l.market = 'o/u')
                              AND l.pick_time >= '2026-09-03' AND l.pick_time < '2026-09-14')   AS n_ou_calbug,
           count(*) FILTER (WHERE l.pick_time < '2026-07-15')                                  AS n_pre_mid_july
      FROM legs l JOIN g ON g.bot_name = l.bot_name
     GROUP BY l.bot_name
), r AS (
    SELECT p.*,
           b.display_name,
           (b.retired_at IS NOT NULL)                                  AS is_retired,
           b.retired_at,
           b.retired_reason,
           coalesce(b.maturity_label, 'experimental')                  AS maturity_label,
           coalesce(b.vip, false)                                      AS vip,
           g.public_group,
           g.markets,
           coalesce(fl.n_swap_window, 0)                               AS n_swap_window,
           coalesce(fl.n_ou_calbug, 0)                                 AS n_ou_calbug,
           coalesce(fl.n_pre_mid_july, 0)                              AS n_pre_mid_july
      FROM bot_performance p
      JOIN g ON g.bot_name = p.bot_name
      LEFT JOIN fl ON fl.bot_name = p.bot_name
      LEFT JOIN bots b ON b.name = p.bot_name
), rep AS (
    SELECT bot_name,
           row_number() OVER (PARTITION BY public_group ORDER BY settled DESC, bot_name) AS rep_rank
      FROM r
     WHERE is_retired AND settled >= 150
       AND (public_group = 'inplay' OR clv_n >= 0.5 * settled)
)
SELECT r.bot_name, r.display_name, r.public_group,
       CASE r.public_group
         WHEN 'model_result'   THEN 'Model — match result (1X2, double chance, handicap)'
         WHEN 'model_goals'    THEN 'Model — goals (over/under, both teams to score)'
         WHEN 'sharp_lineshop' THEN 'Sharp line-shopping (a book beats the sharp line)'
         WHEN 'trigger_model'  THEN 'Soft-book triggers vs OUR MODEL'
         WHEN 'trigger_sharp'  THEN 'Soft-book triggers vs the SHARP line'
         WHEN 'forward_test'   THEN 'Pre-registered forward test'
         WHEN 'new_markets'    THEN 'New markets (corners, team totals, first half)'
         WHEN 'inplay'         THEN 'In-play'
         WHEN 'acca_legs'      THEN 'Accumulator legs'
       END                                                             AS public_group_title,
       r.is_retired, r.retired_at, r.retired_reason, r.maturity_label, r.vip, r.markets,
       r.picks_total, r.pending, r.settled, r.won, r.lost, r.void,
       r.pnl_units_public, r.roi_public, r.n_public_recorded,
       r.clv_public, r.clv_n, r.clv_n_pinnacle, r.clv_n_consensus,
       r.first_pick_at, r.last_pick_at,
       r.n_swap_window, r.n_ou_calbug, r.n_pre_mid_july,
       coalesce(rep.rep_rank <= 2, false)                              AS is_representative
  FROM r LEFT JOIN rep ON rep.bot_name = r.bot_name;

COMMENT ON VIEW public.bot_public_record IS
  '#157 one row per bot in bot_performance: its family (bot_public_group), retired flag, the SAME roi_public / clv_public as bot_performance, data-quality counts (swap window #065, O/U calibration bug, pre-mid-July §83) and is_representative (retired, >=150 settled, >=50% sharp-anchor close unless in-play, <=2 per family by sample — never by ROI). PRIVATE (service_role).';

CREATE OR REPLACE VIEW public.bot_public_record_group AS
SELECT public_group,
       max(public_group_title)                                                     AS public_group_title,
       is_retired,
       count(*)                                                                    AS bots,
       sum(picks_total)                                                            AS picks_total,
       sum(settled)                                                                AS settled,
       sum(won)                                                                    AS won,
       sum(lost)                                                                   AS lost,
       sum(pnl_units_public)                                                       AS pnl_units_public,
       CASE WHEN sum(settled) > 0 THEN round(sum(pnl_units_public) / sum(settled), 6) END AS roi_public,
       sum(clv_n)                                                                  AS clv_n,
       CASE WHEN sum(clv_n) > 0
            THEN sum(clv_public * clv_n) FILTER (WHERE clv_n > 0) / sum(clv_n) END  AS clv_public,
       sum(n_swap_window)                                                          AS n_swap_window,
       sum(n_ou_calbug)                                                            AS n_ou_calbug,
       sum(n_pre_mid_july)                                                         AS n_pre_mid_july,
       min(first_pick_at)                                                          AS first_pick_at,
       max(last_pick_at)                                                           AS last_pick_at,
       max(retired_at)                                                             AS last_retired_at
  FROM bot_public_record
 GROUP BY public_group, is_retired;

COMMENT ON VIEW public.bot_public_record_group IS
  '#157 per family x retired: sums of bot_public_record (= bot_performance) — ROI = sum pnl_units_public / sum settled, CLV = clv_n-weighted clv_public. The /performance retired section reads is_retired rows; the family lesson reads trigger_model vs trigger_sharp (both states). PRIVATE (service_role).';

CREATE OR REPLACE VIEW public.bot_public_work_done AS
WITH r AS MATERIALIZED (SELECT * FROM bot_public_record),
     sel AS (SELECT count(DISTINCT (match_id, market, selection)) AS n FROM bot_ledger WHERE in_record)
SELECT count(*)                                                                             AS strategies,
       count(*) FILTER (WHERE is_retired)                                                   AS retired,
       count(*) FILTER (WHERE NOT is_retired AND maturity_label IN ('testing', 'beta', 'calibrated')) AS public_active,
       count(*) FILTER (WHERE NOT is_retired AND maturity_label NOT IN ('testing', 'beta', 'calibrated')) AS not_public_active,
       sum(picks_total)                                                                     AS picks_total,
       sum(settled)                                                                         AS settled,
       sum(picks_total) FILTER (WHERE is_retired)                                           AS retired_picks,
       max(sel.n)                                                                           AS distinct_selections,
       min(first_pick_at)                                                                   AS since,
       -- the family lesson (owner answer 3): soft-book triggers, model anchor vs sharp anchor, every
       -- bot in each family (retired + experimental), family level only — no bot's own record
       sum(clv_public * clv_n) FILTER (WHERE public_group = 'trigger_model' AND clv_n > 0)
         / nullif(sum(clv_n) FILTER (WHERE public_group = 'trigger_model'), 0)             AS lesson_model_clv,
       sum(clv_n) FILTER (WHERE public_group = 'trigger_model')                             AS lesson_model_n,
       sum(clv_public * clv_n) FILTER (WHERE public_group = 'trigger_sharp' AND clv_n > 0)
         / nullif(sum(clv_n) FILTER (WHERE public_group = 'trigger_sharp'), 0)             AS lesson_sharp_clv,
       sum(clv_n) FILTER (WHERE public_group = 'trigger_sharp')                             AS lesson_sharp_n
  FROM r CROSS JOIN sel;

COMMENT ON VIEW public.bot_public_work_done IS
  '#157 the /performance "work done" line: every strategy ever scored (retired included) + the trigger anchor family lesson. Separate from the active headline ROI (BETA+CALIBRATED only). PRIVATE (service_role).';

REVOKE ALL ON public.bot_public_record, public.bot_public_record_group, public.bot_public_work_done
  FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.bot_public_record, public.bot_public_record_group, public.bot_public_work_done
  TO service_role;

COMMIT;

-- verify: (SELECT count(*) FROM bot_public_record r JOIN bot_performance p USING (bot_name) WHERE r.roi_public IS DISTINCT FROM p.roi_public OR r.settled <> p.settled) = 0
-- verify: (SELECT picks_total FROM bot_public_work_done) = (SELECT sum(picks_total) FROM bot_performance)
-- verify: (SELECT count(*) FROM bot_ledger WHERE is_inplay AND source IN ('sim', 'shadow') AND public_basis <> 'inplay') = 0
