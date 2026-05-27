ALTER TABLE bots ADD COLUMN IF NOT EXISTS maturity_label TEXT NOT NULL DEFAULT 'active';

UPDATE bots SET maturity_label = 'calibrated' WHERE name IN (
  'bot_aggressive', 'bot_v10_all', 'bot_ou25_global', 'bot_ah_away_dog', 'inplay_e'
);

UPDATE bots SET maturity_label = 'experimental' WHERE name IN (
  'bot_acca_value', 'bot_acca_proven', 'bot_acca_coolbet',
  'bot_combo_system', 'bot_combo_proven_system', 'bot_acca_leg_shadow'
);

UPDATE bots SET maturity_label = 'beta' WHERE name IN (
  'bot_high_alignment', 'inplay_b', 'inplay_m', 'inplay_d', 'inplay_f',
  'inplay_a', 'inplay_n', 'inplay_g', 'inplay_i', 'inplay_h',
  'bot_dnb_away_value', 'bot_dnb_home_value',
  'inplay_btts_dryspell_v1', 'inplay_btts_press_v1',
  'inplay_q', 'inplay_j', 'inplay_c_home', 'inplay_a2'
);
