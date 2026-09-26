-- 461 — #162 (owner decision (b), 2026-09-26): a bot's record, split by the rule it was picked under.
--
-- WHY. Decision (b) ("no twins") changes a live bot's rule IN PLACE and tags every pick with
-- rule_version (migration 453: bots.rule_version, stamped on simulated_bets / shadow_bets by
-- trigger; source of truth BotSpec.rule_version in workers/registry/bot_registry.py). But the
-- tag stopped at the table: bot_ledger emitted NULL::text AS rule_version on the simulated and
-- shadow branches, and bot_performance groups by bot_name only — so e.g. VIP #1
-- (bot_combined_1x2_ev5_v1, now r2) showed ONE record mixing r0 and r2 picks, and nothing could
-- say whether a rule change helped.
--
-- WHAT.
--   * bot_ledger carries the real rule_version on all three branches (NULL = picked before
--     tagging began 2026-09-25 ~22:20 UTC).
--   * bot_performance_sets computes bot_performance's metric expressions ONCE over
--     GROUPING SETS ((bot_name), (bot_name, rule_version)). The pooled rows are bot_performance,
--     the per-rule rows are the NEW bot_performance_by_rule (NULL labelled 'r0' = "before
--     tagging"). One statement, one copy of each expression: the two cannot drift, and the
--     per-rule n / pnl sum exactly to the pooled row (smoke RULE-VERSION-SCORED).
--   * Both new views are PRIVATE (service_role only; anon / authenticated revoked) — read by
--     /admin/bots (bot sheet, "By rule version").
--
-- NOT CHANGED, on purpose: bot_performance still POOLS every rule version, so no public headline
-- moves. Whether a public record should show only the current rule is an OWNER decision.
-- Dry-run in BEGIN ... ROLLBACK against production: bot_performance, bot_public_record,
-- bot_scoreboard, bot_review_flag, bot_ledger (minus rule_version) and bot_ledger_display (minus
-- rule_version) hashed row-for-row before/after — 0 differences; per-rule sums = pooled for
-- picks_total / settled / pnl_units_public on every bot; SELECT * FROM bot_performance 0.27 s.
SET lock_timeout = '3s';

-- ── 1. bot_ledger: carry the pick's rule_version on the simulated / shadow branches ──────────
-- Live pg_get_viewdef text (migration 454) with exactly three edits: the two
-- `NULL::text AS rule_version` become s.rule_version / sh.rule_version, and the shadow DISTINCT ON
-- sub-select (whose column list was frozen before migration 453 added the column) gains
-- x_1.rule_version. Column list unchanged, so grants and dependents are kept. NULL stays NULL here
-- (= picked before tagging began); the 'r0' label is applied only where rules are grouped.
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
    s.rule_version,
    x.odds_public,
    x.public_basis,
        CASE s.result::text
            WHEN 'won'::text THEN x.odds_public - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
        CASE s.result::text
            WHEN 'won'::text THEN x.odds_own - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    s.stake::numeric AS stake,
        CASE s.result::text
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * s.stake
            WHEN 'lost'::text THEN - s.stake
            ELSE 0::numeric
        END AS pnl_staked_public,
    x.odds_public * a.p_close - 1::numeric AS clv_anchor_public,
    x.odds_own * a.p_close - 1::numeric AS clv_anchor_own,
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
     CROSS JOIN LATERAL ( SELECT s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text AS inplay) ip
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN ip.inplay THEN s.odds_at_pick
                    WHEN s.odds_at_pick_available > 1::numeric THEN s.odds_at_pick_available
                    WHEN s.odds_at_pick_live > 1::numeric THEN s.odds_at_pick_live
                    ELSE s.odds_at_pick
                END AS odds_public,
                CASE
                    WHEN ip.inplay THEN 'inplay'::text
                    WHEN s.odds_at_pick_available > 1::numeric THEN 'available'::text
                    WHEN s.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS public_basis,
                CASE
                    WHEN ip.inplay THEN s.odds_at_pick
                    WHEN s.odds_at_pick_live > 1::numeric THEN s.odds_at_pick_live
                    ELSE s.odds_at_pick
                END AS odds_own,
                CASE
                    WHEN ip.inplay THEN 'inplay'::text
                    WHEN s.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets'::text AND c.leg_id = s.id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL::numeric
                    ELSE anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons)
                END AS p_close,
                CASE
                    WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL::text
                    ELSE anchor_source(c.status, c.cons_status)
                END AS anchor_source) a
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
    sh.rule_version,
    x.odds_public,
    x.public_basis,
        CASE sh.result::text
            WHEN 'won'::text THEN x.odds_public - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
        CASE sh.result::text
            WHEN 'won'::text THEN x.odds_own - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    sh.stake::numeric AS stake,
        CASE sh.result::text
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * sh.stake
            WHEN 'lost'::text THEN - sh.stake
            ELSE 0::numeric
        END AS pnl_staked_public,
    x.odds_public * a.p_close - 1::numeric AS clv_anchor_public,
    x.odds_own * a.p_close - 1::numeric AS clv_anchor_own,
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
            x_1.closing_minutes_before_ko,
            x_1.rule_version
           FROM shadow_bets x_1
          WHERE NOT (EXISTS ( SELECT 1
                   FROM simulated_bets s2
                  WHERE s2.bot_id = x_1.bot_id))
          ORDER BY x_1.bot_id, x_1.match_id, x_1.market, x_1.selection, x_1.pick_time) sh
     JOIN bots b ON b.id = sh.bot_id
     JOIN matches m ON m.id = sh.match_id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
                    WHEN sh.odds_at_pick_available > 1::numeric THEN sh.odds_at_pick_available
                    WHEN sh.odds_at_pick_live > 1::numeric THEN sh.odds_at_pick_live
                    ELSE sh.odds_at_pick
                END AS odds_public,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'::text
                    WHEN sh.odds_at_pick_available > 1::numeric THEN 'available'::text
                    WHEN sh.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS public_basis,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
                    WHEN sh.odds_at_pick_live > 1::numeric THEN sh.odds_at_pick_live
                    ELSE sh.odds_at_pick
                END AS odds_own,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'::text
                    WHEN sh.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets'::text AND c.leg_id = sh.id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN NULL::numeric
                    ELSE anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons)
                END AS p_close,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN NULL::text
                    ELSE anchor_source(c.status, c.cons_status)
                END AS anchor_source) a
UNION ALL
 SELECT 'forward_test'::text AS source,
    p.id AS pick_id,
    fb.bot AS bot_name,
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
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    p.odds AS odds_own,
    'published'::text AS own_basis,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    1::numeric AS stake,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_staked_public,
    p.odds * anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons) - 1::numeric AS clv_anchor_public,
    p.odds * anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons) - 1::numeric AS clv_anchor_own,
    anchor_source(c.status, c.cons_status) AS clv_anchor_source,
        CASE
            WHEN fb.role = 'control'::text THEN p.rule_version = jr.rule_version
            ELSE COALESCE(r.record_state = ANY (ARRAY['native'::text, 'rechecked_pass'::text]), false)
        END AS in_record,
        CASE
            WHEN fb.role = 'control'::text THEN
            CASE
                WHEN p.rule_version = jr.rule_version THEN 'native'::text
                ELSE 'earlier'::text
            END
            ELSE COALESCE(r.record_state, 'unsent'::text)
        END AS record_state,
        CASE
            WHEN fb.role = 'control'::text THEN jr.rule_version
            ELSE r.current_rule_version
        END AS record_rule_version,
    p.p_sharp AS model_prob,
    p.edge,
    NULL::text AS strategy_profile
   FROM picks_forward_test p
     JOIN forward_test_leg_arm fb ON fb.leg_id = p.id AND fb.in_bot_ledger
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test'::text AND c.leg_id = p.id
     LEFT JOIN LATERAL ( SELECT j.rule_version
           FROM picks_forward_test j
          WHERE j.arm = (( SELECT fc.arm
                   FROM forward_test_arms fc
                  WHERE fc.role = 'control'::text))
          ORDER BY j.published_at DESC
         LIMIT 1) jr ON true;

-- ── 2. bot_performance_sets: ONE aggregate, two groupings ─────────────────────────────────
-- The metric expressions are bot_performance's, verbatim, computed once over
-- GROUPING SETS ((bot_name), (bot_name, rule_key)). The pooled row (is_pooled) IS bot_performance;
-- the per-rule rows are bot_performance_by_rule. Because both read the same expressions in the
-- same statement they cannot drift apart. Private (service_role only).
CREATE VIEW public.bot_performance_sets AS
 WITH l AS (
         SELECT bot_ledger.source,
            bot_ledger.pick_id,
            bot_ledger.bot_name,
            bot_ledger.bot_id,
            bot_ledger.match_id,
            bot_ledger.kickoff,
            bot_ledger.pick_time,
            bot_ledger.market,
            bot_ledger.selection,
            bot_ledger.odds,
            bot_ledger.bookmaker,
            bot_ledger.result,
            bot_ledger.pnl_unit,
            bot_ledger.clv_raw,
            bot_ledger.clv_mc,
            bot_ledger.clv_pinnacle,
            bot_ledger.is_inplay,
            bot_ledger.model_version,
            bot_ledger.rule_version,
            bot_ledger.odds_public,
            bot_ledger.public_basis,
            bot_ledger.pnl_unit_public,
            bot_ledger.odds_own,
            bot_ledger.own_basis,
            bot_ledger.pnl_unit_own,
            bot_ledger.stake,
            bot_ledger.pnl_staked_public,
            bot_ledger.clv_anchor_public,
            bot_ledger.clv_anchor_own,
            bot_ledger.clv_anchor_source,
            bot_ledger.in_record,
            bot_ledger.record_state,
            bot_ledger.record_rule_version,
            bot_ledger.model_prob,
            bot_ledger.edge,
            bot_ledger.strategy_profile,
            bot_ledger.result = ANY (ARRAY['won'::text, 'lost'::text]) AS is_settled,
            bot_ledger.clv_anchor_public IS NOT NULL AND abs(bot_ledger.clv_anchor_public) <= 1::numeric AS clv_ok_public,
            bot_ledger.clv_anchor_own IS NOT NULL AND abs(bot_ledger.clv_anchor_own) <= 1::numeric AS clv_ok_own,
            COALESCE(bot_ledger.rule_version, 'r0'::text) AS rule_key
           FROM bot_ledger
          WHERE bot_ledger.in_record
        )
 SELECT bot_name,
    rule_key AS rule_version,
    GROUPING(rule_key) = 1 AS is_pooled,
    string_agg(DISTINCT source, '+'::text ORDER BY l.source) AS source,
    max(record_rule_version) AS scored_rule_version,
    count(*) AS picks_total,
    count(*) FILTER (WHERE result = 'pending'::text) AS pending,
    count(*) FILTER (WHERE is_settled) AS settled,
    count(*) FILTER (WHERE result = 'won'::text) AS won,
    count(*) FILTER (WHERE result = 'lost'::text) AS lost,
    count(*) FILTER (WHERE result <> ALL (ARRAY['pending'::text, 'won'::text, 'lost'::text])) AS void,
    COALESCE(sum(pnl_unit_public) FILTER (WHERE is_settled), 0::numeric) AS pnl_units_public,
        CASE
            WHEN count(*) FILTER (WHERE is_settled) > 0 THEN round(avg(pnl_unit_public) FILTER (WHERE is_settled), 6)
            ELSE NULL::numeric
        END AS roi_public,
    stddev_samp(pnl_unit_public) FILTER (WHERE is_settled) AS roi_public_sd,
    count(*) FILTER (WHERE is_settled AND public_basis = 'recorded'::text) AS n_public_recorded,
    COALESCE(sum(pnl_unit_own) FILTER (WHERE is_settled), 0::numeric) AS pnl_units_own,
        CASE
            WHEN count(*) FILTER (WHERE is_settled) > 0 THEN round(avg(pnl_unit_own) FILTER (WHERE is_settled), 6)
            ELSE NULL::numeric
        END AS roi_own,
    count(*) FILTER (WHERE is_settled AND own_basis = 'recorded'::text) AS n_own_recorded,
    sum(stake) FILTER (WHERE is_settled) AS staked,
        CASE
            WHEN sum(stake) FILTER (WHERE is_settled) > 0::numeric THEN round(sum(pnl_staked_public) FILTER (WHERE is_settled) / sum(stake) FILTER (WHERE is_settled), 6)
            ELSE NULL::numeric
        END AS roi_staked,
    count(*) FILTER (WHERE is_settled AND clv_ok_public) AS clv_n,
    count(*) FILTER (WHERE is_settled AND clv_ok_public AND clv_anchor_source = 'pinnacle'::text) AS clv_n_pinnacle,
    count(*) FILTER (WHERE is_settled AND clv_ok_public AND clv_anchor_source = 'consensus'::text) AS clv_n_consensus,
    avg(clv_anchor_public) FILTER (WHERE is_settled AND clv_ok_public) AS clv_public,
    stddev_samp(clv_anchor_public) FILTER (WHERE is_settled AND clv_ok_public) AS clv_public_sd,
    count(*) FILTER (WHERE is_settled AND clv_ok_own) AS clv_own_n,
    avg(clv_anchor_own) FILTER (WHERE is_settled AND clv_ok_own) AS clv_own,
    stddev_samp(clv_anchor_own) FILTER (WHERE is_settled AND clv_ok_own) AS clv_own_sd,
    count(*) FILTER (WHERE is_settled AND clv_anchor_own IS NOT NULL AND NOT clv_ok_own) AS clv_outlier_n,
    min(pick_time) AS first_pick_at,
    max(pick_time) AS last_pick_at,
    count(*) FILTER (WHERE pick_time >= (now() - '7 days'::interval)) AS picks_7d,
    count(*) FILTER (WHERE is_settled AND kickoff >= (now() - '7 days'::interval)) AS settled_7d
   FROM l
  GROUP BY GROUPING SETS ((bot_name), (bot_name, rule_key));

-- ── 3. bot_performance: the pooled rows — output unchanged (dry-run parity: 0 differences) ──
CREATE OR REPLACE VIEW public.bot_performance AS
 SELECT bot_name,
    source,
    scored_rule_version,
    picks_total,
    pending,
    settled,
    won,
    lost,
    void,
    pnl_units_public,
    roi_public,
    roi_public_sd,
    n_public_recorded,
    pnl_units_own,
    roi_own,
    n_own_recorded,
    staked,
    roi_staked,
    clv_n,
    clv_n_pinnacle,
    clv_n_consensus,
    clv_public,
    clv_public_sd,
    clv_own_n,
    clv_own,
    clv_own_sd,
    clv_outlier_n,
    first_pick_at,
    last_pick_at,
    picks_7d,
    settled_7d
   FROM public.bot_performance_sets
  WHERE is_pooled;

-- ── 4. bot_performance_by_rule: one row per (bot, rule_version) ───────────────────────────
CREATE VIEW public.bot_performance_by_rule AS
 SELECT bot_name,
    rule_version,
    source,
    scored_rule_version,
    picks_total,
    pending,
    settled,
    won,
    lost,
    void,
    pnl_units_public,
    roi_public,
    roi_public_sd,
    n_public_recorded,
    pnl_units_own,
    roi_own,
    n_own_recorded,
    staked,
    roi_staked,
    clv_n,
    clv_n_pinnacle,
    clv_n_consensus,
    clv_public,
    clv_public_sd,
    clv_own_n,
    clv_own,
    clv_own_sd,
    clv_outlier_n,
    first_pick_at,
    last_pick_at,
    picks_7d,
    settled_7d
   FROM public.bot_performance_sets
  WHERE NOT is_pooled;

REVOKE ALL ON public.bot_performance_sets, public.bot_performance_by_rule FROM PUBLIC;
REVOKE ALL ON public.bot_performance_sets, public.bot_performance_by_rule FROM anon, authenticated;
GRANT SELECT ON public.bot_performance_sets, public.bot_performance_by_rule TO service_role;

COMMENT ON VIEW public.bot_performance_by_rule IS
  'Per-bot record split by rule_version (#162 owner decision (b), migration 461). Same metric '
  'expressions as bot_performance (both read bot_performance_sets). rule_version r0 = picked before '
  'tagging began 2026-09-25 ~22:20 UTC. Forward-test bots carry their picks_forward_test rule_version. '
  'Private: service_role only. bot_performance still POOLS every rule version -- whether a public '
  'record shows only the current rule is an owner decision.';
COMMENT ON VIEW public.bot_performance_sets IS
  'Internal to bot_performance / bot_performance_by_rule (migration 461): one aggregate over '
  'GROUPING SETS ((bot_name), (bot_name, rule_version)); is_pooled marks the bot_performance rows.';
