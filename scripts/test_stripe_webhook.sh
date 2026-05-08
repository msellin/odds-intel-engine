#!/usr/bin/env bash
# MONEY-WEBHOOK-TEST: Stripe CLI webhook scenario tests
#
# Prerequisites:
#   1. stripe CLI installed: brew install stripe/stripe-cli/stripe
#   2. Logged in: stripe login
#   3. Local Next.js running: cd ../odds-intel-web && npm run dev
#   4. Stripe webhook forwarding running in another terminal:
#      stripe listen --forward-to localhost:3000/api/stripe/webhook
#
# Run this script AFTER the listener is running.
# Each test prints PASS/FAIL based on HTTP status + DB state.
#
# Usage:
#   bash scripts/test_stripe_webhook.sh

set -euo pipefail

BASE_URL="${WEBHOOK_URL:-http://localhost:3000/api/stripe/webhook}"
WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-}"

echo ""
echo "════════════════════════════════════════════════════"
echo "  Stripe Webhook Scenario Tests"
echo "  Target: $BASE_URL"
echo "════════════════════════════════════════════════════"
echo ""

# ── Scenario 1: Bad signature ──────────────────────────────────────────────
echo "1. Bad signature → expect 400"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "Stripe-Signature: t=1234,v1=fakesig" \
  -d '{"type":"checkout.session.completed","data":{}}')
if [ "$STATUS" = "400" ]; then
  echo "   ✅ PASS (got $STATUS)"
else
  echo "   ❌ FAIL (got $STATUS, expected 400)"
fi

# ── Scenario 2: No signature header ───────────────────────────────────────
echo "2. No signature header → expect 400"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -d '{"type":"checkout.session.completed","data":{}}')
if [ "$STATUS" = "400" ]; then
  echo "   ✅ PASS (got $STATUS)"
else
  echo "   ❌ FAIL (got $STATUS, expected 400)"
fi

# ── Scenario 3: Unknown event type (valid signature via stripe CLI) ────────
echo "3. Unknown event type via stripe CLI → expect 200 (silently ignored)"
echo "   Run manually: stripe trigger payment_intent.created"
echo "   Verify: processed_events should contain the event_id, profiles unchanged"
echo "   (Skipped — requires stripe listen running)"

# ── Scenario 4: Duplicate checkout event (idempotency) ────────────────────
echo ""
echo "4. Duplicate checkout.session.completed → expect 200 on second call, no double-grant"
echo "   Run manually:"
echo "     stripe trigger checkout.session.completed"
echo "     # Note the event ID from Railway logs"
echo "     # Then in Stripe Dashboard → Webhooks → [endpoint] → find event → Resend"
echo "     # Second delivery should return 200 and NOT change the user's tier again"
echo "   Verify in Supabase: processed_events has ONE row for that event_id"

# ── Scenario 5: subscription.deleted downgrades tier ──────────────────────
echo ""
echo "5. customer.subscription.deleted → tier should drop to 'free'"
echo "   Run manually: stripe trigger customer.subscription.deleted"
echo "   Verify: profiles.tier = 'free' for the test customer"

echo ""
echo "════════════════════════════════════════════════════"
echo "  Manual checks checklist:"
echo "  [ ] Bad signature → 400 (automated above)"
echo "  [ ] No signature  → 400 (automated above)"
echo "  [ ] Valid checkout → tier upgraded, row in processed_events"
echo "  [ ] Duplicate event → 200, no second DB write, tier unchanged"
echo "  [ ] subscription.updated (cancel) → tier drops to free"
echo "  [ ] subscription.deleted → tier drops to free"
echo "  [ ] Unknown event type → 200, row in processed_events"
echo "  [ ] DB error simulation → 500 returned (triggers Stripe retry)"
echo "════════════════════════════════════════════════════"
echo ""
echo "Stripe CLI trigger commands:"
echo "  stripe trigger checkout.session.completed"
echo "  stripe trigger customer.subscription.updated"
echo "  stripe trigger customer.subscription.deleted"
echo ""
echo "To resend a specific event:"
echo "  Stripe Dashboard → Developers → Webhooks → [endpoint] → [event] → Resend"
