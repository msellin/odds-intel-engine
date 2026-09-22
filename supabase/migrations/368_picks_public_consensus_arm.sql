-- 368 — PICKS-CONSENSUS-ARM-ON-THE-PAGE ([[#068]], 2026-09-22)
--
-- WHY. Migration 361 built `picks_public_all` with `WHERE p.arm = 'live'`,
-- deliberately: at the time `live` was the only arm that ever reached a reader
-- (`junk_anchor` is a negative control that is recorded and never sent), and
-- pinning the arm in the DATABASE meant a view could not forget it.
--
-- [[#068]] adds a SECOND arm that genuinely publishes. Leaving the filter as-is
-- would ship an incoherence: the Telegram message for a consensus pick ends with
-- a link to /picks, and the reader would arrive at a page that does not contain
-- the pick they just read about. On 2026-09-22 the live arm qualified ZERO legs,
-- so that page is not merely missing one pick — it is empty while the channel is
-- active.
--
-- WHAT CHANGES. The arm filter widens from `= 'live'` to the two arms that are
-- actually published, and the arm is EXPOSED as a column so the surface can tell
-- them apart. `junk_anchor` stays excluded by name rather than by omission —
-- an arm list that silently admits whatever is added next is how a negative
-- control ends up in front of customers.
--
-- WHAT DOES NOT CHANGE. `edge_kind` stays 'sharp' for both: both arms compute
-- `edge = p_sharp * odds - 1`, a genuine expected return, and the page's
-- break-even tooltip reads `fair_prob` identically for each. The model arm's
-- `edge` remains a different quantity in the same column — see SYSTEM_MAP §1 and
-- the `edge_kind` switch on the page. Nothing about the pre-registered `live`
-- arm's selection rule, floors or ledger is touched by this migration.
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
    'bot_sharp_forward_test_v1'::text AS bot,
    p.arm,
    p.anchor_bookmaker,
    p.match_id, p.market, p.selection, p.odds, p.bookmaker, p.edge,
    p.p_sharp AS fair_prob,
    p.rule_version,
    p.alignment_gap_minutes,
    p.published_at, p.outcome, p.clv,
    m.date AS kickoff_utc,
    l.name AS league, l.country,
    ht.name AS home_team, at.name AS away_team
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
    NULL::text AS arm,
    NULL::text AS anchor_bookmaker,
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
    ht.name AS home_team, at.name AS away_team
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL
    AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL;
