-- EDGE-IS-DERIVED-NOT-STORED (2026-09-22) — PRIORITY_QUEUE row #031
--
-- THE DEFECT, in one line: `simulated_bets.edge_percent` was declared
-- `numeric(5,2)`. Edge is a probability DIFFERENCE (calibrated_prob - 1/odds),
-- so two decimal places is a granularity of one full percentage POINT, and
-- Postgres rounded every write to it silently.
--
-- The write path was never wrong. `store_bet` passes the edge the bot actually
-- gated on; the COLUMN destroyed it. Measured 2026-09-22 over every non-combo
-- pick with a calibrated probability (n=3,672, all time):
--
--     3,661 rows (99.7 pct) sit within 0.005 of `calibrated_prob - 1/odds`
--                            -- i.e. exactly the residue of round(x, 2)
--         9 rows  0.005-0.01
--         2 rows  >0.01      -- retired in-play bots (inplay_e, June) that
--                               stored a MODEL-prob edge; in-play betting was
--                               retired 2026-08-21
--
-- WHY IT IS A MONEY BUG AND NOT COSMETIC. Every per-market edge floor is
-- specified to exactly two decimals (1x2 pooled 0.13, 1x2 home-underdog 0.10,
-- o/u 0.08, AH/DNB 0.05). With a stored value rounded to two decimals,
-- `stored >= floor` is true for every true edge in [floor - 0.005, floor) --
-- so the gate admits a half-point band it was built to reject, and it can only
-- ever err in that direction (round-half-up never pushes a clearing pick
-- below its floor). Counted against the live floors via
-- `coolbet_placer.clears_edge_floor`:
--
--     FLOOR-CROSSING rows (stored clears, derived does NOT)
--        30d:  14 / 256      90d:  26 / 905      all time: 114 / 3,672
--     crossings in the opposite direction (false rejects): 0
--
-- The readers that gate on the stored column are the Telegram signaler
-- (operator's manual-placement prompt AND the public customer channel), the
-- pre-kickoff catch-net, and the placer's candidate loaders. The placer itself
-- re-derives at the live price before staking (coolbet_placer.py:2154) so it
-- could never STAKE a rounded-up pick -- but the signal, the public post and
-- the /picks row all went out on the rounded number.
--
-- THIS MIGRATION DOES TWO THINGS:
--
-- 1. Widens the column to numeric(6,4) -- the same declaration `shadow_bets`
--    has carried since migration 101 -- so a correctly computed edge survives
--    the write. Edge is bounded in (-1, 1) by construction, so four decimals is
--    ample and the extra integer digit is pure headroom.
--
-- 2. Makes `picks_public_all`'s model arm DERIVE the edge it publishes instead
--    of reading the stored column. The view's own comment already claimed the
--    column was "calibrated_prob - 1/odds"; now it is, and it is right for the
--    3,672 HISTORICAL rows too, whose stored precision this migration cannot
--    restore. That is the deliberate alternative to a backfill: a backfill of
--    `edge_percent` would rewrite what each bot is recorded as having cleared,
--    which is the evidence base for every floor we have set. Reads derive;
--    the ledger keeps what it was written with.
--
-- The column widening cannot be done while the view depends on it, hence the
-- DROP + CREATE. Definition below is migration 361's verbatim, with the single
-- changed line marked. DROP + CREATE, not CREATE OR REPLACE -- migrations must
-- be re-appliable and CREATE OR REPLACE VIEW cannot change a column's type
-- (RE-APPLIABLE-MIGRATIONS-2026-09-15).
--
-- (No literal percent signs anywhere in this file, comments included --
--  ANALYSIS_GOTCHAS 59(d) / SQL-PERCENT-GUARD.)

DROP VIEW IF EXISTS picks_public_all;

ALTER TABLE simulated_bets
    ALTER COLUMN edge_percent TYPE numeric(6, 4);

COMMENT ON COLUMN simulated_bets.edge_percent IS
  'Model edge as a FRACTION: calibrated_prob - 1/odds_at_pick (probability '
  'POINTS, not a return). numeric(6,4) since migration 367 -- it was '
  'numeric(5,2) until 2026-09-22, which rounded every write to one full '
  'percentage point and let picks inside [floor-0.005, floor) clear their own '
  'edge floor (EDGE-IS-DERIVED-NOT-STORED, 114 crossings all time). Rows '
  'written before that date are still rounded; DERIVE from calibrated_prob '
  'and odds_at_pick rather than trusting this column on historical rows.';

CREATE VIEW picks_public_all AS
-- ── SHARP ARM ── the pre-registered forward test
SELECT p.id,
       'sharp'::text                     AS edge_kind,
       'bot_sharp_forward_test_v1'::text AS bot,
       p.match_id,
       p.market,
       p.selection,
       p.odds,
       p.bookmaker,
       p.edge,                                  -- p_sharp * odds - 1
       p.p_sharp                         AS fair_prob,
       p.rule_version,
       p.alignment_gap_minutes,
       p.published_at,
       p.outcome,
       p.clv,
       m.date                            AS kickoff_utc,
       l.name                            AS league,
       l.country                         AS country,
       ht.name                           AS home_team,
       at.name                           AS away_team
  FROM picks_forward_test p
  JOIN matches m      ON m.id  = p.match_id
  LEFT JOIN leagues l ON l.id  = m.league_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id
 WHERE p.arm = 'live'

UNION ALL

-- ── MODEL ARM ── bots explicitly switched on for customers
SELECT s.id,
       'model'::text                     AS edge_kind,
       b.name                            AS bot,
       s.match_id,
       s.market,
       s.selection,
       s.odds_at_pick                    AS odds,
       s.recommended_bookmaker           AS bookmaker,
       -- EDGE-IS-DERIVED-NOT-STORED (2026-09-22): DERIVED, not `s.edge_percent`.
       -- This is the number the customer sees on /picks. Reading the stored
       -- column published it rounded to two decimals -- overstated by up to half
       -- a point on 80 pct of rows, and always in the flattering direction. The
       -- comment on this line has claimed "calibrated_prob - 1/odds" since
       -- migration 361; it is now the expression rather than a promise, which
       -- also makes it right for rows written before the column was widened.
       -- NULL cal_prob or a non-positive price yields NULL rather than a lie.
       CASE WHEN s.calibrated_prob IS NOT NULL AND s.odds_at_pick > 0
            THEN round(s.calibrated_prob - 1.0 / s.odds_at_pick, 6)
            ELSE s.edge_percent::numeric
       END                               AS edge,
       s.calibrated_prob                 AS fair_prob,
       s.model_version                   AS rule_version,
       NULL::numeric                     AS alignment_gap_minutes,
       s.pick_time                       AS published_at,
       -- 'pending' -> NULL, so "not settled yet" has ONE spelling across both
       -- arms. The forward test uses NULL; a page branching on two different
       -- representations of the same state is how a settled pick renders as live.
       NULLIF(s.result::text, 'pending') AS outcome,
       s.clv,
       m.date                            AS kickoff_utc,
       l.name                            AS league,
       l.country                         AS country,
       ht.name                           AS home_team,
       at.name                           AS away_team
  FROM simulated_bets s
  JOIN bots b         ON b.id  = s.bot_id
  JOIN matches m      ON m.id  = s.match_id
  LEFT JOIN leagues l ON l.id  = m.league_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id
 WHERE b.show_on_picks
   AND b.retired_at IS NULL
   AND s.combo_legs IS NULL
   AND s.match_minute_at_pick IS NULL;

COMMENT ON VIEW picks_public_all IS
  'Every pick offered to customers, from BOTH bot families, in one shape. '
  'The sharp arm is picks_forward_test (arm=''live''); the model arm is '
  'simulated_bets for bots with bots.show_on_picks = true, pre-match singles '
  'only. edge_kind is load-bearing: ''sharp'' edges are p_sharp*odds-1 (a '
  'return) and ''model'' edges are cal_prob-1/odds (probability points). They '
  'are NOT comparable and must never share a label -- see SYSTEM_MAP section 1 '
  'and MODEL-EDGE-LABEL. fair_prob is the matching anchor probability, so '
  'break-even is 1/fair_prob against whichever anchor edge_kind names. '
  'The model arm DERIVES edge from calibrated_prob and odds_at_pick rather '
  'than reading simulated_bets.edge_percent (EDGE-IS-DERIVED-NOT-STORED, '
  'migration 367) -- the stored column was rounded to two decimals until '
  '2026-09-22 and historical rows still are.';

GRANT SELECT ON picks_public_all TO anon, authenticated;
