-- 473 — [[#182]] OWN BOT: the paper bot designed for OWN (workers/jobs/own_bots.py).
-- Owner 2026-09-26: "maybe we should build our own bots for OWN picks — designed very precisely to our OWN
-- needs", and "better a single bot, or at least one per market, not a bot for every book". Rule from the
-- pre-registered #182 filter study (dev/active/own-bot-filter-study-findings.md): 1X2, the best of
-- Coolbet / Unibet-Site / Epicbet / Tonybet at EV >= 3% over the v2 anchor (Pinnacle+exchange, else a
-- consensus without that book), sharp-engine gates, < 3 h to kick-off, >= 3 other books confirm the fair
-- price; the book is recorded on every pick.
-- EXPERIMENTAL (not on /picks, /performance or Telegram) — it exists to build a record at the price we
-- could actually take. ON CONFLICT DO UPDATE: the scheduler's ensure_bots() may insert a bare row first.
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks, show_on_performance)
VALUES ('bot_own_1x2_v1', 'OWN · 1X2, best Estonian book, last 3 h',
        'OWN paper bot (#182): best Estonian-book 1X2 price EV >= 3% over the v2 anchor, < 3 h to kick-off, >= 3 books confirm; book recorded per pick.',
        1000.00, 1000.00, true, 'experimental', false, false)
ON CONFLICT (name) DO UPDATE
   SET display_name = EXCLUDED.display_name, strategy = EXCLUDED.strategy, is_active = true,
       retired_at = NULL, maturity_label = 'experimental', show_on_picks = false, show_on_performance = false;
