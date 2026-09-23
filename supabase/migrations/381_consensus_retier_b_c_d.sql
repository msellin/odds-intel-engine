-- 381 — RE-TIER THE CONSENSUS GRADES: B strongest, C standard, D weak & unpublished ([[#098]], 2026-09-23)
--
-- Owner: "make grade B serve grade A picks and grade C serve grade B picks … we
-- don't publish grade C picks at all" and "leave grade A for some model picks,
-- not the consensus bot at all". Evidence: docs/PUBLISHED_PICKS_GRADING_2026_09_23.md §3-§5.
--
--   B  strongest — clean AND odds 1.20-1.60. Positive in all three samples (ours
--      56 d +17.8%, unseen May-Jul +14.7%, Beat the Bookie 2015-16 +9.8% n=696).
--   C  standard  — clean, any other odds. Positive but unproven (+2.9% / +3.3%).
--   D  weak      — tier-0 league / a panel book disagrees / edge > 6%. Claimed to
--      the ledger, NEVER sent. Loses on our own data (-25.6% / -2.8%).
--
-- The grade is re-derived for existing rows from what is stored (old grade +
-- odds), exactly as grade_consensus_pick() now computes it, so each bot's
-- history means the same thing as its future. Order matters: old C -> D first,
-- then old B outside the 1.20-1.60 band -> C.

ALTER TABLE picks_forward_test DROP CONSTRAINT IF EXISTS picks_forward_test_grade_check;
ALTER TABLE picks_forward_test
    ADD CONSTRAINT picks_forward_test_grade_check CHECK (grade IS NULL OR grade IN ('B', 'C', 'D'));

UPDATE picks_forward_test SET grade = 'D'
 WHERE arm = 'consensus_anchor' AND grade = 'C';
UPDATE picks_forward_test SET grade = 'C'
 WHERE arm = 'consensus_anchor' AND grade = 'B'
   AND NOT (odds >= 1.20 AND odds <= 1.60);

UPDATE bots SET
    display_name   = 'Consensus picks — grade B (strongest)',
    maturity_label = 'beta',
    description    = 'Consensus picks, grade B — the STRONGEST tier since the 2026-09-23 re-tier ([[#098]]): passes every grade check AND odds 1.20-1.60. The only rule positive in all three samples (ours 56 d +17.8% n=48, unseen May-Jul +14.7% n=29, Beat the Bookie 2015-16 +9.8% n=696, Holm p<1e-4). Beta: not yet proven live.'
 WHERE name = 'bot_consensus_b_v1';

UPDATE bots SET
    display_name   = 'Consensus picks — grade C (standard)',
    maturity_label = 'testing',
    description    = 'Consensus picks, grade C — the STANDARD tier since the 2026-09-23 re-tier ([[#098]]): passes every grade check, odds outside 1.20-1.60. Positive but unproven (+2.9% unseen n=150, +3.3% external n=6,380). Testing.'
 WHERE name = 'bot_consensus_c_v1';

INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks,
    display_name
) VALUES (
    'bot_consensus_d_v1',
    'Consensus picks, grade D — WEAK, NOT PUBLISHED since 2026-09-23 ([[#098]]): tier-0 league, OR a panel book disagrees, OR edge > 6%. Recorded so its record stays checkable; its earlier picks were published as grade C and remain on /performance. Loses on our own data (-25.6% in sample, -2.8% unseen).',
    'consensus_anchor',
    'picks_forward_test WHERE arm=''consensus_anchor'' AND grade=''D''. Claimed, never sent (workers/scheduler.py skips send for grade D).',
    TRUE, 'testing', 1.00, 1.00, FALSE,
    'Consensus picks — weak (not published)'
) ON CONFLICT (name) DO NOTHING;

CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
    CASE WHEN p.arm = 'consensus_anchor' AND p.grade = 'D' THEN 'bot_consensus_d_v1'
         WHEN p.arm = 'consensus_anchor' AND p.grade = 'C' THEN 'bot_consensus_c_v1'
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
    -- grade D is recorded but never SENT; only the D picks that were actually
    -- published (as 'C', before the re-tier) carry a message id and stay shown.
    AND NOT (p.grade = 'D' AND p.telegram_message_id IS NULL)
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

