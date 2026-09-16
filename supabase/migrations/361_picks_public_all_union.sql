-- PICKS-SHOW-BOTH-BOTS (2026-09-16)
--
-- THE PROBLEM. `bot_v10_all` publishes to the public Telegram channel and has
-- been on /performance for months, but it has never appeared on /picks. The
-- owner reported it on 2026-09-15 — "i see 15 Sept Ludogorets II vs Fratria 1x2
-- home 4.00 P +16.0 pct bet on botv10 in performance page, but not on picks
-- page, why?" — and again as "these systems need to be combined and users need to
-- have those 3 picks as well".
--
-- It was never a flag: the two bot families write to two DIFFERENT LEDGERS.
-- The sharp-anchored publisher writes `picks_forward_test`; every model-anchored
-- bot writes `simulated_bets`. /picks reads only the first. Migration 356 added
-- `bots.show_on_picks` intending to gate this and then nothing ever read it.
-- This view is what reads it.
--
-- ⚠️ THE ONE THING THAT MUST NOT HAPPEN HERE: the two `edge` numbers are NOT
-- the same quantity and must never be rendered under one label.
--
--     sharp edge = p_sharp * odds - 1        (a MULTIPLICATIVE return, vs a
--                                             Shin-de-vigged Pinnacle line)
--     model edge = calibrated_prob - 1/odds  (a PROBABILITY-POINT difference,
--                                             vs our own model)
--
-- SYSTEM_MAP section 1 has said for weeks that these are not comparable. A
-- reader who sees "+16.0 pct" beside "+3.3 pct" and concludes the first is five
-- times better has been misled by us: a 16-POINT model edge at odds of 4.00 is
-- roughly +64 pct expected return, while a 3.3 sharp edge really is 3.3 pct.
--
-- (No literal percent signs anywhere in this file, including comments.
--  Migrations run through `psql -f`, which does not care — but the repo's own
--  psycopg2 helper treats a bare percent as a parameter placeholder and dies
--  with an opaque IndexError. ANALYSIS_GOTCHAS 59(d); it has now cost time
--  three times, including once on this very file.)
--
-- MODEL-EDGE-LABEL (2026-09-15) fixed exactly this on Telegram, where the two
-- families already shared a channel; putting them on one page re-creates it.
--
-- So `edge_kind` is NOT optional metadata. It is the column that tells the
-- renderer which label to draw, it is NOT NULL by construction, and the page is
-- pinned by smoke test PICKS-SHOW-BOTH-BOTS to switch on it.
--
-- `fair_prob` is the same idea for the break-even price: p_sharp for the sharp
-- arm, calibrated_prob for the model arm. Break-even is 1/fair_prob in both
-- cases, but it is break-even AGAINST A DIFFERENT ANCHOR, so the tooltip has to
-- say which — again driven off `edge_kind`.
--
-- WHAT IS DELIBERATELY EXCLUDED FROM THE MODEL ARM:
--   * `arm <> 'live'`      — the junk-anchor negative control (sharp side), as before
--   * `b.show_on_picks`    — the curation switch; default FALSE, so a new bot is
--                            invisible to customers until someone says otherwise
--   * `b.retired_at`       — a retired bot's old picks are not an offer
--   * `combo_legs`         — a combo is not a single-market pick this page can render
--   * `match_minute_at_pick` — in-play picks; /picks is pre-match only, and
--                            in-play betting was retired 2026-08-21
--
-- DROP + CREATE, not CREATE OR REPLACE: migrations must be re-appliable, and
-- CREATE OR REPLACE VIEW cannot add or remove a column (RE-APPLIABLE-MIGRATIONS
-- -2026-09-15, learned when migration 355 took the whole job down).
DROP VIEW IF EXISTS picks_public_all;

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
       s.edge_percent                    AS edge,   -- calibrated_prob - 1/odds,
                                                    -- stored as a FRACTION
                                                    -- (ANALYSIS_GOTCHAS 48)
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
  'are NOT comparable and must never share a label — see SYSTEM_MAP section 1 '
  'and MODEL-EDGE-LABEL. fair_prob is the matching anchor probability, so '
  'break-even is 1/fair_prob against whichever anchor edge_kind names.';

GRANT SELECT ON picks_public_all TO anon, authenticated;

-- PICKS-SHOW-BOTH-BOTS: turn the model bot on for customers. It already
-- publishes to the public Telegram channel and already appears on /performance
-- as CALIBRATED (641 settled), so /picks was the one surface that omitted it.
-- Every other bot stays FALSE — the column defaults FALSE precisely so a new
-- strategy is invisible to customers until someone decides otherwise.
UPDATE bots SET show_on_picks = true WHERE name = 'bot_v10_all';

NOTIFY pgrst, 'reload schema';
