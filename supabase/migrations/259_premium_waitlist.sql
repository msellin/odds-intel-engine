-- PREMIUM-WAITLIST 2026-06-24
--
-- Captures email addresses from the landing-page "Notify me when premium
-- launches" CTA. No paid product exists today (Stripe checkout deleted in
-- the product collapse) — this table is a pure demand-signal capture so
-- the operator can gauge interest and reach out when a premium tier
-- becomes ready.

CREATE TABLE IF NOT EXISTS premium_waitlist (
    id            BIGSERIAL    PRIMARY KEY,
    email         TEXT         NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ip_hash       TEXT,                  -- SHA-256 of IP — light spam-control signal
    user_agent    TEXT,                  -- best-effort UA snippet
    notified_at   TIMESTAMPTZ,           -- set once we actually email them
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_premium_waitlist_created_at
    ON premium_waitlist (created_at DESC);

-- RLS: the table is public-insert (the landing form posts via service-role
-- key in a server route — anon clients NEVER hit this table directly).
-- Reads are admin-only. No anon SELECT, no anon UPDATE, no anon DELETE.
ALTER TABLE premium_waitlist ENABLE ROW LEVEL SECURITY;

-- Drop any pre-existing policies of the same name so the migration is
-- idempotent against re-runs.
DROP POLICY IF EXISTS "premium_waitlist no anon"        ON premium_waitlist;

CREATE POLICY "premium_waitlist no anon"
    ON premium_waitlist
    FOR ALL
    TO anon, authenticated
    USING (false)
    WITH CHECK (false);

COMMENT ON TABLE premium_waitlist IS
    'Demand-signal capture for a future premium tier. Populated by /api/v1/waitlist endpoint (server-side, service-role). Anon clients cannot read or write.';
