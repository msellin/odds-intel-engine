-- 366_simulated_bets_settled_at.sql
--
-- BOT-AGGREGATES-SSOT-FLAKY. `dashboard_cache.bot_breakdown` is rebuilt on a
-- schedule while `simulated_bets` settle continuously, so the smoke test that
-- reconciles the two is comparing a snapshot against a moving target. A bet
-- settling between the cache write and the test's live read shows up as drift
-- that is not drift.
--
-- The existing guard only re-reads when the CACHE was rebuilt mid-test, and its
-- own comment says why it cannot do better: "simulated_bets has no settled_at
-- to bound the live read". This adds it, so the live read can be taken AS OF
-- the cache's computed_at and the race disappears rather than being retried.
--
-- Implemented as a trigger, deliberately. There are four separate UPDATE sites
-- that move a bet out of 'pending' (settlement's main path, the postponed-match
-- voider, the CLV auto-voider, plus one-off repair scripts), and editing each
-- one is how a column ends up populated on three paths out of four. A BEFORE
-- UPDATE trigger catches every writer including future ones.
--
-- Historical rows stay NULL. That is correct and is what consumers should read
-- as "settled before any cache we are comparing against" — every existing
-- settled row predates this migration by definition.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS settled_at timestamptz;

COMMENT ON COLUMN simulated_bets.settled_at IS
    'When result left ''pending'', set by trigger trg_simulated_bets_settled_at. '
    'NULL on rows settled before migration 366 — read that as "long ago". '
    'Exists so a cache-vs-live reconciliation can bound its live read AS OF the '
    'cache''s computed_at instead of racing continuous settlement.';

CREATE OR REPLACE FUNCTION set_simulated_bet_settled_at() RETURNS trigger AS $$
BEGIN
    -- Only the pending -> settled transition stamps. A later correction (a
    -- re-settle, a void of an already-graded bet) must NOT move the timestamp,
    -- or the column stops meaning "when this bet first stopped being pending".
    IF NEW.result IS DISTINCT FROM OLD.result
       AND NEW.result IS NOT NULL
       AND NEW.result <> 'pending'
       AND NEW.settled_at IS NULL THEN
        NEW.settled_at := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_simulated_bets_settled_at ON simulated_bets;
CREATE TRIGGER trg_simulated_bets_settled_at
    BEFORE UPDATE ON simulated_bets
    FOR EACH ROW
    EXECUTE FUNCTION set_simulated_bet_settled_at();

CREATE INDEX IF NOT EXISTS idx_simulated_bets_settled_at
    ON simulated_bets (settled_at)
    WHERE settled_at IS NOT NULL;
