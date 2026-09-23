-- 384 — CANDIDATE FUNNEL: keep the rejected population ([[#082]], 2026-09-23)
--
-- The pipeline counts why every candidate was rejected (`_funnel[bot][step]`),
-- prints it, and throws it away; the picks publisher drops everything below its
-- floor / above its ceiling / de-duplicated without a trace. So no question of
-- the form "would a lower floor / another grade / another de-vig have helped?"
-- could ever be answered — #073 and #077 both hit that wall ("those picks do not
-- exist"). This table is that population. Step 1 of the Block D re-order
-- (dev/active/block-d-order-2026-09-23.md).
--
-- ONE ROW PER (day, source, bot, match, market, selection), updated in place with
-- the LATEST decision, so a candidate re-evaluated every hour costs one row a day.
-- Stores PRICE + PROBABILITY, never the edge: an edge is derived on read
-- (EDGE-IS-DERIVED-NOT-STORED — the numeric(5,2) rounding incident), and the two
-- sources' edges are not even the same quantity (model: p − 1/odds; sharp:
-- p·odds − 1). Only candidates within 5pp of their floor, or rejected by a later
-- gate, are written — far-below-floor rows answer nothing.
-- Retention 90 days, enforced by the writer.

CREATE TABLE IF NOT EXISTS candidate_funnel (
    day            date        NOT NULL,
    source         text        NOT NULL,   -- 'pipeline' | 'publisher_live' | 'publisher_consensus'
    bot            text        NOT NULL,   -- bot name, or the arm's bot for the publisher
    match_id       uuid        NOT NULL,
    market         text        NOT NULL,
    selection      text        NOT NULL,
    bookmaker      text,
    odds           numeric     NOT NULL,
    fair_prob      numeric,               -- calibrated model prob / Shin p_sharp / consensus p
    fair_source    text,                  -- 'model_cal' | 'pinnacle_shin' | 'consensus:N'
    raw_prob       numeric,               -- pipeline: the uncalibrated model probability
    threshold      numeric,               -- the floor applied, in that source's own edge units
    step           text        NOT NULL,   -- the rejecting step, or 'accepted' / 'selected'
    quote_age_min  numeric,               -- decision-quote age, where the source knows it
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now(),
    n_seen         integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (day, source, bot, match_id, market, selection)
);

CREATE INDEX IF NOT EXISTS candidate_funnel_step ON candidate_funnel (source, bot, step, day);
