-- HLTV news index. Light-weight daily/4h scrape of /news to capture what
-- topics HLTV is publishing. No body text or LLM analysis — just store
-- the article ID, slug (which encodes the headline), and timestamps. The
-- slug like "allu-returns-to-ence-as-head-coach" is enough for downstream
-- keyword matching and team-association lookups.
--
-- v1 goal: keep a chronological feed we can correlate against match
-- outcomes later. v2 (optional) can add Gemini-extracted entities and
-- topic tags.

CREATE TABLE IF NOT EXISTS cs2_hltv_news (
    id                  BIGSERIAL PRIMARY KEY,
    news_id             INTEGER NOT NULL UNIQUE,
    slug                TEXT NOT NULL,
    url                 TEXT NOT NULL,
    title_from_slug     TEXT,         -- humanised: "allu returns to ence as head coach"
    section             TEXT,         -- "news" | "feature" — from HLTV's URL bucket
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cs2_hltv_news_first_seen
    ON cs2_hltv_news (first_seen_at DESC);
