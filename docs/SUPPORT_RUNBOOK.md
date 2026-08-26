# Support Runbook — OddsIntel

> Operator reference. Write before the first edge case fires, not after.
> Last updated: 2026-06-07

---

## 1 — Stripe charged but no tier

**Symptom:** User emails saying they paid but the site still shows free features.

**Why it happens:** The `checkout.session.completed` webhook failed to deliver (Stripe retries
for 72h — check Stripe → Developers → Webhooks → your endpoint → Recent deliveries).

**Recovery:**
1. Confirm the charge exists in Stripe → Customers → [customer] → Payments.
2. Check `processed_events` in Supabase for the session's event ID. If absent, the webhook never fired.
3. Fix the tier manually:
   ```sql
   UPDATE profiles
   SET tier = 'pro',            -- or 'elite'
       stripe_customer_id = 'cus_XXXX'
   WHERE email = 'user@example.com';
   ```
4. Replay the webhook event from Stripe dashboard (delivery details → Resend) so the
   `processed_events` row gets written and future replays are idempotent.
5. Reply to user: tier is now active, apologise for the delay.

---

## 2 — Tier granted but no charge

**Symptom:** User has Pro/Elite in the DB but Stripe shows no active subscription.

**Why it happens:** Unlikely given idempotency via `processed_events`, but possible if the
DB was edited manually or a test event was accidentally processed in production.

**Recovery:**
1. Check Stripe → Customers → Subscriptions for the relevant customer.
2. If **no subscription exists** → the tier grant was wrong. Downgrade:
   ```sql
   UPDATE profiles SET tier = 'free' WHERE email = 'user@example.com';
   ```
3. Investigate before acting — do not downgrade until you've confirmed no payment was made.

---

## 3 — Subscription cancelled but tier still active

**Symptom:** User cancelled; Stripe shows deleted subscription; `profiles.tier` is still pro/elite.

**Why it happens:** `customer.subscription.deleted` webhook missed, or `stripe_customer_id`
mismatch on the profile row.

**Recovery:**
1. Verify `profiles.stripe_customer_id` matches the Stripe customer ID.
2. Replay `customer.subscription.deleted` from Stripe dashboard.
3. Manual fallback if replay fails:
   ```sql
   UPDATE profiles SET tier = 'free' WHERE email = 'user@example.com';
   ```

---

## 4 — Refund procedure

OddsIntel sells software access, not bet outcomes. Policy:

| Scenario | Action |
|----------|--------|
| < 24h since charge, no meaningful usage | Full refund — Stripe dashboard → Customers → Payments → Refund |
| > 24h or meaningful usage | No obligation; goodwill refund at discretion for genuine technical failures |
| Site was down for their entire billing period | Full refund |

After any refund:
```sql
UPDATE profiles SET tier = 'free' WHERE email = 'user@example.com';
```
Also cancel the Stripe subscription (Stripe → Subscriptions → Cancel) to prevent future charges.

---

## 5 — User never received upgrade email

**Symptom:** User says they paid but got no confirmation email.

**Why it happens:** The `sendUpgradeEmail` call in `webhook/route.ts` is fire-and-forget
(`.catch(() => {})`). If Resend is down or `RESEND_API_KEY` is unset, it silently drops.

**Recovery:** Check Resend dashboard → Emails for a delivery attempt. If missing, reply
manually confirming tier is active. Their tier is already correct in the DB.

---

## 6 — "Can't see Pro features" despite correct tier

**Symptom:** `profiles.tier = 'pro'` in DB but frontend still shows free-tier UI.

**Why it happens:** Supabase session token is stale; the tier is read server-side on page
render from the session.

**Recovery:** Ask user to log out and log back in. If that fails:
- Check `profiles.tier` is lowercase (`'pro'`, not `'Pro'`).
- Check the user is logged in with the same email address they subscribed with.

---

## Key locations

| Resource | Where |
|----------|-------|
| Stripe dashboard | https://dashboard.stripe.com |
| Supabase profiles | Supabase → Table Editor → profiles |
| Processed events | Supabase → Table Editor → processed_events |
| Webhook event log | Stripe → Developers → Webhooks → endpoint → Recent deliveries |
| Resend email log | https://resend.com/emails |
| Engine logs | `ssh root@204.168.199.8 'journalctl -u oddsintel-scheduler -n 200 --no-pager'` |
| Vercel logs | Vercel → Project → Functions tab |

---

## Webhook events handled (webhook/route.ts)

| Event | Effect |
|-------|--------|
| `checkout.session.completed` | Sets `profiles.tier` + `stripe_customer_id`; sends upgrade email |
| `customer.subscription.updated` | Updates tier when plan changes or subscription goes inactive |
| `customer.subscription.deleted` | Downgrades tier to `'free'` |

All events are deduplicated via `processed_events.event_id` (UNIQUE constraint). A `23505`
duplicate key error means the event was already processed — return 200, no action needed.
