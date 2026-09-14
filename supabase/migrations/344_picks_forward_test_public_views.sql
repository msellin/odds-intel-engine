-- PICKS-PAGE-SHOW-FORWARD-TEST (2026-09-14)
--
-- /picks read `simulated_bets` filtered to calibrated bots. After migration 335
-- removed the broken O/U calibrator, nothing clears the old model floors, so
-- that query returns nothing and the page is dead. These two views are what it
-- reads instead.
--
-- WHY A VIEW AND NOT A DIRECT TABLE GRANT:
--
--  * `arm = 'live'` is enforced HERE, in the database. The junk_anchor arm is a
--    negative control that is deliberately never published; filtering it in
--    TypeScript would mean one forgotten `.eq()` away from publishing bets
--    chosen by a deliberately meaningless number. A view cannot forget.
--  * The page needs team/league/kickoff names that live in four other tables.
--    Doing that join in PostgREST embeds requires FK grants on all of them and
--    produces a nested shape the page then has to flatten.
--
-- WHAT IS EXPOSED, AND WHY ALL OF IT: every column that goes into the edge —
-- `p_sharp`, `anchor_overround`, `alignment_gap_minutes` — is readable. A pick
-- whose edge cannot be recomputed by the reader is a claim, not a measurement,
-- and this whole test exists because a number nobody could check ran unchecked
-- for months. `alignment_gap_minutes` in particular is the quantity that took
-- the backtest from +8.47% to +5.5%; it is published per row on purpose.

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
  'filter cannot be forgotten the way a client-side one can. Read path for '
  '/picks (PICKS-PAGE-SHOW-FORWARD-TEST-2026-09-14).';

-- The running number, computed in ONE place.
--
-- Any aggregate shown to a reader must be the LIVE forward-test figure with its
-- n and its confidence interval — never the +5.5% backtest, which is a prior and
-- not an achievement. Computing it in SQL means the page cannot quietly show a
-- different cut than the API, and the stopping rules can be evaluated with the
-- same query the site renders.
--
-- WON/LOST ONLY. Pushes and voids return the stake, so they carry no return and
-- are excluded from the denominator — the same rule every other ledger in this
-- system uses ('Voids excluded from settled/won/staked/pnl/clv',
-- settlement.py). That is a units convention, not a discretionary exclusion:
-- the pre-registration's "no discretionary exclusions" is about not cherry-
-- picking WHICH BETS count, and nothing here chooses.
--
-- `roi_sd` is the sample sd of the per-bet unit return, so the caller forms
--     95% CI = roi ± 1.96 · roi_sd / sqrt(n)
-- This is deliberately NOT pre-formatted into a "+X%" string: the CI is the
-- point of the number and a renderer that drops it is the failure this test
-- was set up to make impossible.

CREATE OR REPLACE VIEW picks_forward_test_summary AS
SELECT
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
 WHERE arm = 'live';

COMMENT ON VIEW picks_forward_test_summary IS
  'Running result of the pre-registered forward test, live arm only. The ONLY '
  'aggregate any public surface may show for this method — the +5.5% backtest '
  'is a prior, not a record, and must never be rendered as one. roi_sd is the '
  'per-bet sd so callers form 95%% CI = roi ± 1.96·roi_sd/sqrt(settled). '
  'clv_margin_corrected is the primary instrument (per-bet return variance is '
  '~1.32, so ROI does not resolve on any realistic timescale).';

GRANT SELECT ON picks_forward_test_public  TO anon, authenticated, service_role;
GRANT SELECT ON picks_forward_test_summary TO anon, authenticated, service_role;

-- POSTGREST-SCHEMA-RELOAD: PostgREST caches the schema at boot. Without this
-- both views 404 until the container restarts, which looks exactly like the
-- page being broken.
NOTIFY pgrst, 'reload schema';
