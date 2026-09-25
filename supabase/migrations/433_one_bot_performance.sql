-- 433 — ONE ROI + CLV DEFINITION FOR EVERY BOT ON EVERY SURFACE ([[#159]], 2026-09-25, owner-approved)
--
-- WHY. The same bot read a different ROI and CLV on each page, because each page computed
-- its own from a different column:
--   * /performance (Pro)        stake-weighted P&L at our-books price (engine-data execPnl)
--   * /performance (anon/free)  dashboard_cache.bot_breakdown — the same, precomputed
--   * /admin/bots               bot_scoreboard.roi_unit — FLAT, but at the RECORDED odds_at_pick
--   * CLV everywhere            simulated_bets.clv / clv_pinnacle_devig — no close-age limit,
--                               priced at odds_at_pick, circular before mid-July (ANALYSIS_GOTCHAS §83)
-- Measured 2026-09-25 for bot_v10_1x2: +5.2% / +13.4% ROI and +7.0% / +0.8% CLV on two pages.
--
-- THE ONE DEFINITION (owner, 2026-09-25) — FLAT 1-unit stake, TWO labelled price bases:
--   PUBLIC  "best price available when the pick was made (all books)" — /performance, the hero,
--           dashboard_cache. odds_at_pick_available = latest stored quote PER BOOK at or before
--           pick_time, MAX across every PUBLISHABLE book (daily_pipeline_v2.is_publishable_book,
--           the [[#005]] set), floored at odds_at_pick_live (a real quote at pick time from a
--           subset of those books — it guards against intraday snapshots trimmed by retention).
--           Readers are not bound by our Estonian licensing (OWN vs PICKS).
--   OWN     "at our books" — /admin/bots, beside the public figure. odds_at_pick_live = the same
--           rule over ACCESSIBLE_BOOKMAKERS only (Coolbet / Epicbet / Tonybet / Unibet-Site).
--   Both from ONE producer (scripts/backfill_odds_at_pick_live.py). A leg with no quote falls
--   back to the recorded odds_at_pick and is COUNTED (n_*_recorded) — never silent.
--   Forward-test legs: the published odds on both bases. Stake-weighted ROI at the public price
--   is roi_staked — a labelled SECONDARY only.
--   CLV = SHARP-ANCHOR close (the [[#156]] definition, migration 430): the leg's price x the fresh
--         Shin-de-vigged Pinnacle close (leg_clv_sharp.p_close, status='ok'), else the >=5-book
--         consensus close (p_close_cons, cons_status='ok'), minus 1 — on each basis's price. Thin
--         3–4-book consensus EXCLUDED, |clv| > 1 a data fault, n + the Pinnacle/consensus mix
--         travel with it, never for in-play legs (ANALYSIS_GOTCHAS §14).
--   RECORD = which legs count. sim/shadow: every leg (combos out, one ledger per bot — the
--         migration 410 rules). Forward-test arms: the [[#158]] record — the arm's current rule,
--         plus earlier-rule picks that PASSED it on pick-time data (picks_forward_test_record_leg);
--         never-sent grade D out. The junk control: its latest rule.
--
-- WHAT THIS CREATES / CHANGES (views all PRIVATE — service_role only, no anon grant, #072 / 404):
--   simulated_bets, shadow_bets  + odds_at_pick_available (filled by the producer job)
--   bot_ledger          + odds_public/public_basis/pnl_unit_public, odds_own/own_basis/pnl_unit_own,
--                         stake, pnl_staked_public, clv_anchor_public, clv_anchor_own,
--                         clv_anchor_source, in_record, record_state, record_rule_version,
--                         model_prob, edge, strategy_profile                       (APPENDED)
--   bot_performance     NEW — the ONE per-bot aggregate. /performance, /admin/bots (via
--                         bot_scoreboard) and dashboard_cache.bot_breakdown all read it.
--   bot_scoreboard      re-created as a projection of bot_performance. roi_unit = the OWN flat ROI
--                         (admin's native basis), roi_public beside it; clv_pin_* (the legacy
--                         column) are GONE, replaced by clv_anchor_*; clv_mc_* stay as the
--                         labelled own-book secondary.
--   bot_weekly          re-created on the record legs; clv_pin_* -> clv_anchor_*; pnl at our books.
--   bot_market_stats    record legs, break-even at our-books price, + clv_anchor_n/mean/sd per market.
--   bot_ledger_display  + the appended bot_ledger columns + league (the /performance detail view).
--
-- DEPRECATED (do not add readers — smoke LEGACY-CLV-PNL-NO-NEW-READERS): simulated_bets.clv,
-- simulated_bets.clv_pinnacle / clv_pinnacle_devig, shadow_bets.clv_pinnacle, bot_ledger.clv_raw /
-- clv_pinnacle / pnl_unit, and stored simulated_bets.pnl as a basis for a published or admin ROI.

BEGIN;

ALTER TABLE public.simulated_bets ADD COLUMN IF NOT EXISTS odds_at_pick_available numeric;
ALTER TABLE public.shadow_bets    ADD COLUMN IF NOT EXISTS odds_at_pick_available numeric;
COMMENT ON COLUMN public.simulated_bets.odds_at_pick_available IS
  '#159 PUBLIC price basis: latest quote per PUBLISHABLE book at/before pick_time, max across books, floored at odds_at_pick_live. Producer: scripts/backfill_odds_at_pick_live.py (job_backfill_live_prices).';
COMMENT ON COLUMN public.shadow_bets.odds_at_pick_available IS
  '#159 PUBLIC price basis — see simulated_bets.odds_at_pick_available.';

-- ── bot_ledger: append the one-definition columns (CREATE OR REPLACE keeps grants + dependents) ──
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
     CROSS JOIN LATERAL (SELECT
        CASE WHEN s.odds_at_pick_available > 1 THEN s.odds_at_pick_available
             WHEN s.odds_at_pick_live > 1 THEN s.odds_at_pick_live ELSE s.odds_at_pick END AS odds_public,
        CASE WHEN s.odds_at_pick_available > 1 THEN 'available'
             WHEN s.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS public_basis,
        CASE WHEN s.odds_at_pick_live > 1 THEN s.odds_at_pick_live ELSE s.odds_at_pick END AS odds_own,
        CASE WHEN s.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS own_basis) x
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
        CASE WHEN sh.odds_at_pick_available > 1 THEN sh.odds_at_pick_available
             WHEN sh.odds_at_pick_live > 1 THEN sh.odds_at_pick_live ELSE sh.odds_at_pick END AS odds_public,
        CASE WHEN sh.odds_at_pick_available > 1 THEN 'available'
             WHEN sh.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS public_basis,
        CASE WHEN sh.odds_at_pick_live > 1 THEN sh.odds_at_pick_live ELSE sh.odds_at_pick END AS odds_own,
        CASE WHEN sh.odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS own_basis) x
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

COMMENT ON VIEW public.bot_ledger IS
  '#139 unified ledger (one row per leg, every bot). #159: + pnl_unit_public (flat, best price available at pick time, all books) / pnl_unit_own (flat, at our books), clv_anchor_public / clv_anchor_own (sharp-anchor close, #156), in_record (#158 record). pnl_unit / clv_raw / clv_pinnacle are LEGACY (recorded price, no close-age limit) — never a basis for a shown ROI or CLV. PRIVATE (service_role).';

-- ── bot_performance: THE per-bot computation ────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.bot_performance AS
WITH l AS (
    SELECT *, result = ANY (ARRAY['won'::text, 'lost'::text]) AS is_settled,
           (clv_anchor_public IS NOT NULL AND abs(clv_anchor_public) <= 1) AS clv_ok_public,
           (clv_anchor_own    IS NOT NULL AND abs(clv_anchor_own)    <= 1) AS clv_ok_own
      FROM bot_ledger
     WHERE in_record
)
SELECT bot_name,
       string_agg(DISTINCT source, '+' ORDER BY source)                                    AS source,
       max(record_rule_version)                                                             AS scored_rule_version,
       count(*)                                                                             AS picks_total,
       count(*) FILTER (WHERE result = 'pending')                                           AS pending,
       count(*) FILTER (WHERE is_settled)                                                   AS settled,
       count(*) FILTER (WHERE result = 'won')                                               AS won,
       count(*) FILTER (WHERE result = 'lost')                                              AS lost,
       count(*) FILTER (WHERE result NOT IN ('pending', 'won', 'lost'))                     AS void,
       -- PUBLIC ROI: flat 1 unit at the best price available at pick time (all books)
       coalesce(sum(pnl_unit_public) FILTER (WHERE is_settled), 0)                          AS pnl_units_public,
       CASE WHEN count(*) FILTER (WHERE is_settled) > 0
            THEN round(avg(pnl_unit_public) FILTER (WHERE is_settled), 6) END               AS roi_public,
       stddev_samp(pnl_unit_public) FILTER (WHERE is_settled)                               AS roi_public_sd,
       count(*) FILTER (WHERE is_settled AND public_basis = 'recorded')                     AS n_public_recorded,
       -- OWN ROI: flat 1 unit at our books
       coalesce(sum(pnl_unit_own) FILTER (WHERE is_settled), 0)                             AS pnl_units_own,
       CASE WHEN count(*) FILTER (WHERE is_settled) > 0
            THEN round(avg(pnl_unit_own) FILTER (WHERE is_settled), 6) END                  AS roi_own,
       count(*) FILTER (WHERE is_settled AND own_basis = 'recorded')                        AS n_own_recorded,
       -- SECONDARY: the bot's own stakes, at the public price
       sum(stake) FILTER (WHERE is_settled)                                                 AS staked,
       CASE WHEN sum(stake) FILTER (WHERE is_settled) > 0
            THEN round(sum(pnl_staked_public) FILTER (WHERE is_settled)
                       / sum(stake) FILTER (WHERE is_settled), 6) END                        AS roi_staked,
       -- CLV: sharp-anchor close on settled legs, on each basis's price
       count(*) FILTER (WHERE is_settled AND clv_ok_public)                                 AS clv_n,
       count(*) FILTER (WHERE is_settled AND clv_ok_public AND clv_anchor_source = 'pinnacle')  AS clv_n_pinnacle,
       count(*) FILTER (WHERE is_settled AND clv_ok_public AND clv_anchor_source = 'consensus') AS clv_n_consensus,
       avg(clv_anchor_public) FILTER (WHERE is_settled AND clv_ok_public)                   AS clv_public,
       stddev_samp(clv_anchor_public) FILTER (WHERE is_settled AND clv_ok_public)           AS clv_public_sd,
       count(*) FILTER (WHERE is_settled AND clv_ok_own)                                    AS clv_own_n,
       avg(clv_anchor_own) FILTER (WHERE is_settled AND clv_ok_own)                         AS clv_own,
       stddev_samp(clv_anchor_own) FILTER (WHERE is_settled AND clv_ok_own)                 AS clv_own_sd,
       count(*) FILTER (WHERE is_settled AND clv_anchor_own IS NOT NULL AND NOT clv_ok_own) AS clv_outlier_n,
       min(pick_time)                                                                       AS first_pick_at,
       max(pick_time)                                                                       AS last_pick_at,
       count(*) FILTER (WHERE pick_time >= now() - interval '7 days')                       AS picks_7d,
       count(*) FILTER (WHERE is_settled AND kickoff >= now() - interval '7 days')          AS settled_7d
  FROM l
 GROUP BY bot_name;

COMMENT ON VIEW public.bot_performance IS
  '#159: THE per-bot ROI + CLV. roi_public = mean flat-1-unit return at the best price available at pick time on ALL publishable books (odds_at_pick_available; /performance, hero, dashboard_cache); roi_own = the same at OUR books (odds_at_pick_live; /admin/bots, labelled "at our books"); n_*_recorded = legs priced at the recorded odds for want of a quote; roi_staked = secondary. clv_public / clv_own = sharp-anchor close (Pinnacle, else >=5-book consensus; |clv|<=1) with clv_n / clv_n_pinnacle / clv_n_consensus. No page computes its own. PRIVATE (service_role).';

-- ── bot_scoreboard: a projection of bot_performance (+ bot identity, family, own-book secondary) ──
DROP VIEW IF EXISTS public.bot_scoreboard;
CREATE VIEW public.bot_scoreboard AS
WITH mc AS (   -- own-book margin-corrected CLV: the labelled SECONDARY, never the headline
    SELECT bot_name,
           count(clv_mc) FILTER (WHERE result <> 'void' AND abs(clv_mc) <= 1)        AS clv_mc_n,
           avg(clv_mc) FILTER (WHERE result <> 'void' AND abs(clv_mc) <= 1)          AS clv_mc_mean,
           stddev_samp(clv_mc) FILTER (WHERE result <> 'void' AND abs(clv_mc) <= 1)  AS clv_mc_sd,
           count(*) FILTER (WHERE result <> 'void' AND abs(clv_mc) > 1)              AS clv_mc_outlier_n
      FROM bot_ledger WHERE in_record GROUP BY bot_name
), older AS (
    SELECT bot_name, count(*) AS earlier_version_picks
      FROM bot_ledger WHERE source = 'forward_test' AND record_state IN ('earlier', 'rechecked_fail')
     GROUP BY bot_name
)
SELECT p.bot_name,
       b.display_name,
       p.source,
       p.scored_rule_version,
       COALESCE(o.earlier_version_picks, 0::bigint)                                  AS earlier_version_picks,
       b.is_active,
       b.retired_at,
       b.maturity_label,
       COALESCE(c.family, 'unknown'::text)                                           AS family,
       p.picks_total, p.pending, p.settled, p.won, p.lost, p.void,
       p.roi_own                                                                     AS roi_unit,
       mc.clv_mc_n,
       mc.clv_mc_mean,
       CASE WHEN mc.clv_mc_n >= 2 THEN mc.clv_mc_sd::double precision / sqrt(mc.clv_mc_n::double precision) END AS clv_mc_se,
       CASE WHEN mc.clv_mc_n >= 2 AND mc.clv_mc_sd > 0
            THEN mc.clv_mc_mean::double precision / (mc.clv_mc_sd::double precision / sqrt(mc.clv_mc_n::double precision)) END AS clv_mc_t,
       -- headline CLV on /admin/bots = sharp-anchor at OUR books' price
       p.clv_own_n                                                                   AS clv_anchor_n,
       p.clv_n_pinnacle                                                              AS clv_anchor_n_pinnacle,
       p.clv_n_consensus                                                             AS clv_anchor_n_consensus,
       p.clv_own                                                                     AS clv_anchor_mean,
       CASE WHEN p.clv_own_n >= 2 THEN p.clv_own_sd::double precision / sqrt(p.clv_own_n::double precision) END AS clv_anchor_se,
       CASE WHEN p.clv_own_n >= 2 AND p.clv_own_sd > 0
            THEN p.clv_own::double precision / (p.clv_own_sd::double precision / sqrt(p.clv_own_n::double precision)) END AS clv_anchor_t,
       p.clv_outlier_n + COALESCE(mc.clv_mc_outlier_n, 0)                            AS clv_outlier_n,
       p.first_pick_at, p.last_pick_at, p.picks_7d, p.settled_7d,
       -- the PUBLIC basis beside it (what /performance shows for the same bot)
       p.roi_public,
       p.clv_public,
       p.clv_n                                                                       AS clv_public_n,
       p.pnl_units_own,
       p.pnl_units_public,
       p.roi_staked,
       p.n_own_recorded,
       p.n_public_recorded
  FROM bot_performance p
  LEFT JOIN mc ON mc.bot_name = p.bot_name
  LEFT JOIN older o ON o.bot_name = p.bot_name
  LEFT JOIN bots b ON b.name = p.bot_name
  LEFT JOIN bot_config c ON c.bot_name = p.bot_name;

COMMENT ON VIEW public.bot_scoreboard IS
  '#139 scoreboard, re-based on bot_performance by #159: roi_unit = FLAT ROI at OUR books, roi_public = the same at the best price available on all books (the /performance figure), clv_anchor_* = sharp-anchor CLV at our books (the headline CLV here), clv_public = the /performance CLV, clv_mc_* = own-book margin-corrected (secondary). The legacy clv_pin_* columns were removed. PRIVATE (service_role).';

-- ── bot_weekly: record legs, anchor CLV, pnl at our books ──────────────────────────────────
DROP VIEW IF EXISTS public.bot_weekly;
CREATE VIEW public.bot_weekly AS
SELECT l.bot_name,
       date_trunc('week', l.pick_time)                                                           AS week,
       count(*)                                                                                  AS picks,
       count(*) FILTER (WHERE l.result = ANY (ARRAY['won'::text, 'lost'::text]))                 AS settled,
       count(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)                  AS clv_mc_n,
       avg(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)                    AS clv_mc_mean,
       count(l.clv_anchor_own) FILTER (WHERE l.result = ANY (ARRAY['won'::text, 'lost'::text]) AND abs(l.clv_anchor_own) <= 1) AS clv_anchor_n,
       avg(l.clv_anchor_own) FILTER (WHERE l.result = ANY (ARRAY['won'::text, 'lost'::text]) AND abs(l.clv_anchor_own) <= 1)   AS clv_anchor_mean,
       sum(l.pnl_unit_own)                                                                       AS pnl_unit
  FROM bot_ledger l
 WHERE l.in_record AND l.pick_time >= date_trunc('week', now()) - interval '77 days'
 GROUP BY l.bot_name, date_trunc('week', l.pick_time);

COMMENT ON VIEW public.bot_weekly IS
  '#139 12-week strip. #159: record legs only, clv_anchor_* (sharp-anchor at our books) replaces the legacy clv_pin_*, pnl_unit is flat at our books. PRIVATE (service_role).';

-- ── bot_market_stats: same columns, record legs, break-even at our-books price ─────────────
CREATE OR REPLACE VIEW public.bot_market_stats AS
SELECT l.bot_name,
       l.market,
       count(*) FILTER (WHERE l.result = ANY (ARRAY['won'::text, 'lost'::text]))                           AS settled,
       count(*) FILTER (WHERE l.result = 'won'::text)                                                      AS won,
       count(*) FILTER (WHERE (l.result = ANY (ARRAY['won'::text, 'lost'::text])) AND l.odds_own > 1)      AS odds_n,
       sum(1::numeric / l.odds_own) FILTER (WHERE (l.result = ANY (ARRAY['won'::text, 'lost'::text])) AND l.odds_own > 1) AS sum_inv_odds,
       count(l.clv_mc) FILTER (WHERE l.result <> 'void'::text AND abs(l.clv_mc) <= 1::numeric)             AS clv_mc_n,
       avg(l.clv_mc) FILTER (WHERE l.result <> 'void'::text AND abs(l.clv_mc) <= 1::numeric)               AS clv_mc_mean,
       stddev_samp(l.clv_mc) FILTER (WHERE l.result <> 'void'::text AND abs(l.clv_mc) <= 1::numeric)       AS clv_mc_sd,
       -- appended: sharp-anchor CLV at our books per market (the junk-control comparison, #156)
       count(l.clv_anchor_own) FILTER (WHERE (l.result = ANY (ARRAY['won'::text, 'lost'::text])) AND abs(l.clv_anchor_own) <= 1) AS clv_anchor_n,
       avg(l.clv_anchor_own) FILTER (WHERE (l.result = ANY (ARRAY['won'::text, 'lost'::text])) AND abs(l.clv_anchor_own) <= 1)   AS clv_anchor_mean,
       stddev_samp(l.clv_anchor_own) FILTER (WHERE (l.result = ANY (ARRAY['won'::text, 'lost'::text])) AND abs(l.clv_anchor_own) <= 1) AS clv_anchor_sd
  FROM bot_ledger l
 WHERE l.in_record
 GROUP BY l.bot_name, l.market;

-- ── bot_ledger_display: + the appended columns (the /performance detail view reads it) ──────
CREATE OR REPLACE VIEW public.bot_ledger_display AS
SELECT l.source, l.pick_id, l.bot_name, l.bot_id, l.match_id, l.kickoff, l.pick_time, l.market,
       l.selection, l.odds, l.bookmaker, l.result, l.pnl_unit, l.clv_raw, l.clv_mc, l.clv_pinnacle,
       l.is_inplay, l.model_version, l.rule_version,
       ht.name AS home_team,
       at.name AS away_team,
       l.odds_public, l.public_basis, l.pnl_unit_public, l.odds_own, l.own_basis, l.pnl_unit_own,
       l.stake, l.pnl_staked_public, l.clv_anchor_public, l.clv_anchor_own, l.clv_anchor_source,
       l.in_record, l.record_state, l.record_rule_version, l.model_prob, l.edge, l.strategy_profile,
       lg.name AS league,
       lg.country AS country
  FROM bot_ledger l
  LEFT JOIN matches m ON m.id = l.match_id
  LEFT JOIN teams ht ON ht.id = m.home_team_id
  LEFT JOIN teams at ON at.id = m.away_team_id
  LEFT JOIN leagues lg ON lg.id = m.league_id;

REVOKE ALL ON public.bot_ledger, public.bot_performance, public.bot_scoreboard, public.bot_weekly,
              public.bot_market_stats, public.bot_ledger_display
  FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.bot_ledger, public.bot_performance, public.bot_scoreboard, public.bot_weekly,
                public.bot_market_stats, public.bot_ledger_display TO service_role;

COMMIT;
