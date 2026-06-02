-- BRACKET-SHARE (2026-06-02): viral sharing for WC bracket picks.
--
-- Adds an unguessable share_token on wc_bracket_meta so users can publish a
-- read-only link of their bracket. Token is null until the user first taps
-- "Share" (saves a round-trip for non-sharers and keeps the table clean).
--
-- Token derivation: UUID v4 via gen_random_uuid(). Not derived from user_id
-- so it can be regenerated later (privacy / revoke) without leaking history.
-- The share page on the web app loads picks via the service-role key,
-- bypassing RLS — the token itself is the authorisation bearer.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

ALTER TABLE wc_bracket_meta
    ADD COLUMN IF NOT EXISTS share_token uuid;

-- Unique partial index — uniqueness only enforced when set, multiple NULLs OK.
-- (UUID v4 collisions are astronomically unlikely, but the constraint protects
-- against any future manual seeding.)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wc_bracket_meta_share_token
    ON wc_bracket_meta (share_token)
    WHERE share_token IS NOT NULL;
