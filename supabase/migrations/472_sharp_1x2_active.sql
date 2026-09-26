-- 472 — [[#183]] bot_sharp_1x2_v1 TESTING → ACTIVE (owner decision 2026-09-26).
--
-- WHY. The promotion rule (docs/SYSTEM_MAP.md §Lifecycle, owner 2026-09-26): TESTING → ACTIVE after
-- 50 settled picks with sharp-anchor CLV > 0. At the decision (v4 rule, 1x2, settled won/lost):
--   n 68 · ROI +9.7% (CI ±33pp, uninformative) · sharp-anchor CLV +2.19% [+1.0, +3.3] (n 67)
--   vs the junk-anchor control −2.20% (n 369): +4.4pp [+3.2, +5.6], one-sided p ≈ 0.
--   Independent close (≥5 books, Pinnacle and own book excluded): +1.1% [−0.05, +2.3] (n 56),
--   +3.2pp over the control's −2.1% — borderline on its own, stated so nobody over-reads it.
-- WHAT CHANGES. Picks now count in the headline totals (hero ROI / P&L, track-record API, P&L
-- curve — all read bot_ledger incl. source 'forward_test' since #183), and EVERY pick goes to the
-- public Telegram channel (ACTIVE always; TESTING only at EV >= 5%, #174 — most of this bot's picks
-- sit at 3–5% and were not being posted). /picks already showed all of them (arm 'live' is
-- always shown, migration 469). NOT staked: placeable stays false. The pre-registered forward
-- test's rule and constants are unchanged — this is a distribution status, not a rule change.
-- bot_sharp_ou_v1 stays TESTING (n 28 settled, below the 50 needed).

BEGIN;
SET LOCAL lock_timeout = '3s';

UPDATE bots SET maturity_label = 'active'
 WHERE name = 'bot_sharp_1x2_v1' AND maturity_label = 'testing' AND retired_at IS NULL;

INSERT INTO control_changes (actor, source, control, bot_name, old_value, new_value, reason, outcome)
SELECT 'migration:472', 'migration', 'maturity_label', 'bot_sharp_1x2_v1',
       to_jsonb('testing'::text), to_jsonb('active'::text),
       '#183 owner 2026-09-26: 68 settled, sharp-anchor CLV +2.19% [+1.0,+3.3], +4.4pp vs junk control',
       'applied'
 WHERE EXISTS (SELECT 1 FROM bots WHERE name = 'bot_sharp_1x2_v1' AND maturity_label = 'active');

COMMIT;
