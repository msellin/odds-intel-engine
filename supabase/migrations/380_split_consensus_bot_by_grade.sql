-- 380 — SPLIT THE CONSENSUS BOT INTO TWO BOTS BY GRADE ([[#095]], 2026-09-23)
--
-- Owner: "label the consensus bets with grade label, B and C, in picks page and
-- in telegram. split the consensus bot into 2 separate bots based on the grade"
-- and "make grade B beta and grade C testing".
--
-- WHY SPLIT IN THE VIEWS, NOT IN THE LEDGER. Every consensus pick stays in
-- picks_forward_test with arm = 'consensus_anchor'; the two bots are the two
-- values of `grade` (migration 379). Giving each grade its own `arm` instead
-- would break the unique index (match_id, market, selection, arm) as a de-dupe:
-- a leg graded B at 12:15 and C at 12:45 would be claimed — and SENT — twice.
-- One arm, one selection rule, one de-dupe; the grade is only who owns the row.
--
-- The evidence behind the two labels (docs/PUBLISHED_PICKS_GRADING_2026_09_23.md,
-- 56-day replay, n=677): grade C ROI -25.6% (-26.0 / -24.7 in both halves);
-- grade B +10.6% but -3.4% in the holdout — so B is `beta`, not proven, and C is
-- `testing`, tracked in the open to be retired on its own record.
--
-- All 44 consensus picks published before this migration were graded by
-- scripts/backfill_consensus_grades.py, so both bots start with full history
-- from 2026-09-22. A NULL grade (should not occur) falls to grade B's bot rather
-- than vanishing, and is visible as such.

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks,
    display_name
) VALUES
(
    'bot_consensus_b_v1',
    'Consensus picks, grade B ([[#095]]) — the consensus arm''s picks that pass every grade check: a classified league, a second sharp book agreeing there is an edge at the published price, and an edge no larger than 6%. Replay n=392, ROI +10.6% but -3.4% in the holdout, so NOT proven — beta.',
    'consensus_anchor',
    'picks_forward_test WHERE arm=''consensus_anchor'' AND grade=''B''. Selection is the unchanged consensus rule (scripts/publish_picks_forward_test.py, CONSENSUS_RULE_VERSION); grade_consensus_pick() labels each leg. Published to Telegram and /picks. Never staked.',
    TRUE, 'beta', 1.00, 1.00, FALSE,
    'Consensus picks — grade B'
),
(
    'bot_consensus_c_v1',
    'Consensus picks, grade C ([[#095]]) — the consensus arm''s weaker picks: league tier 0, OR no second sharp book sees an edge at the published price, OR edge above 6%. Replay n=285, ROI -25.6% (-26.0 / -24.7 in both halves). Published and tracked in the open as TESTING; retire on its own record.',
    'consensus_anchor',
    'picks_forward_test WHERE arm=''consensus_anchor'' AND grade=''C''. Same rule and same ledger as grade B; only the label differs. Published to Telegram and /picks. Never staked.',
    TRUE, 'testing', 1.00, 1.00, FALSE,
    'Consensus picks — grade C'
)
ON CONFLICT (name) DO NOTHING;

-- The parent is superseded, not deleted: its rows now belong to the two bots.
UPDATE bots SET retired_at = now(), is_active = FALSE WHERE name = 'bot_consensus_anchor_v1' AND retired_at IS NULL;

-- /picks — each forward-test row is labelled with the bot that owns it, and
-- `grade` is appended (CREATE OR REPLACE VIEW can only APPEND columns).
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
    CASE WHEN p.arm = 'consensus_anchor' AND p.grade = 'C' THEN 'bot_consensus_c_v1'
         WHEN p.arm = 'consensus_anchor'                   THEN 'bot_consensus_b_v1'
         ELSE 'bot_sharp_forward_test_v1' END AS bot,
    p.match_id, p.market, p.selection, p.odds, p.bookmaker, p.edge,
    p.p_sharp AS fair_prob,
    p.rule_version,
    p.alignment_gap_minutes,
    p.published_at, p.outcome, p.clv,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team,
    p.arm, p.anchor_bookmaker,
    p.grade
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE p.arm IN ('live', 'consensus_anchor')
    AND m.status <> 'postponed'
UNION ALL
 SELECT s.id,
    'model'::text AS edge_kind,
    b.name AS bot,
    s.match_id, s.market, s.selection,
    s.odds_at_pick AS odds,
    s.recommended_bookmaker AS bookmaker,
        CASE
            WHEN s.calibrated_prob IS NOT NULL AND s.odds_at_pick > 0::numeric
            THEN round(s.calibrated_prob - 1.0 / s.odds_at_pick, 6)
            ELSE s.edge_percent::numeric
        END AS edge,
    s.calibrated_prob AS fair_prob,
    s.model_version AS rule_version,
    NULL::numeric AS alignment_gap_minutes,
    s.pick_time AS published_at,
    NULLIF(s.result::text, 'pending'::text) AS outcome,
    s.clv,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team,
    NULL::text AS arm, NULL::text AS anchor_bookmaker,
    NULL::text AS grade
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL
    AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL
    AND m.status <> 'postponed';

-- /performance bet list — `grade` appended so the web can split by bot.
CREATE OR REPLACE VIEW picks_forward_test_public AS
 SELECT p.id, p.match_id, p.market, p.selection, p.odds, p.bookmaker, p.edge,
    p.p_sharp, p.anchor_bookmaker, p.anchor_overround, p.alignment_gap_minutes,
    p.rule_version, p.kickoff_at, p.published_at, p.outcome, p.pnl,
    p.closing_odds, p.closing_bookmaker, p.clv, p.clv_margin_corrected,
    p.settled_at,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team,
    p.arm,
    p.grade
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE p.arm IN ('live', 'consensus_anchor');

-- /performance record — one row per (rule_version, arm, grade). The live arm
-- has grade NULL, so it still yields exactly one row per rule version.
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
  WHERE arm IN ('live', 'consensus_anchor')
  GROUP BY rule_version, arm, grade;
