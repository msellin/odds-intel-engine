-- 382 — PRICE VERIFICATIONS: was the recorded price really on the book's site? ([[#103]], 2026-09-23)
--
-- Owner: "instead of I doing it manually, can we set up an automated action for that?"
--
-- The first-half lead ([[#084]] → [[#103]]) shows +3.9% de-vigged CLV (t=4.0) on live
-- paper picks at a 3% floor, 83% of them at Epicbet. CLV is computed on the price WE
-- RECORDED, so a price that was never clickable scores the same as a real one. This
-- table is the automated version of a human opening the site: for each such pick, the
-- paper job re-fetches that ONE fixture straight from Epicbet's live feed (the same
-- data the website renders) within seconds of picking, and stores what it found.
--
-- It proves the price was ON THE SITE, not that the book would ACCEPT a bet at it
-- (limits, bet-slip re-pricing) — only a placement proves that.

CREATE TABLE IF NOT EXISTS price_verifications (
    id             bigserial   PRIMARY KEY,
    shadow_bet_id  uuid        NOT NULL UNIQUE REFERENCES shadow_bets(id) ON DELETE CASCADE,
    bookmaker      text        NOT NULL,
    market         text        NOT NULL,
    selection      text        NOT NULL,
    recorded_odds  numeric     NOT NULL,
    live_odds      numeric,
    fair_prob      numeric,
    status         text        NOT NULL CHECK (status IN (
                     'confirmed',      -- live price == recorded price
                     'moved_up',       -- live price higher than recorded
                     'moved_down',     -- live price lower (value may be gone)
                     'missing',        -- fixture found, this market/selection not offered now
                     'no_mapping',     -- no Epicbet event id in book_event_map
                     'fetch_failed',   -- the live fetch itself failed
                     'kicked_off')),
    checked_at     timestamptz NOT NULL DEFAULT now(),
    detail         text
);

CREATE INDEX IF NOT EXISTS price_verifications_checked_at ON price_verifications (checked_at);
