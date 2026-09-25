-- 431 — EARLIER-RULE PICKS RE-CHECKED AGAINST THE CURRENT RULE, FROM PICK-TIME DATA ([[#158]], 2026-09-25, owner-approved)
--
-- Since [[#156]] (migration 430) /performance scores each forward-test bot on its CURRENT
-- rule_version only. The consensus arm moved consensus_edge_v1 -> consensus_edge_v2_2026_09_24,
-- whose ONLY change is the credible-method gate (edge >= 3% under ALL of shin / additive /
-- power). A v1 pick that would ALSO have been published under v2 is a v2 pick in every respect
-- but its label, so the owner decided it counts in the bot's current record — provided the
-- verdict comes from what was known when it was published, never from how it turned out.
--
-- The verdict is DATA, not page logic: scripts/recheck_forward_test_picks.py rebuilds each SENT
-- earlier-rule pick's consensus from odds_snapshots as of published_at with the publisher's own
-- functions and writes one row per (pick, rule it was checked against). It reads no outcome,
-- pnl, closing or CLV column. Idempotent: ON CONFLICT DO NOTHING — a verdict is frozen once
-- reached, so a re-run after the intraday snapshots age out can never overwrite it.
--
-- The PRE-REGISTERED test is untouched: picks_forward_test.rule_version is never rewritten, and
-- picks_forward_test_summary / _by_market (which the stopping-rule machinery and the /picks
-- panel read) still group on rule_version as published. Only the /performance BOT RECORD
-- (picks_forward_test_bot_record, below) and the bot's bet list (record_rule_version on
-- picks_forward_test_public) follow the re-check.

CREATE TABLE IF NOT EXISTS public.pick_rule_recheck (
    pick_id              uuid        NOT NULL REFERENCES public.picks_forward_test(id) ON DELETE CASCADE,
    checked_rule_version text        NOT NULL,
    passes               boolean     NOT NULL,
    details              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    checked_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pick_id, checked_rule_version)
);
COMMENT ON TABLE public.pick_rule_recheck IS
  '#158: an earlier-rule forward-test pick re-checked against a later rule from PICK-TIME data only (scripts/recheck_forward_test_picks.py). passes=true => counts in that rule''s /performance bot record, marked re-checked. Private.';
ALTER TABLE public.pick_rule_recheck ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.pick_rule_recheck FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.pick_rule_recheck TO service_role;

-- Each published arm's CURRENT rule = the rule_version of its most recently claimed pick
-- (sent or not — an unsent grade-D claim is still the engine running that rule).
CREATE OR REPLACE VIEW public.picks_forward_test_arm_rule AS
SELECT DISTINCT ON (arm) arm, rule_version AS current_rule_version
  FROM picks_forward_test
 WHERE arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
 ORDER BY arm, published_at DESC;

-- One row per record-eligible pick (published arms, never-sent grade D out — the 430 filter,
-- NULL-safe) with the rule it COUNTS under:
--   native          rule_version = the arm's current rule
--   rechecked_pass  an earlier rule, passed the current rule on pick-time data -> counts as current
--   rechecked_fail  an earlier rule, failed it -> "didn't meet today's rule", not counted
--   earlier         an earlier rule, never re-checked -> not counted
-- A re-check against an OLDER rule than the current one never matches the join, so when a v3
-- ships every v2-verdict falls back to 'earlier' until re-checked against v3.
CREATE OR REPLACE VIEW public.picks_forward_test_record_leg AS
SELECT p.id, p.arm, p.grade, p.market, p.rule_version, p.published_at,
       p.outcome, p.pnl, p.clv_margin_corrected,
       cur.current_rule_version,
       CASE WHEN p.rule_version = cur.current_rule_version THEN 'native'
            WHEN rc.passes IS TRUE  THEN 'rechecked_pass'
            WHEN rc.passes IS FALSE THEN 'rechecked_fail'
            ELSE 'earlier' END                                             AS record_state,
       CASE WHEN p.rule_version <> cur.current_rule_version AND rc.passes IS TRUE
            THEN cur.current_rule_version ELSE p.rule_version END          AS record_rule_version
  FROM picks_forward_test p
  JOIN picks_forward_test_arm_rule cur ON cur.arm = p.arm
  LEFT JOIN pick_rule_recheck rc
         ON rc.pick_id = p.id AND rc.checked_rule_version = cur.current_rule_version
 WHERE p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
   AND NOT (p.grade IS NOT DISTINCT FROM 'D' AND p.telegram_message_id IS NULL);

-- The /performance bot record: per (arm, grade, market, record_rule_version, record_state).
-- is_current rows (native + rechecked_pass) are the bot's figures; the rest is the "earlier"
-- line. Sharp-anchor CLV exactly as migration 430 (clv_sharp, else >=5-book clv_cons; thin
-- excluded; |clv| > 1 a data fault).
CREATE OR REPLACE VIEW public.picks_forward_test_bot_record AS
WITH legs AS (
    SELECT r.*,
           CASE WHEN c.status = 'ok' THEN c.clv_sharp
                WHEN c.cons_status = 'ok' THEN c.clv_cons END  AS clv_anchor,
           CASE WHEN c.status = 'ok' THEN 'pinnacle'
                WHEN c.cons_status = 'ok' THEN 'consensus' END AS anchor_source,
           r.outcome = ANY (ARRAY['won'::text, 'lost'::text])  AS is_settled
      FROM picks_forward_test_record_leg r
      LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test' AND c.leg_id = r.id
)
SELECT arm, grade, market, record_rule_version, record_state,
       (record_rule_version = current_rule_version)                                          AS is_current,
       min(published_at)                                                                     AS started_at,
       count(*)                                                                              AS published,
       count(*) FILTER (WHERE outcome IS NULL)                                               AS pending,
       count(*) FILTER (WHERE is_settled)                                                    AS settled,
       count(*) FILTER (WHERE outcome = 'won')                                               AS won,
       coalesce(sum(pnl) FILTER (WHERE is_settled), 0)                                       AS pnl_units,
       count(clv_anchor) FILTER (WHERE is_settled AND abs(clv_anchor) <= 1)                  AS n_anchor,
       count(*) FILTER (WHERE is_settled AND anchor_source = 'pinnacle'  AND abs(clv_anchor) <= 1) AS n_pinnacle,
       count(*) FILTER (WHERE is_settled AND anchor_source = 'consensus' AND abs(clv_anchor) <= 1) AS n_consensus,
       avg(clv_anchor) FILTER (WHERE is_settled AND abs(clv_anchor) <= 1)                    AS clv_anchor,
       count(clv_margin_corrected) FILTER (WHERE is_settled AND abs(clv_margin_corrected) <= 1) AS n_clv_mc,
       avg(clv_margin_corrected) FILTER (WHERE is_settled AND abs(clv_margin_corrected) <= 1)   AS clv_margin_corrected
  FROM legs
 GROUP BY arm, grade, market, record_rule_version, record_state, current_rule_version;

COMMENT ON VIEW public.picks_forward_test_bot_record IS
  '#158: the /performance forward-test bot record. is_current = native current-rule picks + earlier-rule picks that passed the current rule on pick-time data (pick_rule_recheck). PRIVATE — read server-side with the service client. The pre-registered test counts stay on picks_forward_test_summary (rule_version as published).';

REVOKE ALL ON public.picks_forward_test_arm_rule, public.picks_forward_test_record_leg,
              public.picks_forward_test_bot_record
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.picks_forward_test_arm_rule, public.picks_forward_test_record_leg,
                public.picks_forward_test_bot_record TO service_role;

-- The bot's bet list reads the anon view picks_forward_test_public. Two columns APPENDED (so
-- CREATE OR REPLACE keeps every grant): record_rule_version (the list is scoped on it, so the
-- list a reader counts reconciles with the row) and record_state. They expose only the
-- verdict, never the details jsonb.
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
    p.grade,
    coalesce(r.record_rule_version, p.rule_version) AS record_rule_version,
    r.record_state
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
  WHERE p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])
    AND NOT (p.grade IS NOT DISTINCT FROM 'D' AND p.telegram_message_id IS NULL);
