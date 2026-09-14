-- MFV-REBUILD-RESTORE-PRUNED-SIGNALS (2026-09-14)
--
-- Master task #2 correction. The rebuild did its job on ELO/form -- elo_diff AUC
-- fell 0.7536 -> 0.6134 against a de-vigged market at 0.7270, and 81.8 pct of
-- rows changed -- but it was DESTRUCTIVE beyond that remit, and the coverage
-- check caught it before any retrain consumed the result:
--
--   goals_for_avg_home      37,152 -> 14,970   (-59.7 pct)
--   goals_against_avg_home  37,152 -> 14,970   (-59.7 pct)
--   goals_for_avg_away      37,423 -> 15,113   (-59.6 pct)
--   goals_against_avg_away  37,423 -> 15,113   (-59.6 pct)
--
-- CAUSE. Those columns are not computed by the MFV builder; they are READ from
-- `match_signals`. SIGNALS-STORE-ON-CHANGE-2026-09-03 plus
-- `scripts/prune_match_signals.py` collapsed that table from 49.3M rows, so the
-- signals that existed when the original MFV rows were written are gone. A
-- rebuild reads the pruned present and writes NULL over data it cannot
-- reconstruct.
--
-- THE GENERAL TRAP, worth stating because it will recur: REBUILDING A DERIVED
-- TABLE FROM A PRUNED SOURCE IS LOSSY, and silently so -- the job reports
-- success, the row count is unchanged, and only a column-level coverage diff
-- against a backup reveals it. Any future MFV rebuild must diff coverage against
-- a snapshot before being trusted.
--
-- THE FIX. Restore every column the leak fix had no business touching, taking
-- the rebuilt value where it exists and the backup value where the rebuild
-- produced NULL. This keeps the genuine GAINS the rebuild also produced
-- (opening_implied_home 28,185 -> 30,133; odds_drift_home 27,454 -> 28,701) while
-- undoing the losses.
--
-- DELIBERATELY NOT RESTORED -- the seven leak-affected columns:
--   elo_home, elo_away, elo_diff, form_ppg_home, form_ppg_away,
--   form_momentum_home, form_momentum_away
-- Their new NULLs are CORRECT. A match with no strictly-prior rating has no
-- honest pre-match ELO, and inventing one is the bug we just removed. That
-- accounts for the ~8-10 pct drop on those columns and for form_momentum's
-- larger fall (it needs two form points, so it goes when one does).

UPDATE match_feature_vectors m
   SET "league_tier" = COALESCE(m."league_tier", b."league_tier"),
       "data_tier" = COALESCE(m."data_tier", b."data_tier"),
       "ensemble_prob_home" = COALESCE(m."ensemble_prob_home", b."ensemble_prob_home"),
       "ensemble_prob_draw" = COALESCE(m."ensemble_prob_draw", b."ensemble_prob_draw"),
       "ensemble_prob_away" = COALESCE(m."ensemble_prob_away", b."ensemble_prob_away"),
       "poisson_prob_home" = COALESCE(m."poisson_prob_home", b."poisson_prob_home"),
       "xgboost_prob_home" = COALESCE(m."xgboost_prob_home", b."xgboost_prob_home"),
       "af_pred_prob_home" = COALESCE(m."af_pred_prob_home", b."af_pred_prob_home"),
       "model_disagreement" = COALESCE(m."model_disagreement", b."model_disagreement"),
       "opening_implied_home" = COALESCE(m."opening_implied_home", b."opening_implied_home"),
       "opening_implied_draw" = COALESCE(m."opening_implied_draw", b."opening_implied_draw"),
       "opening_implied_away" = COALESCE(m."opening_implied_away", b."opening_implied_away"),
       "odds_drift_home" = COALESCE(m."odds_drift_home", b."odds_drift_home"),
       "steam_move" = COALESCE(m."steam_move", b."steam_move"),
       "news_impact_score" = COALESCE(m."news_impact_score", b."news_impact_score"),
       "injury_severity_home" = COALESCE(m."injury_severity_home", b."injury_severity_home"),
       "injury_severity_away" = COALESCE(m."injury_severity_away", b."injury_severity_away"),
       "lineup_confirmed" = COALESCE(m."lineup_confirmed", b."lineup_confirmed"),
       "match_outcome" = COALESCE(m."match_outcome", b."match_outcome"),
       "total_goals" = COALESCE(m."total_goals", b."total_goals"),
       "over_25" = COALESCE(m."over_25", b."over_25"),
       "pseudo_clv_home" = COALESCE(m."pseudo_clv_home", b."pseudo_clv_home"),
       "pseudo_clv_draw" = COALESCE(m."pseudo_clv_draw", b."pseudo_clv_draw"),
       "pseudo_clv_away" = COALESCE(m."pseudo_clv_away", b."pseudo_clv_away"),
       "built_at" = COALESCE(m."built_at", b."built_at"),
       "fixture_importance" = COALESCE(m."fixture_importance", b."fixture_importance"),
       "bookmaker_disagreement" = COALESCE(m."bookmaker_disagreement", b."bookmaker_disagreement"),
       "referee_cards_avg" = COALESCE(m."referee_cards_avg", b."referee_cards_avg"),
       "injury_count_home" = COALESCE(m."injury_count_home", b."injury_count_home"),
       "injury_count_away" = COALESCE(m."injury_count_away", b."injury_count_away"),
       "market_implied_home" = COALESCE(m."market_implied_home", b."market_implied_home"),
       "market_implied_draw" = COALESCE(m."market_implied_draw", b."market_implied_draw"),
       "market_implied_away" = COALESCE(m."market_implied_away", b."market_implied_away"),
       "goals_for_avg_home" = COALESCE(m."goals_for_avg_home", b."goals_for_avg_home"),
       "goals_for_avg_away" = COALESCE(m."goals_for_avg_away", b."goals_for_avg_away"),
       "goals_against_avg_home" = COALESCE(m."goals_against_avg_home", b."goals_against_avg_home"),
       "goals_against_avg_away" = COALESCE(m."goals_against_avg_away", b."goals_against_avg_away"),
       "h2h_win_pct" = COALESCE(m."h2h_win_pct", b."h2h_win_pct"),
       "league_position_home" = COALESCE(m."league_position_home", b."league_position_home"),
       "league_position_away" = COALESCE(m."league_position_away", b."league_position_away"),
       "overnight_line_move" = COALESCE(m."overnight_line_move", b."overnight_line_move"),
       "points_to_relegation_home" = COALESCE(m."points_to_relegation_home", b."points_to_relegation_home"),
       "points_to_relegation_away" = COALESCE(m."points_to_relegation_away", b."points_to_relegation_away"),
       "points_to_title_home" = COALESCE(m."points_to_title_home", b."points_to_title_home"),
       "points_to_title_away" = COALESCE(m."points_to_title_away", b."points_to_title_away"),
       "referee_home_win_pct" = COALESCE(m."referee_home_win_pct", b."referee_home_win_pct"),
       "referee_over25_pct" = COALESCE(m."referee_over25_pct", b."referee_over25_pct"),
       "rest_days_home" = COALESCE(m."rest_days_home", b."rest_days_home"),
       "rest_days_away" = COALESCE(m."rest_days_away", b."rest_days_away"),
       "pinnacle_implied_over25" = COALESCE(m."pinnacle_implied_over25", b."pinnacle_implied_over25"),
       "pinnacle_implied_under25" = COALESCE(m."pinnacle_implied_under25", b."pinnacle_implied_under25"),
       "ou25_bookmaker_disagreement" = COALESCE(m."ou25_bookmaker_disagreement", b."ou25_bookmaker_disagreement"),
       "market_implied_btts_yes" = COALESCE(m."market_implied_btts_yes", b."market_implied_btts_yes"),
       "weather_temp_c" = COALESCE(m."weather_temp_c", b."weather_temp_c"),
       "weather_wind_kmh" = COALESCE(m."weather_wind_kmh", b."weather_wind_kmh"),
       "weather_rain_mm" = COALESCE(m."weather_rain_mm", b."weather_rain_mm"),
       "weather_humidity" = COALESCE(m."weather_humidity", b."weather_humidity"),
       "odds_drift_home_at_t6h" = COALESCE(m."odds_drift_home_at_t6h", b."odds_drift_home_at_t6h"),
       "steam_move_at_t6h" = COALESCE(m."steam_move_at_t6h", b."steam_move_at_t6h"),
       "pinnacle_line_move_home_at_t6h" = COALESCE(m."pinnacle_line_move_home_at_t6h", b."pinnacle_line_move_home_at_t6h"),
       "pinnacle_line_move_draw_at_t6h" = COALESCE(m."pinnacle_line_move_draw_at_t6h", b."pinnacle_line_move_draw_at_t6h"),
       "pinnacle_line_move_away_at_t6h" = COALESCE(m."pinnacle_line_move_away_at_t6h", b."pinnacle_line_move_away_at_t6h"),
       "sharp_consensus_home_at_t6h" = COALESCE(m."sharp_consensus_home_at_t6h", b."sharp_consensus_home_at_t6h"),
       "sharp_consensus_draw_at_t6h" = COALESCE(m."sharp_consensus_draw_at_t6h", b."sharp_consensus_draw_at_t6h"),
       "sharp_consensus_away_at_t6h" = COALESCE(m."sharp_consensus_away_at_t6h", b."sharp_consensus_away_at_t6h"),
       "odds_volatility_home_at_t6h" = COALESCE(m."odds_volatility_home_at_t6h", b."odds_volatility_home_at_t6h"),
       "odds_volatility_draw_at_t6h" = COALESCE(m."odds_volatility_draw_at_t6h", b."odds_volatility_draw_at_t6h"),
       "odds_volatility_away_at_t6h" = COALESCE(m."odds_volatility_away_at_t6h", b."odds_volatility_away_at_t6h"),
       "pinnacle_ah_line_at_t6h" = COALESCE(m."pinnacle_ah_line_at_t6h", b."pinnacle_ah_line_at_t6h"),
       "pinnacle_ah_line_move" = COALESCE(m."pinnacle_ah_line_move", b."pinnacle_ah_line_move"),
       "team_avg_player_rating_home" = COALESCE(m."team_avg_player_rating_home", b."team_avg_player_rating_home"),
       "team_avg_player_rating_away" = COALESCE(m."team_avg_player_rating_away", b."team_avg_player_rating_away"),
       "injury_severity_score_home" = COALESCE(m."injury_severity_score_home", b."injury_severity_score_home"),
       "injury_severity_score_away" = COALESCE(m."injury_severity_score_away", b."injury_severity_score_away"),
       "league_clv_efficiency" = COALESCE(m."league_clv_efficiency", b."league_clv_efficiency"),
       "league_draw_rate_ytd" = COALESCE(m."league_draw_rate_ytd", b."league_draw_rate_ytd"),
       "season_progress" = COALESCE(m."season_progress", b."season_progress"),
       "line_velocity" = COALESCE(m."line_velocity", b."line_velocity"),
       "xg_overperf_home" = COALESCE(m."xg_overperf_home", b."xg_overperf_home"),
       "xg_overperf_away" = COALESCE(m."xg_overperf_away", b."xg_overperf_away"),
       "pinnacle_drift_home" = COALESCE(m."pinnacle_drift_home", b."pinnacle_drift_home"),
       "pinnacle_drift_draw" = COALESCE(m."pinnacle_drift_draw", b."pinnacle_drift_draw"),
       "pinnacle_drift_away" = COALESCE(m."pinnacle_drift_away", b."pinnacle_drift_away")
  FROM mfv_pre_elo_fix_backup b
 WHERE b.match_id = m.match_id;
