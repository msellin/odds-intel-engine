-- COOLBET-SIGNALER-DEEPLINK (2026-06-12): cache Coolbet's per-match
-- numeric event id so Telegram bet-signals can include a direct
-- "open the match page" link (https://www.coolbet.com/et/sport/match/{id})
-- instead of the generic sports landing page. The signaler resolves it
-- lazily — first signal for a match does ONE anon Coolbet search, stores
-- the id here, every subsequent signal reads from DB. Search uses public
-- Imperva-gated endpoint, no JWT.

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS coolbet_match_id BIGINT;

COMMENT ON COLUMN matches.coolbet_match_id IS
    'Coolbet''s internal event id, used to build deep links like '
    'https://www.coolbet.com/et/sport/match/{id}. Populated lazily by '
    'workers/automation/coolbet_signaler.py on first signal for the match; '
    'reused indefinitely. NULL = not yet resolved OR not on Coolbet.';
