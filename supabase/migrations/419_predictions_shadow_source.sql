-- 419 — #147 PREDICTIONS-1X2-VERSION-MIXING (2026-09-24).
--
-- Since SHADOW-AUTOSELECT (2026-08-26) the pipeline wrote the candidate model's rows with the
-- SAME source as production ('ensemble' / 'xgboost'), distinguished only by model_version and a
-- 'shadow=<version>' marker in reasoning. Most readers never filter model_version, so calibration
-- fits (fit_blend_weights shrinkage alpha + blend weights), the ML ETL, the no-pin / sweep shadow
-- passes, in-play strategies, triggers and match previews have been mixing the two. The pipeline
-- now writes 'ensemble_shadow' / 'xgboost_shadow'; this moves the existing rows the same way.
-- Unique key is (match_id, market, source, model_version) and no *_shadow rows exist yet, so no
-- conflicts. compare_models.py and model_version_clv_scoreboard.py read both sources.

UPDATE predictions SET source = 'ensemble_shadow'
 WHERE source = 'ensemble' AND reasoning LIKE '%shadow=%';

UPDATE predictions SET source = 'xgboost_shadow'
 WHERE source = 'xgboost' AND reasoning LIKE '%shadow=%';
