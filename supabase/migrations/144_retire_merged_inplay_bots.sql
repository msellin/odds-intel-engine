-- INPLAY-MERGED-BOT-RETIRE (2026-05-28):
-- inplay_a2 was merged into inplay_a on 2026-05-08 (same xG-divergence thesis,
-- merged into a single strategy covering 0-0/1-0/0-1). The bot record exists
-- for historical settlement but is explicitly not dispatched in inplay_bot.py.
-- inplay_c_home was merged into inplay_c on 2026-05-08 (home-fav gets wider
-- window inside unified strategy_c). Same — not dispatched, not retired in DB.
-- Both now formally retired so the ops_snapshot + /performance "active bot count"
-- stops counting them.

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    retired_reason = 'Merged into inplay_a on 2026-05-08. Strategy expanded to cover same thesis across 0-0, 1-0, 0-1 score states. No new bets — historical rows remain for settlement.'
WHERE name = 'inplay_a2';

UPDATE bots
SET is_active     = false,
    retired_at    = NOW(),
    retired_reason = 'Merged into inplay_c on 2026-05-08. Home-favourite branch folded into unified strategy_c with wider minute window (25-70) and 5pp lower possession threshold. No new bets.'
WHERE name = 'inplay_c_home';
