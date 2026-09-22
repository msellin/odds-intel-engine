-- 373 — a postponed match is not a pick ([[#068]], 2026-09-22)
--
-- Owner: *"we should not show postponed matches, also not send them to telegram
-- if possible."*
--
-- The SEND half shipped earlier today: `load_candidates` now requires
-- `m.status = 'scheduled'`, after 3 of the consensus arm's first 20 picks went
-- to 62 subscribers on matches that had been marked postponed THREE HOURS
-- before we published them.
--
-- This is the SHOW half, and it was still open: `picks_public_all` never looked
-- at match status, so those same 3 picks were still sitting on /picks after the
-- publisher stopped creating new ones. Fixing the writer does not clean the
-- window.
--
-- Applied to BOTH legs of the union — the model arm reads `simulated_bets` and
-- had the same hole; it simply had no picks today to expose it.
--
-- `<> 'postponed'` rather than `= 'scheduled'` HERE, deliberately, and it is the
-- opposite choice to the publisher's. The publisher creates picks and must
-- refuse anything it does not understand, so it uses an allow-list. This view
-- also serves rows whose match has since gone `live` or `finished`, and an
-- allow-list would blank them out mid-match — the reader would watch their own
-- pick disappear at kickoff. Only `postponed` means "this is not happening".
-- Statuses in the table: finished, postponed, scheduled, live.
--
-- The LEDGER is untouched. Voided picks stay in `picks_forward_test` and on
-- /performance, because deleting a published pick from the record is exactly
-- what the forward test exists to make impossible. This hides them from the
-- UPCOMING feed only.
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
    NULL::text AS arm, NULL::text AS anchor_bookmaker
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL
    AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL
    AND m.status <> 'postponed';
