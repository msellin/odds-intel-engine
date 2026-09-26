-- 476 — [[#187]] Asian handicap QUARTER lines settle as half win / half loss; the new AH bot's row.
--
-- WHY. A quarter-line AH bet (e.g. home -0.75) is half the stake on each neighbouring line (-0.5 and -1);
-- when one half pushes, the bet is a HALF win or HALF loss. The bet_result enum has no such value and
-- bot_ledger computes P&L from the result word alone, so settlement graded quarter lines as a full
-- win/loss (settlement.py _r_asian_handicap strict comparison — a home -0.25 draw lost the whole stake).
-- The three AH shadow bots that ever produced these rows are retired (bot_ah_home_fav, bot_ah_away_dog,
-- bot_high_alignment), so no live record changes; the new bot (below) needs it right from its first pick.
--
-- WHAT.
--   * simulated_bets.settle_fraction (1 = normal, 0.5 = quarter-line half result). The result word stays
--     won / lost, so every counter, enum and filter keeps working; settlement writes 0.5 and a halved pnl.
--   * bot_ledger's SIM branch multiplies its four P&L columns by settle_fraction. Every other line of the
--     view is 467's text verbatim; the column list is unchanged, so dependents and grants are kept.
--   * bots row: bot_ah_sharp_v1, EXPERIMENTAL (admins only) — the pre-registered pooled rule that passed
--     discovery AND holdout (dev/active/ah-market-bot-prereg.md).
-- depends on: 467 (its bot_ledger text, verbatim apart from the four CASEs).
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.simulated_bets
  ADD COLUMN IF NOT EXISTS settle_fraction numeric NOT NULL DEFAULT 1;
ALTER TABLE public.simulated_bets DROP CONSTRAINT IF EXISTS simulated_bets_settle_fraction_check;
ALTER TABLE public.simulated_bets
  ADD CONSTRAINT simulated_bets_settle_fraction_check CHECK (settle_fraction IN (0.5, 1));
COMMENT ON COLUMN public.simulated_bets.settle_fraction IS
  '#187: 0.5 when a quarter-line Asian handicap bet is a HALF win / HALF loss (result stays won/lost; pnl and bot_ledger P&L are halved). 1 otherwise.';

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
            WHEN 'won'::text THEN (s.odds_at_pick - 1::numeric) * s.settle_fraction
            WHEN 'lost'::text THEN - s.settle_fraction
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
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * s.settle_fraction
            WHEN 'lost'::text THEN - s.settle_fraction
            ELSE 0::numeric
        END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
        CASE s.result::text
            WHEN 'won'::text THEN (x.odds_own - 1::numeric) * s.settle_fraction
            WHEN 'lost'::text THEN - s.settle_fraction
            ELSE 0::numeric
        END AS pnl_unit_own,
    s.stake::numeric AS stake,
        CASE s.result::text
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * s.stake * s.settle_fraction
            WHEN 'lost'::text THEN - s.stake * s.settle_fraction
            ELSE 0::numeric
        END AS pnl_staked_public,
    x.odds_public * a.p_close - 1::numeric AS clv_anchor_public,
    x.odds_own * a.p_close - 1::numeric AS clv_anchor_own,
    a.anchor_source AS clv_anchor_source,
        CASE
            WHEN b.record_restart_at IS NULL OR s.pick_time >= b.record_restart_at THEN true
            ELSE (EXISTS ( SELECT 1
               FROM pick_sends ps
              WHERE ps.pick_table = 'simulated_bets'::text AND ps.pick_id = s.id AND ps.status = 'sent'::text))
        END AS in_record,
        CASE
            WHEN b.record_restart_at IS NULL OR s.pick_time >= b.record_restart_at THEN 'counted'::text
            WHEN (EXISTS ( SELECT 1
               FROM pick_sends ps
              WHERE ps.pick_table = 'simulated_bets'::text AND ps.pick_id = s.id AND ps.status = 'sent'::text)) THEN 'sent_before_restart'::text
            ELSE 'before_restart'::text
        END AS record_state,
        CASE
            WHEN b.record_restart_at IS NULL THEN NULL::text
            ELSE b.rule_version
        END AS record_rule_version,
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
        CASE
            WHEN b.record_restart_at IS NULL OR sh.pick_time >= b.record_restart_at THEN true
            ELSE (EXISTS ( SELECT 1
               FROM pick_sends ps
              WHERE ps.pick_table = 'shadow_bets'::text AND ps.pick_id = sh.id AND ps.status = 'sent'::text))
        END AS in_record,
        CASE
            WHEN b.record_restart_at IS NULL OR sh.pick_time >= b.record_restart_at THEN 'counted'::text
            WHEN (EXISTS ( SELECT 1
               FROM pick_sends ps
              WHERE ps.pick_table = 'shadow_bets'::text AND ps.pick_id = sh.id AND ps.status = 'sent'::text)) THEN 'sent_before_restart'::text
            ELSE 'before_restart'::text
        END AS record_state,
        CASE
            WHEN b.record_restart_at IS NULL THEN NULL::text
            ELSE b.rule_version
        END AS record_rule_version,
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
            x_1.rule_version,
            x_1.odds_own_pick_time
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
                    WHEN sh.pick_time < '2026-09-25 15:22:24+00'::timestamp with time zone AND sh.odds_own_pick_time > 1::numeric THEN sh.odds_own_pick_time
                    WHEN sh.odds_at_pick_live > 1::numeric THEN sh.odds_at_pick_live
                    ELSE sh.odds_at_pick
                END AS odds_own,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'::text
                    WHEN sh.pick_time < '2026-09-25 15:22:24+00'::timestamp with time zone AND sh.odds_own_pick_time > 1::numeric THEN 'pick_time_snapshot'::text
                    WHEN sh.pick_time < '2026-09-25 15:22:24+00'::timestamp with time zone AND sh.odds_at_pick_live > 1::numeric THEN 'our_books_unverified'::text
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
            WHEN fb.role = 'twin'::text THEN p.rule_version = (( SELECT fa2.rule_version
               FROM forward_test_arms fa2
              WHERE fa2.arm = p.arm))
            ELSE COALESCE(r.record_state = ANY (ARRAY['native'::text, 'rechecked_pass'::text]), false)
        END AS in_record,
        CASE
            WHEN fb.role = 'control'::text THEN
            CASE
                WHEN p.rule_version = jr.rule_version THEN 'native'::text
                ELSE 'earlier'::text
            END
            WHEN fb.role = 'twin'::text THEN
            CASE
                WHEN p.rule_version = (( SELECT fa2.rule_version
                   FROM forward_test_arms fa2
                  WHERE fa2.arm = p.arm)) THEN 'native'::text
                ELSE 'earlier'::text
            END
            ELSE COALESCE(r.record_state, 'unsent'::text)
        END AS record_state,
        CASE
            WHEN fb.role = 'control'::text THEN jr.rule_version
            WHEN fb.role = 'twin'::text THEN ( SELECT fa2.rule_version
               FROM forward_test_arms fa2
              WHERE fa2.arm = p.arm)
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

INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks)
VALUES ('bot_ah_sharp_v1', 'Asian handicap — sharp line',
        'Asian handicap, every line (whole/half/quarter): a publishable API-Football book beats Pinnacle''s power-de-vigged fair price on the SAME line by EV 3-15%, >= 45 min before kickoff, one pick per match. #187, pre-registered; passed discovery + holdout on independent-close CLV.',
        1000.00, 1000.00, true, 'experimental', false)
ON CONFLICT (name) DO NOTHING;

COMMIT;
