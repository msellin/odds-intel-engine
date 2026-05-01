-- ============================================================================
-- ENG-3: match_previews table
-- Stores Gemini-generated AI match previews (200-word + 50-word teaser)
-- Generated daily at 07:00 UTC for top 10 matches by signal count
-- ============================================================================

CREATE TABLE IF NOT EXISTS match_previews (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id      uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  match_date    date NOT NULL,
  preview_text  text NOT NULL,          -- full ~200-word preview (Pro/Elite)
  preview_short text NOT NULL,          -- ~50-word teaser (Free tier)
  signal_count  smallint DEFAULT 0,     -- signals available at generation time
  league_tier   smallint DEFAULT 1,     -- league tier for prioritisation
  generated_at  timestamptz NOT NULL DEFAULT now(),
  model_used    text NOT NULL DEFAULT 'gemini-2.5-flash',
  tokens_used   integer DEFAULT 0,

  UNIQUE(match_id, match_date)
);

CREATE INDEX IF NOT EXISTS idx_match_previews_match_date ON match_previews(match_date);
CREATE INDEX IF NOT EXISTS idx_match_previews_match_id   ON match_previews(match_id);

-- Public read — previews are free content
ALTER TABLE match_previews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Anyone can read previews"
  ON match_previews FOR SELECT
  USING (true);
