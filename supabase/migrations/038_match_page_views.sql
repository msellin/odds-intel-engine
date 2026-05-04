-- ENG-1: Rolling page view counter per match (30-minute window)
-- One row per (session_id, match_id) — viewed_at refreshed on each revisit
-- No PII: session_id is a random UUID stored in localStorage, no FK to auth.users

CREATE TABLE IF NOT EXISTS match_page_views (
  session_id  text        NOT NULL,
  match_id    uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  viewed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, match_id)
);

-- Fast 30-minute window count queries
CREATE INDEX IF NOT EXISTS match_page_views_match_viewed
  ON match_page_views (match_id, viewed_at DESC);

-- RLS: allow anyone (anon + authenticated) — no PII in this table
ALTER TABLE match_page_views ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anyone can read view counts"
  ON match_page_views FOR SELECT
  USING (true);

CREATE POLICY "anyone can track a view"
  ON match_page_views FOR INSERT
  WITH CHECK (true);

CREATE POLICY "anyone can refresh their view timestamp"
  ON match_page_views FOR UPDATE
  USING (true)
  WITH CHECK (true);
