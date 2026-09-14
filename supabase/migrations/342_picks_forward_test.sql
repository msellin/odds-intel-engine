-- PICKS-FORWARD-TEST (2026-09-14) — the ledger for the pre-registered
-- sharp-edge forward test. See dev/active/picks-forward-test-preregistration.md
--
-- WHY A NEW TABLE rather than reusing `published_picks` or `simulated_bets`:
--
--  * `published_picks` records a MODEL probability and a model_version. This
--    rule uses no model at all — its anchor is the Shin-de-vigged Pinnacle
--    line. Writing P_shin into a column named model_probability is exactly the
--    kind of vocabulary collapse that made "edge" mean two different things for
--    months.
--  * `simulated_bets` is bot-scoped and carries staking/Kelly semantics. These
--    picks are published to readers, not staked by a bot. Attaching them to a
--    synthetic bot row would put them in every bot-cohort query ever written
--    and silently re-contaminate the track record we just finished cleaning.
--
-- THE COLUMN THAT MATTERS MOST is `alignment_gap_minutes`. The first version of
-- this backtest read +8.47% ROI; time-aligning the anchor quote against the bet
-- quote took it to +5.5%, because the selection rule was picking STALE soft-book
-- prices (median gap on selected legs: 360 minutes). That gap must be auditable
-- PER ROW and recorded at publish time. Recomputing it later from
-- odds_snapshots cannot work: snapshots are pruned, and the whole point is to
-- prove what we knew when we pressed send.

CREATE TABLE IF NOT EXISTS picks_forward_test (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id                uuid NOT NULL REFERENCES matches(id),
    market                  text NOT NULL,
    selection               text NOT NULL,

    -- what the reader was told
    odds                    numeric NOT NULL,
    bookmaker               text    NOT NULL,
    edge                    numeric NOT NULL,

    -- the anchor, in full, so the edge can be recomputed from first principles
    p_sharp                 numeric NOT NULL,
    anchor_bookmaker        text    NOT NULL DEFAULT 'Pinnacle',
    anchor_odds             jsonb   NOT NULL,   -- the full triple/pair as quoted
    anchor_overround        numeric,
    anchor_quoted_at        timestamptz NOT NULL,
    odds_quoted_at          timestamptz NOT NULL,
    alignment_gap_minutes   numeric NOT NULL,   -- see header

    -- provenance / audit
    arm                     text NOT NULL DEFAULT 'live'
                              CHECK (arm IN ('live','junk_anchor')),
    rule_version            text NOT NULL,
    kickoff_at              timestamptz NOT NULL,
    published_at            timestamptz NOT NULL DEFAULT now(),
    telegram_message_id     bigint,

    -- settlement
    outcome                 text CHECK (outcome IN ('won','lost','push','void')),
    pnl                     numeric,
    closing_odds            numeric,
    closing_bookmaker       text,
    clv                     numeric,            -- RAW price ratio, no de-vig
    clv_margin_corrected    numeric,            -- EV = (1+clv)/(1+m) - 1
    settled_at              timestamptz
);

-- One row per selection per arm. A double-send is a real risk (the signaler
-- historically had no dedup on the public path) and it would silently double
-- the apparent n of a pre-registered test.
CREATE UNIQUE INDEX IF NOT EXISTS picks_forward_test_unique
    ON picks_forward_test (match_id, market, selection, arm);

CREATE INDEX IF NOT EXISTS picks_forward_test_published
    ON picks_forward_test (published_at DESC);
CREATE INDEX IF NOT EXISTS picks_forward_test_unsettled
    ON picks_forward_test (kickoff_at) WHERE outcome IS NULL;

COMMENT ON TABLE picks_forward_test IS
  'Pre-registered sharp-edge PICKS forward test, started 2026-09-14. Rule and '
  'stopping criteria locked in dev/active/picks-forward-test-preregistration.md. '
  'Primary instrument is clv_margin_corrected, NOT roi: per-bet return variance '
  'is ~1.32, so confirming a true +3pct ROI at 80pct power needs ~15,600 bets.';
COMMENT ON COLUMN picks_forward_test.alignment_gap_minutes IS
  'Minutes between the anchor quote and the bet quote. The rule caps this at 60. '
  'Unaligned, this backtest read +8.47pct; aligned it reads +5.5pct — the '
  'difference was soft-book staleness, not edge.';
COMMENT ON COLUMN picks_forward_test.clv IS
  'RAW price ratio (odds/closing_odds - 1), no de-vig — same definition as '
  'simulated_bets.clv. Break-even is the closing book MARGIN, not zero. Use '
  'clv_margin_corrected for any decision.';
