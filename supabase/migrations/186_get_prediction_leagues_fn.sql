-- GROWTH-SEO-EXPAND-LEAGUES (2026-06-05) — Phase 2 of GROWTH-SEO-CONTENT-ENGINE.
--
-- Returns the set of leagues that qualify for /predictions/[league] SEO pages:
--   * at least one upcoming scheduled/live fixture in the next 21 days, AND
--   * at least N ensemble 1x2_home predictions in the 60-day-back + 21-day-fwd
--     window (so the page isn't a thin-content shell).
--
-- The frontend uses this via supabase.rpc("get_prediction_leagues", { min_preds: 3 })
-- to drive the dynamic league list for sitemap + generateStaticParams.

CREATE OR REPLACE FUNCTION get_prediction_leagues(min_preds INTEGER DEFAULT 3)
RETURNS TABLE (
    league_id      UUID,
    league_name    TEXT,
    country        TEXT,
    tier           INTEGER,
    n_upcoming     BIGINT,
    n_pred         BIGINT
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        l.id           AS league_id,
        l.name         AS league_name,
        l.country      AS country,
        l.tier         AS tier,
        COUNT(DISTINCT m.id) FILTER (
            WHERE m.date > NOW()
              AND m.date < NOW() + INTERVAL '21 days'
              AND m.status IN ('scheduled', 'live')
        ) AS n_upcoming,
        COUNT(DISTINCT p.match_id) FILTER (
            WHERE p.source = 'ensemble'
              AND p.market = '1x2_home'
              AND m.date BETWEEN NOW() - INTERVAL '60 days' AND NOW() + INTERVAL '21 days'
        ) AS n_pred
    FROM leagues l
    JOIN matches m ON m.league_id = l.id
    LEFT JOIN predictions p ON p.match_id = m.id
    WHERE l.is_active = TRUE
    GROUP BY l.id, l.name, l.country, l.tier
    HAVING
        COUNT(DISTINCT p.match_id) FILTER (
            WHERE p.source = 'ensemble'
              AND p.market = '1x2_home'
              AND m.date BETWEEN NOW() - INTERVAL '60 days' AND NOW() + INTERVAL '21 days'
        ) >= min_preds
    AND COUNT(DISTINCT m.id) FILTER (
            WHERE m.date > NOW()
              AND m.date < NOW() + INTERVAL '21 days'
              AND m.status IN ('scheduled', 'live')
        ) >= 1
    ORDER BY n_pred DESC, n_upcoming DESC;
$$;

-- Public read — sitemap + generateStaticParams call this with the anon key.
GRANT EXECUTE ON FUNCTION get_prediction_leagues(INTEGER) TO anon, authenticated, service_role;

COMMENT ON FUNCTION get_prediction_leagues IS
'GROWTH-SEO-EXPAND-LEAGUES: returns leagues with sufficient ensemble prediction coverage to power /predictions/[league] SEO pages. Used by getAllPredictionLeagues() in src/lib/engine-data.ts.';
