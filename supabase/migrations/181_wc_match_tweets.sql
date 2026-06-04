-- WC-F2 (2026-06-04): Twitter / X auto-post audit per WC2026 fixture.
--
-- Background: when a World Cup match resolves at settlement time (21:00
-- UTC), workers/jobs/wc_match_recap_tweet.py composes a one-liner recap
-- ("OddsIntel predicted Brazil 55%, Brazil won 2-1 ✓") and posts it from
-- our @ handle. We need a per-match lock so a re-run of settlement
-- doesn't double-tweet the same recap. Free-tier Twitter is capped at
-- ~1,500 tweets/month; 104 WC fixtures is well within budget but a buggy
-- settlement retry could easily burn through it.
--
-- One row per match_id (PRIMARY KEY) — presence of a row is the lock.
-- We also store the rendered tweet_text so post-hoc inspection ("did we
-- actually say Brazil 55% on June 11?") doesn't require pulling the
-- tweet from Twitter's API.
--
-- Engine-side audit only — no FE reads from this table, so no public
-- RLS policy (RLS enabled with zero policies = service role only,
-- matches the pattern for other engine-only tables).

CREATE TABLE IF NOT EXISTS wc_match_tweets (
    match_id    uuid        PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    tweet_id    text        NOT NULL,
    posted_at   timestamptz NOT NULL DEFAULT NOW(),
    tweet_text  text
);

ALTER TABLE wc_match_tweets ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE wc_match_tweets IS
    'WC-F2 (2026-06-04): per-WC-fixture lock + audit row for the recap '
    'tweet posted by workers/jobs/wc_match_recap_tweet.py from settlement. '
    'PRIMARY KEY on match_id prevents double-tweeting on settlement reruns.';
