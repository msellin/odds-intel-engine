-- 390 — CLV-SHARP VIEW FIXES from the independent review of #024 (a)/(b) (2026-09-23)
--
-- 1. shadow_bets legs were counted ~7x (one row per re-evaluation): new dup_rank column.
-- 2. in-play 'api-football-live' legs excluded (judged against a pre-match close).
-- 3. book_feed: Unibet-Kambi is 'kambi' (Kambi API, not AF); Pinnacle is 'sharp' — it made
--    40% of the junk arm's "AF-fed" legs, i.e. Pinnacle judged against its own close.
-- 4. consensus picks with a NULL grade are 'consensus_ungraded', not grade B.
-- 5. corners_1h_* belong to 'corners', not 'first_half'.
-- Published-arm numbers were reproduced exactly by the review; none of these touch them.
-- DROP + CREATE because a column is added and the WHERE changes.
DROP VIEW IF EXISTS clv_sharp_legs;
CREATE VIEW clv_sharp_legs AS
WITH legs AS (
    SELECT c.ledger, c.leg_id, c.match_id, c.market, c.selection, c.odds, c.odds_basis,
           c.p_close, c.close_age_min, c.clv_sharp,
           CASE WHEN p.arm = 'live' THEN 'bot_sharp_forward_test_v1'
                WHEN p.arm = 'consensus_anchor' AND p.grade = 'D' THEN 'bot_consensus_d_v1'
                WHEN p.arm = 'consensus_anchor' AND p.grade = 'C' THEN 'bot_consensus_c_v1'
                WHEN p.arm = 'consensus_anchor' AND p.grade = 'B' THEN 'bot_consensus_b_v1'
                WHEN p.arm = 'consensus_anchor' THEN 'consensus_ungraded'
                ELSE p.arm END AS bot,
           p.arm, p.grade, p.rule_version AS version, p.bookmaker,
           p.published_at AS decided_at,
           EXTRACT(epoch FROM (p.published_at - p.odds_quoted_at)) / 60.0 AS quote_age_min
      FROM leg_clv_sharp c JOIN picks_forward_test p ON p.id = c.leg_id
     WHERE c.ledger = 'picks_forward_test' AND c.status = 'ok'
    UNION ALL
    SELECT c.ledger, c.leg_id, c.match_id, c.market, c.selection, c.odds, c.odds_basis,
           c.p_close, c.close_age_min, c.clv_sharp,
           b.name, NULL, NULL, s.model_version, s.recommended_bookmaker,
           s.pick_time, s.decision_quote_age_min
      FROM leg_clv_sharp c JOIN shadow_bets s ON s.id = c.leg_id JOIN bots b ON b.id = s.bot_id
     WHERE c.ledger = 'shadow_bets' AND c.status = 'ok'
    UNION ALL
    SELECT c.ledger, c.leg_id, c.match_id, c.market, c.selection, c.odds, c.odds_basis,
           c.p_close, c.close_age_min, c.clv_sharp,
           b.name, NULL, NULL, s.model_version, s.recommended_bookmaker,
           s.pick_time, NULL
      FROM leg_clv_sharp c JOIN simulated_bets s ON s.id = c.leg_id JOIN bots b ON b.id = s.bot_id
     WHERE c.ledger = 'simulated_bets' AND c.status = 'ok'
)
SELECT l.*,
       m.date AS kickoff,
       l.decided_at::date AS decided_day,
       CASE WHEN l.bookmaker IN ('Coolbet', 'Epicbet', 'Unibet-Site', 'Tonybet', 'Optibet',
                                 'Paf', 'Olybet') THEN 'scraped'
            WHEN l.bookmaker = 'Unibet-Kambi' THEN 'kambi'
            WHEN l.bookmaker = 'Pinnacle' THEN 'sharp'
            WHEN l.bookmaker IS NULL THEN 'unknown' ELSE 'af_fed' END AS book_feed,
       CASE WHEN l.market IN ('1x2') THEN '1x2'
            WHEN l.market LIKE 'corners%' THEN 'corners'
            WHEN l.market LIKE 'over_under_1h%' OR l.market LIKE '%_1h%' THEN 'first_half'
            WHEN l.market LIKE 'over_under_%' THEN 'over_under'
            WHEN l.market IN ('double_chance', 'draw_no_bet') THEN l.market
            WHEN l.market LIKE 'team_total%' THEN 'team_total'
            WHEN l.market LIKE 'corners%' THEN 'corners'
            ELSE 'other' END AS market_group,
       CASE WHEN l.odds < 1.60 THEN '<1.60' WHEN l.odds < 2.00 THEN '1.60-2.00'
            WHEN l.odds < 2.50 THEN '2.00-2.50' WHEN l.odds < 3.00 THEN '2.50-3.00'
            WHEN l.odds < 4.00 THEN '3.00-4.00' ELSE '4.00+' END AS odds_band,
       CASE WHEN m.date - l.decided_at < interval '1 hour'  THEN '<1h'
            WHEN m.date - l.decided_at < interval '3 hours' THEN '1-3h'
            WHEN m.date - l.decided_at < interval '6 hours' THEN '3-6h'
            WHEN m.date - l.decided_at < interval '12 hours' THEN '6-12h'
            WHEN m.date - l.decided_at < interval '24 hours' THEN '12-24h'
            ELSE '24h+' END AS ttk_bucket,
       CASE WHEN l.quote_age_min IS NULL THEN 'unknown'
            WHEN l.quote_age_min <= 60 THEN 'fresh' ELSE 'stale' END AS quote_freshness,
       lg.tier AS league_tier,
       -- shadow_bets writes a NEW row on every re-evaluation (median ~7 per bet,
       -- up to 47/day); dup_rank = 1 is the first decision on each bet. The
       -- segment report uses dup_rank = 1 unless told otherwise.
       row_number() OVER (PARTITION BY l.ledger, l.bot, l.match_id, l.market, l.selection
                          ORDER BY l.decided_at, l.leg_id) AS dup_rank
  FROM legs l
  JOIN matches m ON m.id = l.match_id
  LEFT JOIN leagues lg ON lg.id = m.league_id
 -- in-play odds judged against a PRE-MATCH close measure nothing
 WHERE l.bookmaker IS DISTINCT FROM 'api-football-live';
