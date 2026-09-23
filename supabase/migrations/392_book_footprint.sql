-- 392 — BOOK FOOTPRINT: requests per book per hour, with outcomes ([[#110]], 2026-09-23)
--
-- #108's root cause: ~7,500 Coolbet requests in 8 h from one exit IP (peak 1,517 in
-- an hour), 6,033 of them search fallbacks — and NOTHING was counting. Imperva
-- flagged the IP; the book went dark for hours.
--
-- Every outbound request to a book is counted here (workers/utils/footprint.py),
-- across ALL processes (scheduler, near-kickoff timer, in-play collector), so a
-- per-book hourly budget can be enforced and the rates that precede a block —
-- bot-check pages, errors, slow responses — can warn before the hard block.

CREATE TABLE IF NOT EXISTS book_footprint (
    book        text        NOT NULL,
    hour        timestamptz NOT NULL,       -- date_trunc('hour', now())
    requests    integer     NOT NULL DEFAULT 0,
    challenges  integer     NOT NULL DEFAULT 0,  -- bot-check / interstitial / 403
    errors      integer     NOT NULL DEFAULT 0,  -- 5xx, timeouts, transport errors
    slow        integer     NOT NULL DEFAULT 0,  -- > 20 s
    refused     integer     NOT NULL DEFAULT 0,  -- blocked by OUR budget before sending
    PRIMARY KEY (book, hour)
);

ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS requests_1h    integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS budget_1h      integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS challenges_1h  integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS errors_1h      integer;
ALTER TABLE feed_book_stats ADD COLUMN IF NOT EXISTS requests_24h   integer;
