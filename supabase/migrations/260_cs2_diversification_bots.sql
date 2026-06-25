-- CS2 paper-bet diversification (2026-06-25).
--
-- Adds three new bot variants on top of the existing value/v7/v8/hltv_v1
-- quartet so CS2 paper-bet volume can climb toward soccer's tempo:
--   - bot_cs2_aggressive_v1 — lower edge floor + tighter anomaly guard
--   - bot_cs2_dog_v1        — underdog only (odds ≥ 2.20), match_winner only
--   - bot_cs2_fav_v1        — favourite only (odds ≤ 1.70), match_winner only
--
-- All three share the same scan pool as the existing CS2 bots; gates differ.
-- Strategy text mirrors the BOTS_CONFIG entries in scripts/esports/cs2_bot.py.
--
-- Starting bankroll 1000.00 EUR (same convention as bot_cs2_v8 from mig 231).

INSERT INTO bots (name, strategy, description, starting_bankroll, current_bankroll, is_active)
VALUES
  ('bot_cs2_aggressive_v1',
   'CS2 aggressive — extra-edge 3% floor, divergence 20pp',
   'Lower edge floor (3% vs 5%) and tighter anomaly guard (20pp) than bot_cs2_value_v1. Fires on elo+pq_v1, v8, v7 sources. Catches edges the conservative bot lets through; excludes the weakest hltv_v1 model where low-edge picks become noise.',
   1000.00, 1000.00, TRUE),
  ('bot_cs2_dog_v1',
   'CS2 underdog — match_winner @ odds ≥ 2.20',
   'Only fires on underdog match_winner picks with odds ≥ 2.20 and ≥4% extra edge. High variance, longer payouts. Excludes atleast1map (map-handicap dog odds are too noisy in CS2). Sources elo+pq_v1 + v8 only — the higher-AUC models keep dog signal meaningful at low hit-rates.',
   1000.00, 1000.00, TRUE),
  ('bot_cs2_fav_v1',
   'CS2 favourite — match_winner @ odds ≤ 1.70',
   'Only fires on favourite match_winner picks with odds ≤ 1.70 and ≥4% extra edge. Low variance, higher hit-rate. Sources elo+pq_v1 + v8 only. Bookie-margin still leaves edge at short prices when the model has strong probability conviction.',
   1000.00, 1000.00, TRUE)
ON CONFLICT (name) DO NOTHING;
