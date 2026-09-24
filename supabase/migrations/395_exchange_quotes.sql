-- 395 — BETFAIR EXCHANGE QUOTES ([[#117]], 2026-09-24)
--
-- A second sharp reference beside AF-Pinnacle. Read from Betfair's public exchange
-- pages through a London exit (the exchange serves no markets to our Finnish VPS),
-- reading prices only, never betting.
--
-- Its OWN table, deliberately not odds_snapshots: an exchange quote is two prices
-- (back AND lay) plus how much money stands behind them, and a thin market is a
-- placeholder (e.g. 1.10 back / 110 lay, €0 matched) that would poison every
-- consensus and anchor that reads odds_snapshots. It joins the anchor only after
-- its sharpness vs Pinnacle is measured by liquidity tier.

CREATE TABLE IF NOT EXISTS exchange_quotes (
    id              bigserial   PRIMARY KEY,
    match_id        uuid        NOT NULL,
    exchange        text        NOT NULL DEFAULT 'Betfair-Exchange',
    market          text        NOT NULL,          -- '1x2' | 'over_under_25'
    selection       text        NOT NULL,          -- home/draw/away | over/under
    back            numeric,                        -- best available to back
    back_size       numeric,                        -- EUR at that price
    lay             numeric,                        -- best available to lay
    lay_size        numeric,
    last_traded     numeric,
    market_matched  numeric,                        -- EUR matched on the whole market
    market_id       text        NOT NULL,
    event_id        text,
    minutes_to_kickoff integer,
    captured_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS exchange_quotes_match ON exchange_quotes (match_id, market, captured_at DESC);
CREATE INDEX IF NOT EXISTS exchange_quotes_time  ON exchange_quotes (captured_at);
