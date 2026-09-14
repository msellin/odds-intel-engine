-- PICKS-FORWARD-TEST-BOT (2026-09-14)
--
-- Register the pre-registered sharp-edge rule as a bot, so it appears in the
-- normal bot/shadow surfaces and accrues a ledger comparable with the others.
--
-- THE HARD CONSTRAINT, restated from migration 342's header because it is the
-- whole design: these picks must NOT become rows in `simulated_bets`, and by the
-- same argument not in `shadow_bets` either. Both are bot-scoped ledgers with
-- staking semantics (Kelly fraction, stake, bankroll). Every bot-cohort query
-- ever written — the /admin/shadow-bots aggregates, the per-bot sweeps, the
-- promotion gates, the dedup views — selects from them by bot_id with no idea
-- that one of those bots is a flat-stake published-picks ledger under a
-- pre-registered stopping rule. Putting these rows there would silently
-- re-contaminate the track record we spent 2026-09-14 cleaning, and it would do
-- it in a way that looks like nothing at all.
--
-- So the bot row is a HANDLE, not a writer. Nothing writes bets for it. What it
-- gets instead is a read-only PROJECTION over `picks_forward_test`, shaped like
-- `shadow_bets` so an existing reader can consume it, and named so that nobody
-- mistakes it for the real thing.
--
-- Why a bots row exists at all rather than no row: `workers/registry/
-- bot_registry.py` is the single source of truth for what every active bot IS,
-- and the smoke test SYSTEM-MAP-REGISTRY-NOT-DRIFTED asserts the registry's
-- active set equals this table's. A strategy that generates published picks and
-- is absent from the registry is exactly the "silent bot" that test exists to
-- prevent.
--
-- Bankroll is 1, not the 10,000 default. This bot has no bankroll: it stakes a
-- flat 1 unit and nothing ever updates the column, because nothing writes bets
-- for it. A 10,000 sitting here would be counted by every bankroll rollup as if
-- it were capital at risk. 1 is the smallest honest value — `bots` has
-- CHECK (starting_bankroll > 0), so 0 is not available, and it also happens to
-- be the unit this ledger is denominated in.

INSERT INTO bots (name, strategy, description, strategy_description,
                  starting_bankroll, current_bankroll, is_active, maturity_label)
VALUES (
  'bot_sharp_forward_test_v1',
  'sharp_forward_test',
  'Pre-registered sharp-edge PICKS forward test. Reads through to '
  'picks_forward_test — writes NO simulated_bets and NO shadow_bets rows.',
  'edge = P_shin(Shin-de-vigged Pinnacle) x best_book_price - 1 >= 3%, '
  'odds <= 4.0, anchor and bet quote within 60 min, markets 1x2 + O/U 2.5, '
  'top 8 per day by edge. Uses NO model output. Flat 1-unit stake, no Kelly, '
  'no bankroll. Rule and stopping criteria locked in '
  'dev/active/picks-forward-test-preregistration.md; changing either '
  'invalidates the test and starts a new one with a new start date.',
  1, 1, TRUE, 'experimental')
ON CONFLICT (name) DO NOTHING;

-- The read-through projection.
--
-- Shaped like `shadow_bets` so an existing per-bot reader can consume it
-- unchanged, but it is a VIEW over picks_forward_test and therefore cannot be
-- written to by accident. Columns that only make sense for a staking bot are
-- NULL and stay NULL: this rule has no model probability (that is the point),
-- no Kelly fraction, no model version.
--
-- `stake` is 1 and `pnl` is already in units, so SUM(pnl)/COUNT(*) is the ROI
-- directly. Do NOT pool these numbers with shadow_bets' — same column names,
-- different units (money at a Kelly stake there, units at a flat stake here).
--
-- BOTH ARMS are exposed here, unlike `picks_forward_test_public`. This is the
-- operator surface: the junk-anchor negative control is the thing that tells us
-- whether the harness works, and hiding it from the operator would defeat it.
-- `arm` is carried through as `shadow_cohort` so any aggregate must GROUP BY it
-- or be obviously wrong.

CREATE OR REPLACE VIEW picks_forward_test_shadow AS
SELECT p.id,
       NULL::uuid                       AS shadow_run_id,
       p.arm                            AS shadow_cohort,
       b.id                             AS bot_id,
       p.match_id,
       p.market,
       p.selection,
       p.odds                           AS odds_at_pick,
       p.published_at                   AS pick_time,
       1::numeric                       AS stake,
       NULL::numeric                    AS model_probability,
       p.p_sharp                        AS calibrated_prob,
       p.edge                           AS edge_percent,
       p.bookmaker                      AS recommended_bookmaker,
       NULL::numeric                    AS kelly_fraction,
       p.rule_version                   AS model_version,
       p.closing_odds,
       p.clv,
       p.clv_margin_corrected,
       COALESCE(p.outcome, 'pending')   AS result,
       p.pnl,
       p.published_at                   AS created_at,
       p.closing_bookmaker,
       p.alignment_gap_minutes,
       p.settled_at
  FROM picks_forward_test p
  CROSS JOIN (SELECT id FROM bots WHERE name = 'bot_sharp_forward_test_v1') b;

COMMENT ON VIEW picks_forward_test_shadow IS
  'READ-ONLY projection of picks_forward_test in shadow_bets shape, for '
  'bot_sharp_forward_test_v1. These picks are deliberately NOT rows in '
  'simulated_bets or shadow_bets — see migration 342. `calibrated_prob` here is '
  'P_shin (a DE-VIGGED SHARP probability), NOT a model probability, and '
  'edge_percent is a MULTIPLICATIVE sharp edge, not the probability-point model '
  'edge the same column name carries in shadow_bets. stake is a flat 1 unit and '
  'pnl is in units, so SUM(pnl)/COUNT(*) is the ROI directly — never pool these '
  'with shadow_bets numbers. shadow_cohort carries the ARM: aggregate without '
  'grouping by it and the junk-anchor control is mixed into the live result.';

-- Per-arm running result for the operator surface. `picks_forward_test_summary`
-- (migration 344) is the PUBLIC one and is live-arm only; this one shows both,
-- because the negative control is only useful to somebody who can see it.
CREATE OR REPLACE VIEW picks_forward_test_arm_summary AS
SELECT arm,
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
 GROUP BY arm;

COMMENT ON VIEW picks_forward_test_arm_summary IS
  'Running result per ARM — operator surface only. The live arm is the test; '
  'the junk_anchor arm is the negative control and is expected to lose roughly '
  'the vig. If the junk arm makes money the harness is broken and the live arm '
  'means nothing. NOTE the 2026-09-14 junk rows are degenerate (they duplicate '
  'the live arm exactly, JUNK-ARM-DEGENERATE-2026-09-14) and carry a '
  'rule_version ending +DEGENERATE_JUNK_DAY1. avg/max_gap_min surface the '
  'anchor-to-bet alignment, whose drift above 60 min is a pre-registered '
  'early-stop trigger.';

-- service_role only. Both views carry the junk-anchor arm, which must never
-- reach a reader; the operator page authenticates with the service key and is
-- superadmin-gated on top.
GRANT SELECT ON picks_forward_test_shadow      TO service_role;
GRANT SELECT ON picks_forward_test_arm_summary TO service_role;

NOTIFY pgrst, 'reload schema';
