-- FORWARD-TEST-SUMMARY-POOLS-RULE-VERSIONS (2026-09-15)
--
-- Both summary views shipped in migrations 344 and 345 aggregate the WHOLE
-- `picks_forward_test` table per arm. That was correct for exactly one day —
-- the day there was one rule.
--
-- RULE-V2-2026-09-15 closed `sharp_edge_v1_2026_09_14` at n=8 and registered
-- `sharp_edge_v2_2026_09_15` (adding MAX_RATIO = 0.20, because six of v1's eight
-- picks sat >20% over the anchor and the 20-35% band backtests at -12.13%). The
-- pre-registration is explicit that a rule change starts a NEW test and that
-- v1's picks are NOT pooled with v2's:
--
--   "Quietly tightening a running pre-registered rule and carrying its n forward
--    is the exact discipline failure the document exists to prevent."
--
-- An aggregate that sums across `rule_version` does precisely that, silently,
-- in the one place the number is read from — /picks' "Running result", and the
-- operator panel the stopping rules are eyeballed on. v1's eight picks would
-- have been carried into v2's n, and a checkpoint at n=200 would have fired
-- eight picks early on a mix of two different rules.
--
-- Fix: `rule_version` joins the GROUP BY in both views. Nothing is filtered out
-- and nothing is hidden — a closed version keeps its own row, which is what a
-- closed pre-registered test should have. Callers pick the current rule (the one
-- with the latest `started_at`) rather than the sum of all of them.
--
-- Note the degenerate day-one junk rows fall out naturally: they carry
-- `rule_version` ending `+DEGENERATE_JUNK_DAY1` (migration 343) and now sit in
-- their own row instead of being mixed into the junk arm's totals.

DROP VIEW IF EXISTS picks_forward_test_summary;

CREATE VIEW picks_forward_test_summary AS
SELECT
    rule_version,
    min(published_at)                                            AS started_at,
    count(*)                                                     AS published,
    count(*) FILTER (WHERE outcome IS NULL)                      AS pending,
    count(*) FILTER (WHERE outcome IN ('push','void'))           AS refunded,
    count(*) FILTER (WHERE outcome IN ('won','lost'))            AS settled,
    count(*) FILTER (WHERE outcome = 'won')                      AS won,
    sum(pnl)        FILTER (WHERE outcome IN ('won','lost'))     AS pnl_units,
    avg(pnl)        FILTER (WHERE outcome IN ('won','lost'))     AS roi,
    stddev_samp(pnl) FILTER (WHERE outcome IN ('won','lost'))    AS roi_sd,
    count(clv)                   FILTER (WHERE outcome IN ('won','lost')) AS n_clv,
    avg(clv)                     FILTER (WHERE outcome IN ('won','lost')) AS clv_raw,
    count(clv_margin_corrected)  FILTER (WHERE outcome IN ('won','lost')) AS n_clv_mc,
    avg(clv_margin_corrected)    FILTER (WHERE outcome IN ('won','lost')) AS clv_margin_corrected,
    stddev_samp(clv_margin_corrected) FILTER (WHERE outcome IN ('won','lost')) AS clv_mc_sd
  FROM picks_forward_test
 WHERE arm = 'live'
 GROUP BY rule_version;

COMMENT ON VIEW picks_forward_test_summary IS
  'Running result of the pre-registered forward test, live arm, ONE ROW PER '
  'rule_version. Never SUM across rows: a rule change starts a new test with a '
  'new start date and its own n (RULE-V2-2026-09-15 closed v1 at n=8), and '
  'pooling them carries a closed test''s n into a running one — the exact '
  'discipline failure the pre-registration exists to prevent. The current test '
  'is the row with the latest started_at. The +5.5%% backtest is a prior, not a '
  'record, and must never be rendered as one. roi_sd is the per-bet sd so '
  'callers form 95%% CI = roi ± 1.96·roi_sd/sqrt(settled). clv_margin_corrected '
  'is the primary instrument — per-bet return variance is ~1.32, so ROI does not '
  'resolve on any realistic timescale.';

DROP VIEW IF EXISTS picks_forward_test_arm_summary;

CREATE VIEW picks_forward_test_arm_summary AS
SELECT rule_version,
       arm,
       min(published_at)                                         AS started_at,
       count(*)                                                  AS published,
       count(*) FILTER (WHERE outcome IS NULL)                   AS pending,
       count(*) FILTER (WHERE outcome IN ('push','void'))         AS refunded,
       count(*) FILTER (WHERE outcome IN ('won','lost'))          AS settled,
       count(*) FILTER (WHERE outcome = 'won')                    AS won,
       sum(pnl)         FILTER (WHERE outcome IN ('won','lost'))  AS pnl_units,
       avg(pnl)         FILTER (WHERE outcome IN ('won','lost'))  AS roi,
       stddev_samp(pnl) FILTER (WHERE outcome IN ('won','lost'))  AS roi_sd,
       count(clv_margin_corrected) FILTER (WHERE outcome IN ('won','lost'))
                                                                  AS n_clv_mc,
       avg(clv_margin_corrected)   FILTER (WHERE outcome IN ('won','lost'))
                                                                  AS clv_margin_corrected,
       stddev_samp(clv_margin_corrected) FILTER (WHERE outcome IN ('won','lost'))
                                                                  AS clv_mc_sd,
       avg(alignment_gap_minutes)                                 AS avg_gap_min,
       max(alignment_gap_minutes)                                 AS max_gap_min
  FROM picks_forward_test
 GROUP BY rule_version, arm;

COMMENT ON VIEW picks_forward_test_arm_summary IS
  'Running result per rule_version AND arm — operator surface only. The live arm '
  'is the test; junk_anchor is the negative control, expected to lose roughly '
  'the vig. If the junk arm makes money the harness is broken and the live arm '
  'means nothing. Rows are NEVER summed across rule_version: v1 '
  '(sharp_edge_v1_2026_09_14) is CLOSED at n=8 and is not pooled with v2. The '
  '2026-09-14 junk rows are additionally degenerate — they duplicate the live '
  'arm exactly (JUNK-ARM-DEGENERATE-2026-09-14) and carry a rule_version ending '
  '+DEGENERATE_JUNK_DAY1. avg/max_gap_min surface the anchor-to-bet alignment, '
  'whose drift above 60 min is a pre-registered early-stop trigger.';

-- Grants are dropped with the views, so they must be reissued. Same split as
-- before: the public summary is live-arm only, the arm summary carries the
-- negative control and stays service_role.
GRANT SELECT ON picks_forward_test_summary  TO anon, authenticated, service_role;
GRANT SELECT ON picks_forward_test_arm_summary TO service_role;

-- Also expose rule_version on the public pick list, so a surface showing picks
-- from two rules can say which is which instead of implying one continuous
-- record. CREATE OR REPLACE cannot add a column to an existing view, hence the
-- drop — and the grant below, for the same reason as above.
DROP VIEW IF EXISTS picks_forward_test_public;

CREATE VIEW picks_forward_test_public AS
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
       m.date            AS kickoff_utc,
       l.name            AS league,
       l.country         AS country,
       ht.name           AS home_team,
       at.name           AS away_team
  FROM picks_forward_test p
  JOIN matches m   ON m.id  = p.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id
 WHERE p.arm = 'live';

COMMENT ON VIEW picks_forward_test_public IS
  'The PUBLISHED arm of the pre-registered sharp-edge forward test, joined to '
  'fixture names for display. arm=''live'' is filtered IN THE VIEW — the '
  'junk_anchor negative control must never reach a reader, and a database '
  'filter cannot be forgotten the way a client-side one can. `rule_version` '
  'identifies which pre-registered rule produced a pick; picks from different '
  'rules may appear in the same window and are different tests.';

GRANT SELECT ON picks_forward_test_public TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
