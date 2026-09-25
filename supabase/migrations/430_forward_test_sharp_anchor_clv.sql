-- 430 — SHARP-ANCHOR CLV FOR THE FORWARD TEST + NEVER-SENT GRADE-D PICKS OUT OF THE RECORD ([[#156]], 2026-09-25, owner-approved)
--
-- (1) picks_forward_test_anchor_clv — NEW, PRIVATE (service_role only; no anon grant, so the
--     ANON-LEAST-PRIVILEGE list is unchanged). /performance reads it server-side with the
--     service client. Per (arm, grade, market, rule_version): the SHARP-ANCHOR CLV of settled
--     (won/lost) legs = leg_clv_sharp.clv_sharp (fresh Shin-de-vigged Pinnacle close) where
--     status='ok', else clv_cons (>=5-book consensus close) where cons_status='ok'. Thin 3–4-book
--     consensus is EXCLUDED, never pooled. |clv| > 1 is a data fault (bot_scoreboard guard).
--     The own-book margin-corrected figure is carried beside it on the same legs.
--
--     WHY: the forward test was judged on clv_margin_corrected — the pick's odds against the
--     SAME soft book's own close. This rule picks a leg because that book misprices it, so a
--     never-corrected soft line scores the pick at about minus its margin by construction. On
--     that measure live − junk control was −0.4pp [−1.9, +1.2]; on the sharp-anchor close
--     +4.6pp (1X2) / +3.7pp (O/U). Stopping rule amended the same day
--     (dev/active/picks-forward-test-preregistration.md, AMENDMENT 1).
--
-- (2) picks_forward_test_summary, _summary_by_market and picks_forward_test_public drop grade-D
--     consensus picks that were NEVER SENT (grade D stopped publishing, [[#098]]) — the filter
--     picks_public_all already has. Written NULL-safe (IS NOT DISTINCT FROM): the live arm has
--     grade NULL, and `NOT (grade = 'D' AND telegram_message_id IS NULL)` evaluates to NULL — so
--     drops the row — for a live pick recorded while Telegram sends were paused (#139).
--     Columns are unchanged, so CREATE OR REPLACE keeps every existing grant.

CREATE OR REPLACE VIEW picks_forward_test_summary AS
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
    grade
   FROM picks_forward_test
  WHERE arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
    AND NOT (grade IS NOT DISTINCT FROM 'D' AND telegram_message_id IS NULL)
  GROUP BY rule_version, arm, grade;

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
    AND NOT (grade IS NOT DISTINCT FROM 'D' AND telegram_message_id IS NULL)
  GROUP BY rule_version, arm, grade, market;

CREATE OR REPLACE VIEW picks_forward_test_public AS
 SELECT p.id,
    p.match_id,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
    p.edge,
    p.p_sharp,
    p.anchor_bookmaker,
    p.anchor_overround,
    p.alignment_gap_minutes,
    p.rule_version,
    p.kickoff_at,
    p.published_at,
    p.outcome,
    p.pnl,
    p.closing_odds,
    p.closing_bookmaker,
    p.clv,
    p.clv_margin_corrected,
    p.settled_at,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    p.arm,
    p.grade
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
    AND NOT (p.grade IS NOT DISTINCT FROM 'D' AND p.telegram_message_id IS NULL);

CREATE OR REPLACE VIEW public.picks_forward_test_anchor_clv AS
WITH legs AS (
    SELECT p.arm, p.grade, p.market, p.rule_version, p.published_at,
           CASE WHEN c.status = 'ok' THEN c.clv_sharp
                WHEN c.cons_status = 'ok' THEN c.clv_cons END           AS clv_anchor,
           CASE WHEN c.status = 'ok' THEN 'pinnacle'
                WHEN c.cons_status = 'ok' THEN 'consensus' END          AS anchor_source,
           p.clv_margin_corrected
      FROM picks_forward_test p
      LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = p.id
     WHERE p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
       AND NOT (p.grade IS NOT DISTINCT FROM 'D' AND p.telegram_message_id IS NULL)
       AND p.outcome = ANY (ARRAY['won'::text, 'lost'::text])
)
SELECT arm, grade, market, rule_version,
       min(published_at)                                                          AS started_at,
       count(*)                                                                   AS settled,
       count(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1)                      AS n_anchor,
       count(*) FILTER (WHERE anchor_source = 'pinnacle'  AND abs(clv_anchor) <= 1) AS n_pinnacle,
       count(*) FILTER (WHERE anchor_source = 'consensus' AND abs(clv_anchor) <= 1) AS n_consensus,
       avg(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1)                        AS clv_anchor,
       stddev_samp(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1)                AS clv_anchor_sd,
       count(clv_margin_corrected) FILTER (WHERE abs(clv_margin_corrected) <= 1)  AS n_clv_mc,
       avg(clv_margin_corrected) FILTER (WHERE abs(clv_margin_corrected) <= 1)    AS clv_margin_corrected
  FROM legs
 GROUP BY arm, grade, market, rule_version;

COMMENT ON VIEW public.picks_forward_test_anchor_clv IS
  '#156: sharp-anchor CLV (clv_sharp, else >=5-book clv_cons; thin excluded) per arm/grade/market/rule_version on settled legs, own-book margin-corrected beside it. PRIVATE — read server-side by /performance with the service client. Stopping rule: dev/active/picks-forward-test-preregistration.md AMENDMENT 1.';

REVOKE ALL ON public.picks_forward_test_anchor_clv FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.picks_forward_test_anchor_clv TO service_role;
