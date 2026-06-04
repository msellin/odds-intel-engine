-- WC-E3-E4 (2026-06-04): Gemini-generated analytical articles for WC2026.
--
-- One row per article (PK: slug). Each row stores the rendered markdown body
-- plus the structured model_inputs used to generate it, for transparency +
-- audit. The article generator (`scripts/generate_wc_insights.py`) runs
-- daily at 08:00 UTC during the WC window, after the Monte Carlo snapshot.
-- It is idempotent — skips generation if `refresh_after > NOW()` for a slug,
-- so re-runs the same day don't burn Gemini quota.
--
-- Slugs used by the generator (kept in sync with the FE generateStaticParams):
--   - group-of-death       : toughest WC2026 group (variance in p_advance)
--   - cinderella-story     : biggest underdogs by p_advance vs ELO rank
--   - squad-value-vs-model : most expensive squad doesn't always win
--   - champions-favourites : top-5 winner contenders + why
--
-- model_inputs JSONB captures the deterministic per-team numbers that were
-- fed to Gemini, so the page can render an "as of <snapshot_at>" footer and
-- so we can spot-check the article against the source data later.

CREATE TABLE IF NOT EXISTS wc_articles (
    slug           text        PRIMARY KEY,
    title          text        NOT NULL,
    description    text        NOT NULL,
    body_md        text        NOT NULL,
    generated_at   timestamptz NOT NULL DEFAULT NOW(),
    refresh_after  timestamptz NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    model_inputs   jsonb
);

ALTER TABLE wc_articles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public read wc_articles" ON wc_articles;
CREATE POLICY "Public read wc_articles" ON wc_articles FOR SELECT USING (true);

COMMENT ON TABLE wc_articles IS
    'WC-E3-E4 (2026-06-04): Gemini-generated analytical articles for WC2026. '
    'Written by scripts/generate_wc_insights.py daily at 08:00 UTC during the '
    'WC window. Idempotent via refresh_after (default 24h). FE renders at '
    '/world-cup/insights and /world-cup/insights/[slug].';
