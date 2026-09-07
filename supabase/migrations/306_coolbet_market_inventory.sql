-- 306_coolbet_market_inventory.sql
-- COOLBET-MARKET-INVENTORY-2026-09-06
--
-- Durable catalog of Coolbet market types the explorer sees but does NOT yet
-- parse. Today the unmatched-market branch only logs "UNMATCHED market (first
-- sighting)" to a per-process in-memory set — lost on restart, never queryable.
-- This table makes the menu durable so the capture-set decision (what is worth
-- storing) can be made from data instead of scraping logs.
--
-- It is an INVENTORY, not odds: a market with no Pinnacle counterpart cannot be
-- de-vigged, so presence here does not mean bettable. `times_seen` and the
-- first/last window tell us how common each unparsed market actually is.

CREATE TABLE IF NOT EXISTS coolbet_market_inventory (
    market_type_id  INTEGER      NOT NULL,
    name            TEXT         NOT NULL,
    sample_line     NUMERIC,
    first_seen      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    times_seen      BIGINT       NOT NULL DEFAULT 1,
    PRIMARY KEY (market_type_id, name)
);

COMMENT ON TABLE coolbet_market_inventory IS
  'COOLBET-MARKET-INVENTORY: durable catalog of Coolbet market types the explorer sees but does not parse. Inventory, not odds — presence != bettable (no Pinnacle de-vig anchor for many families).';
