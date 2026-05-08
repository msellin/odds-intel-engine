-- MONEY-STRIPE-IDEMPOTENT: Dedup table for Stripe webhook events.
-- Stripe retries webhooks on any non-2xx response or timeout (1-2% of events).
-- Without this, a retry can double-grant tier or double-record a subscription change.
-- Keyed by event.id from the JSON payload (not the Stripe-Signature header, which
-- is per-attempt and won't deduplicate retries).

CREATE TABLE IF NOT EXISTS processed_events (
    event_id      TEXT PRIMARY KEY,           -- Stripe event.id (e.g. evt_1Abc...)
    event_type    TEXT        NOT NULL,        -- e.g. checkout.session.completed
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keep 90 days of history for reconciliation; older rows auto-pruned by a future job.
-- No RLS needed — only ever written by the service-role key in the webhook handler.
