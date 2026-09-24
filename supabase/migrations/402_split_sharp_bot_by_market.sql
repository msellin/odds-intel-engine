-- 402 — SPLIT THE SHARP BOT BY MARKET ([[#122]], 2026-09-24, owner: "yes, only sharp bot")
--
-- bot_sharp_forward_test_v1 published 1x2 AND O/U 2.5 as ONE record. Measured on
-- clv_sharp (odds x de-vigged Pinnacle close - 1) the two halves differ:
--   1x2       n=70  +2.27% ± 0.72
--   O/U 2.5   n=22  +0.67% ± 0.91
-- One record averages them — the same mixing that hid bot_v10_all's losing O/U half
-- for five months (migration 375). Two identities make each half visible, and let an
-- O/U half be retired on its own evidence (trigger: clv_sharp CI entirely below 0 at
-- n >= 100).
--
-- BOOKKEEPING ONLY, exactly like the consensus grade split (migration 380): every
-- pick stays in picks_forward_test with arm = 'live'; the market decides which bot
-- OWNS the row. The rule, the picks, the pre-registered test and its count are
-- unchanged — picks_forward_test_summary (the stopping-rule view) is NOT touched; a
-- NEW view, picks_forward_test_summary_by_market, carries the per-market records.
-- The consensus grades are deliberately NOT split by market (6 tiny records).

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks,
    display_name
) VALUES
(
    'bot_sharp_1x2_v1',
    'Sharp-line picks, 1x2 half ([[#122]]) — the pre-registered sharp-edge forward test''s 1x2 picks: edge >= 3% against Shin-de-vigged Pinnacle at the best available price. clv_sharp +2.27% (n=70) at the split.',
    'sharp_forward_test',
    'picks_forward_test WHERE arm=''live'' AND market=''1x2''. Same pre-registered rule as the O/U half (scripts/publish_picks_forward_test.py RULE_VERSION); only the owner of the row differs. Published to Telegram and /picks. Never staked.',
    TRUE, 'testing', 1.00, 1.00, TRUE,
    'Sharp-line picks — 1x2'
),
(
    'bot_sharp_ou_v1',
    'Sharp-line picks, O/U 2.5 half ([[#122]]) — the pre-registered sharp-edge forward test''s over/under 2.5 picks. clv_sharp +0.67% (n=22) at the split; retire on its own record if its clv_sharp CI is entirely below 0 at n >= 100.',
    'sharp_forward_test',
    'picks_forward_test WHERE arm=''live'' AND market=''over_under_25''. Same pre-registered rule as the 1x2 half; only the owner of the row differs. Published to Telegram and /picks. Never staked.',
    TRUE, 'testing', 1.00, 1.00, TRUE,
    'Sharp-line picks — O/U 2.5'
)
ON CONFLICT (name) DO NOTHING;

-- The parent is superseded, not deleted: its rows now belong to the two halves.
UPDATE bots SET retired_at = now(), is_active = FALSE,
       retired_reason = 'Split by market into bot_sharp_1x2_v1 + bot_sharp_ou_v1 (#122, migration 402)'
 WHERE name = 'bot_sharp_forward_test_v1' AND retired_at IS NULL;

-- Views: only the bot-label branch changes (generated from the live definitions).
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
        CASE
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
            WHEN p.arm = 'consensus_anchor'::text THEN 'bot_consensus_b_v1'::text
            WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
            ELSE 'bot_sharp_1x2_v1'::text
        END AS bot,
    p.match_id,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
    p.edge,
    p.p_sharp AS fair_prob,
    p.rule_version,
    p.alignment_gap_minutes,
    p.published_at,
    p.outcome,
    p.clv,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    p.arm,
    p.anchor_bookmaker,
    p.grade
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE (p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])) AND m.status <> 'postponed'::match_status AND NOT (p.grade = 'D'::text AND p.telegram_message_id IS NULL)
UNION ALL
 SELECT s.id,
    'model'::text AS edge_kind,
    b.name AS bot,
    s.match_id,
    s.market,
    s.selection,
    s.odds_at_pick AS odds,
    s.recommended_bookmaker AS bookmaker,
        CASE
            WHEN s.calibrated_prob IS NOT NULL AND s.odds_at_pick > 0::numeric THEN round(s.calibrated_prob - 1.0 / s.odds_at_pick, 6)
            ELSE s.edge_percent::numeric
        END AS edge,
    s.calibrated_prob AS fair_prob,
    s.model_version AS rule_version,
    NULL::numeric AS alignment_gap_minutes,
    s.pick_time AS published_at,
    NULLIF(s.result::text, 'pending'::text) AS outcome,
    s.clv,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    NULL::text AS arm,
    NULL::text AS anchor_bookmaker,
    NULL::text AS grade
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL AND m.status <> 'postponed'::match_status;

CREATE OR REPLACE VIEW clv_sharp_legs AS
 WITH legs AS (
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
                CASE
                    WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
                    WHEN p.arm = 'live'::text THEN 'bot_sharp_1x2_v1'::text
                    WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
                    WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
                    WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'B'::text THEN 'bot_consensus_b_v1'::text
                    WHEN p.arm = 'consensus_anchor'::text THEN 'consensus_ungraded'::text
                    ELSE p.arm
                END AS bot,
            p.arm,
            p.grade,
            p.rule_version AS version,
            p.bookmaker,
            p.published_at AS decided_at,
            EXTRACT(epoch FROM p.published_at - p.odds_quoted_at) / 60.0 AS quote_age_min
           FROM leg_clv_sharp c
             JOIN picks_forward_test p ON p.id = c.leg_id
          WHERE c.ledger = 'picks_forward_test'::text AND c.status = 'ok'::text
        UNION ALL
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
            b.name,
            NULL::text,
            NULL::text,
            s.model_version,
            s.recommended_bookmaker,
            s.pick_time,
            s.decision_quote_age_min
           FROM leg_clv_sharp c
             JOIN shadow_bets s ON s.id = c.leg_id
             JOIN bots b ON b.id = s.bot_id
          WHERE c.ledger = 'shadow_bets'::text AND c.status = 'ok'::text
        UNION ALL
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
            b.name,
            NULL::text,
            NULL::text,
            s.model_version,
            s.recommended_bookmaker,
            s.pick_time,
            NULL::numeric
           FROM leg_clv_sharp c
             JOIN simulated_bets s ON s.id = c.leg_id
             JOIN bots b ON b.id = s.bot_id
          WHERE c.ledger = 'simulated_bets'::text AND c.status = 'ok'::text
        )
 SELECT l.ledger,
    l.leg_id,
    l.match_id,
    l.market,
    l.selection,
    l.odds,
    l.odds_basis,
    l.p_close,
    l.close_age_min,
    l.clv_sharp,
    l.bot,
    l.arm,
    l.grade,
    l.version,
    l.bookmaker,
    l.decided_at,
    l.quote_age_min,
    m.date AS kickoff,
    l.decided_at::date AS decided_day,
        CASE
            WHEN l.bookmaker = ANY (ARRAY['Coolbet'::text, 'Epicbet'::text, 'Unibet-Site'::text, 'Tonybet'::text, 'Optibet'::text, 'Paf'::text, 'Olybet'::text]) THEN 'scraped'::text
            WHEN l.bookmaker = 'Unibet-Kambi'::text THEN 'kambi'::text
            WHEN l.bookmaker = 'Pinnacle'::text THEN 'sharp'::text
            WHEN l.bookmaker IS NULL THEN 'unknown'::text
            ELSE 'af_fed'::text
        END AS book_feed,
        CASE
            WHEN l.market = '1x2'::text THEN '1x2'::text
            WHEN l.market ~~ 'corners%'::text THEN 'corners'::text
            WHEN l.market ~~ 'over_under_1h%'::text OR l.market ~~ '%_1h%'::text THEN 'first_half'::text
            WHEN l.market ~~ 'over_under_%'::text THEN 'over_under'::text
            WHEN l.market = ANY (ARRAY['double_chance'::text, 'draw_no_bet'::text]) THEN l.market
            WHEN l.market ~~ 'team_total%'::text THEN 'team_total'::text
            WHEN l.market ~~ 'corners%'::text THEN 'corners'::text
            ELSE 'other'::text
        END AS market_group,
        CASE
            WHEN l.odds < 1.60 THEN '<1.60'::text
            WHEN l.odds < 2.00 THEN '1.60-2.00'::text
            WHEN l.odds < 2.50 THEN '2.00-2.50'::text
            WHEN l.odds < 3.00 THEN '2.50-3.00'::text
            WHEN l.odds < 4.00 THEN '3.00-4.00'::text
            ELSE '4.00+'::text
        END AS odds_band,
        CASE
            WHEN (m.date - l.decided_at) < '01:00:00'::interval THEN '<1h'::text
            WHEN (m.date - l.decided_at) < '03:00:00'::interval THEN '1-3h'::text
            WHEN (m.date - l.decided_at) < '06:00:00'::interval THEN '3-6h'::text
            WHEN (m.date - l.decided_at) < '12:00:00'::interval THEN '6-12h'::text
            WHEN (m.date - l.decided_at) < '24:00:00'::interval THEN '12-24h'::text
            ELSE '24h+'::text
        END AS ttk_bucket,
        CASE
            WHEN l.quote_age_min IS NULL THEN 'unknown'::text
            WHEN l.quote_age_min <= 60::numeric THEN 'fresh'::text
            ELSE 'stale'::text
        END AS quote_freshness,
    lg.tier AS league_tier,
    row_number() OVER (PARTITION BY l.ledger, l.bot, l.match_id, l.market, l.selection ORDER BY l.decided_at, l.leg_id) AS dup_rank
   FROM legs l
     JOIN matches m ON m.id = l.match_id
     LEFT JOIN leagues lg ON lg.id = m.league_id
  WHERE l.bookmaker IS DISTINCT FROM 'api-football-live'::text;

CREATE OR REPLACE VIEW picks_forward_test_summary_by_market AS
 SELECT rule_version,
    min(published_at) AS started_at,
    count(*) AS published,
    count(*) FILTER (WHERE outcome IS NULL) AS pending,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['push'::text, 'void'::text])) AS refunded,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS settled,
    count(*) FILTER (WHERE outcome = 'won'::text) AS won,
    sum(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS pnl_units,
    avg(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi,
    stddev_samp(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi_sd,
    count(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv,
    avg(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_raw,
    count(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv_mc,
    avg(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_margin_corrected,
    stddev_samp(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_mc_sd,
    arm,
    grade,
    market
   FROM picks_forward_test
  WHERE arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
  GROUP BY rule_version, arm, grade, market;

GRANT SELECT ON picks_forward_test_summary_by_market TO anon, authenticated, service_role;
