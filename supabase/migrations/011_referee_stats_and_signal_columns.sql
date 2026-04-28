-- =============================================================================
-- OddsIntel — Migration 011: Referee stats + extended match_feature_vectors
-- =============================================================================
-- S4: referee_stats table — aggregated per-referee statistics
-- S5/BDM-1: add fixture_importance, bookmaker_disagreement, referee_cards_avg,
--            injury_count_home/away to match_feature_vectors
-- =============================================================================


-- ─── S4: referee_stats ───────────────────────────────────────────────────────
-- Built from historical matches + match_stats.
-- Refreshed nightly by backfill_referee_stats.py (or settlement pipeline).

CREATE TABLE IF NOT EXISTS referee_stats (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    referee_name    text        NOT NULL UNIQUE,
    matches_total   integer     NOT NULL DEFAULT 0,
    home_wins       integer     NOT NULL DEFAULT 0,
    draws_count     integer     NOT NULL DEFAULT 0,
    away_wins       integer     NOT NULL DEFAULT 0,
    home_win_pct    numeric(5,4),           -- home wins / total
    cards_per_game  numeric(5,2),           -- (yellow + red) / total
    over_25_count   integer     NOT NULL DEFAULT 0,
    over_25_pct     numeric(5,4),           -- over 2.5 total goals / total
    yellow_total    integer     NOT NULL DEFAULT 0,
    red_total       integer     NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_referee_stats_name
    ON referee_stats (referee_name);


-- ─── S5 / BDM-1 / context: extend match_feature_vectors ─────────────────────

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS fixture_importance      numeric(4,3),   -- 0.0–1.0
    ADD COLUMN IF NOT EXISTS bookmaker_disagreement  numeric(5,4),   -- BDM-1: max-min implied
    ADD COLUMN IF NOT EXISTS referee_cards_avg       numeric(5,2),
    ADD COLUMN IF NOT EXISTS injury_count_home       integer,
    ADD COLUMN IF NOT EXISTS injury_count_away       integer;
