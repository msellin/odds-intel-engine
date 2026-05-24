-- B-ML3-V2-ACTIVE (2026-05-25)
--
-- Adds meta_clv_score columns to simulated_bets and shadow_bets so the
-- Stage-3 meta-model's per-bet score can be logged for retrospective
-- analysis. Score is the model's predicted P(this bet beats closing line);
-- production may also use it to filter pre-placement (gated by
-- META_B_ML3_ENABLED env var).

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS meta_clv_score FLOAT;
ALTER TABLE shadow_bets
    ADD COLUMN IF NOT EXISTS meta_clv_score FLOAT;

COMMENT ON COLUMN simulated_bets.meta_clv_score IS
    'B-ML3 v2.1 meta-model score: P(bet beats closing line). NULL if scoring unavailable. See workers/model/meta_b_ml3.py.';
COMMENT ON COLUMN shadow_bets.meta_clv_score IS
    'B-ML3 v2.1 meta-model score: P(bet beats closing line). NULL if scoring unavailable.';
