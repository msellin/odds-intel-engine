-- 356_bots_show_on_picks.sql
-- PICKS-PAGE-CURATION (2026-09-15)
--
-- The owner's model, and it is the right split:
--
--   /performance  = where we MEASURE. Every bot appears, whatever its record.
--                   Hiding a bot there would be hiding the evidence.
--   /picks        = what we OFFER. A curated subset, chosen deliberately.
--
-- Until now the two were entangled by accident rather than by design. /picks
-- read `picks_forward_test` (the pre-registered sharp rule) while the model
-- bots published straight to Telegram through a different code path -- so on
-- 2026-09-15 a `bot_v10_all` pick (Ludogorets II v Fratria, home @4.00) reached
-- the public channel while being absent from /picks. The channel and the page
-- disagreed, and nothing in the system had an opinion about which was right.
--
-- This column makes the choice explicit and auditable: a bot's picks reach
-- customers only if someone set this flag.
--
-- DEFAULT FALSE on purpose. A new bot must be opted IN. The alternative --
-- everything published unless someone remembers to switch it off -- is how the
-- Ludogorets pick went out in the first place.
--
-- ⚠️ This does NOT gate /performance. Every bot is measured there regardless,
-- and any future code that filters the leaderboard on this column is a bug.

ALTER TABLE bots
    ADD COLUMN IF NOT EXISTS show_on_picks BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN bots.show_on_picks IS
    'Does this bot''s picks appear on the customer /picks page and in the '
    'public Telegram channel? Curation only -- /performance measures every bot '
    'regardless and must never filter on this.';

-- bot_sharp_forward_test_v1 is the pre-registered rule already on /picks; it
-- reads through to picks_forward_test rather than simulated_bets, but the flag
-- is set so the page's two sources are described by one switch.
UPDATE bots SET show_on_picks = TRUE WHERE name = 'bot_sharp_forward_test_v1';

-- bot_v10_all is deliberately left FALSE pending the owner's decision. The
-- honest position on it as of today: ROI +4.04% with a 95% CI of [-9.5, +18.2]
-- on 486 settled bets -- i.e. UNPROVEN, not bad -- and its strongest months
-- were concentrated in books independently measured as quoting above their own
-- site (verified-book subset -1.41% on n=144 against +22.96% on the
-- measured-unfaithful subset). Flip it to TRUE to publish its picks; nothing
-- else needs changing.
