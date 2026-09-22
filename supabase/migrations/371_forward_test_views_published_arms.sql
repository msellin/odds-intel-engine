-- 371 — /performance must show every arm a reader actually receives ([[#068]], 2026-09-22)
--
-- THE DEFECT, and it is a public-honesty one. `picks_forward_test_public` and
-- `picks_forward_test_summary` both filtered `arm = 'live'`, written when 'live'
-- was the only arm that reached anyone. Migration 368 added a SECOND published
-- arm. The result, on the day it shipped: **20 picks sent to Telegram, shown on
-- /picks, and invisible on /performance** — a reader could receive a pick and
-- find no track record for it anywhere.
--
-- This is PICKS-SHOW-BOTH-BOTS (2026-09-16) in mirror image. That fix existed
-- because `bot_v10_all` published to Telegram and showed on /performance while
-- being missing from /picks. The same class of bug, the same day of the week,
-- the opposite direction — because the surfaces are gated independently instead
-- of from one fact.
--
-- THE RULE, stated once so the next arm inherits it: **if it is published, its
-- record is published.** The arm list here is the same list the publisher uses
-- (`PUBLISHED_ARMS` in scripts/publish_picks_forward_test.py) and the same one
-- migration 368 put on `picks_public_all`.
--
-- `junk_anchor` is excluded BY NAME, not by omission. It is the negative control
-- — recorded, never sent — and an arm list that silently admits whatever is
-- added next is how a control ends up in front of customers.
--
-- THE TWO ARMS STAY SEPARATE. `arm` is exposed on both views and the summary
-- groups by it, so each arm keeps its own n, ROI and CLV. Pooling them would
-- destroy the thing the pre-registration protects: the 'live' arm's record is of
-- ONE locked rule, and mixing a second rule into it forfeits that, which is
-- exactly what building a second arm was meant to avoid.
CREATE OR REPLACE VIEW picks_forward_test_public AS
 SELECT p.id, p.match_id, p.market, p.selection, p.odds, p.bookmaker, p.edge,
    p.p_sharp, p.anchor_bookmaker, p.anchor_overround, p.alignment_gap_minutes,
    p.rule_version, p.kickoff_at, p.published_at, p.outcome, p.pnl,
    p.closing_odds, p.closing_bookmaker, p.clv, p.clv_margin_corrected,
    p.settled_at,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team,
    p.arm                                  -- appended: see migration 368's note
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE p.arm IN ('live', 'consensus_anchor');

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
    arm                                    -- appended, and GROUPED BY: one record per arm
   FROM picks_forward_test
  WHERE arm IN ('live', 'consensus_anchor')
  GROUP BY rule_version, arm;
