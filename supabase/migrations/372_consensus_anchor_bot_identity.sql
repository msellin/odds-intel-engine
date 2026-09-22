-- 372 — give the consensus arm its own bot identity ([[#068]], 2026-09-22)
--
-- Owner: *"users need to see all the picks they receive on performance page
-- under the correct bot and each individual bot roi and picks."*
--
-- Migration 371 made /performance show both published arms. But both were still
-- LABELLED `bot_sharp_forward_test_v1` — in `picks_public_all` and in the
-- leaderboard's bet list — so their picks and their ROI merged into one row. A
-- reader expanding it would see two different rules' results averaged together,
-- which is the specific confusion the 14 Sep reset existed to prevent.
--
-- They are two rules, not two runs of one rule: different anchors (single sharp
-- line vs a de-vigged consensus of 5+ books), different admissible sets, and one
-- has an 8% edge ceiling the other must never acquire. Averaging them produces a
-- number that describes neither.
--
-- `show_on_picks` is FALSE, and that is not a contradiction. That flag governs
-- the MODEL arm's route onto /picks (`simulated_bets` → `picks_public_all`);
-- this bot's picks reach the page through `picks_forward_test` and the arm
-- filter instead. Setting it TRUE would publish it twice.
INSERT INTO bots (
    name, description, strategy, strategy_description,
    is_active, maturity_label, starting_bankroll, current_bankroll, show_on_picks
) VALUES (
    'bot_consensus_anchor_v1',
    'The CONSENSUS arm of the published picks forward test ([[#068]]). Prices every pick against a de-vigged consensus of 5+ bookmakers instead of a single sharp line, and publishes at a >=3% edge with an 8% ceiling. It exists because the sharp arm''s pre-registered <=4% anchor-overround gate admitted 0 of 173 Pinnacle-priced markets on 2026-09-22 and the channel went dark; that gate is pre-registered, so it was not relaxed and this runs beside it. Tracked and reported SEPARATELY from the sharp arm — two rules, two records.',
    'consensus_anchor',
    'scripts/publish_picks_forward_test.py with anchor="consensus": de-vig each book that prices the complete market, average the probabilities (>=5 books required), then apply the SAME downstream guards as the sharp arm — 3% edge floor, 60-min alignment window, MAX_ODDS 4.0, MAX_RATIO 0.20, 45-min lead, 14h lookahead — plus an 8% edge CEILING the sharp arm does not have. Ledger is picks_forward_test WHERE arm=''consensus_anchor''. Published to Telegram and /picks. Never staked.',
    TRUE, 'experimental', 1.00, 1.00, FALSE
)
ON CONFLICT (name) DO NOTHING;

-- Label each forward-test pick with the bot that actually produced it.
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
    CASE WHEN p.arm = 'consensus_anchor'
         THEN 'bot_consensus_anchor_v1'
         ELSE 'bot_sharp_forward_test_v1' END AS bot,
    p.match_id, p.market, p.selection, p.odds, p.bookmaker, p.edge,
    p.p_sharp AS fair_prob,
    p.rule_version,
    p.alignment_gap_minutes,
    p.published_at, p.outcome, p.clv,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team,
    p.arm, p.anchor_bookmaker
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE p.arm IN ('live', 'consensus_anchor')
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
    NULL::text AS arm, NULL::text AS anchor_bookmaker
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL
    AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL;
