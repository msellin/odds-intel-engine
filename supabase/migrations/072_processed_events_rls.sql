-- processed_events should not be readable via the anon key.
-- It contains Stripe event IDs — not critically sensitive, but no reason
-- to expose it publicly. Service role (webhook handler) bypasses RLS automatically.

ALTER TABLE processed_events ENABLE ROW LEVEL SECURITY;

-- No SELECT policy = anon/authenticated keys get zero rows.
-- Service role always bypasses RLS and can read/write freely.
