"""
OddsIntel — Stripe Event Reconciliation (STRIPE-RECONCILE)

Compares yesterday's Stripe events against our processed_events table.
Stripe silently drops ~1-2% of webhooks — without this check, a missed
checkout means a user paid but never got their tier upgrade.

Checks:
  1. Events in Stripe but NOT in processed_events → missed webhook (alert)
  2. Events in processed_events but NOT in Stripe → should never happen (log)

Runs daily at 09:00 UTC (after Stripe's 24h retry window has closed).
Sends alert email via Resend to ADMIN_ALERT_EMAIL if any drift is found.

Usage:
    venv/bin/python scripts/stripe_reconcile.py              # yesterday
    venv/bin/python scripts/stripe_reconcile.py --date 2026-05-07  # specific date
    venv/bin/python scripts/stripe_reconcile.py --dry-run    # print only, no email
"""

import os
import sys
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")

WATCHED_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


def fetch_stripe_events(target_date: date) -> list[dict]:
    """List all watched Stripe events for a given UTC date."""
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    day_start = int(datetime(target_date.year, target_date.month, target_date.day,
                              tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400

    events = []
    params = {
        "created": {"gte": day_start, "lt": day_end},
        "limit": 100,
    }

    while True:
        page = stripe.Event.list(**params)
        for ev in page.data:
            if ev.type in WATCHED_EVENT_TYPES:
                events.append({"id": ev.id, "type": ev.type, "created": ev.created})
        if not page.has_more:
            break
        params["starting_after"] = page.data[-1].id

    return events


def fetch_processed_events(target_date: date) -> set[str]:
    """Return set of event_ids processed on the given date."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT event_id FROM processed_events WHERE processed_at::date = %s",
        (target_date.isoformat(),)
    )
    return {r["event_id"] for r in rows}


def send_alert(subject: str, html: str, dry_run: bool) -> None:
    if dry_run:
        print(f"\n[DRY RUN] Would send: {subject}")
        return
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        print(f"[ALERT — no email configured] {subject}")
        return
    import httpx
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": html},
        timeout=15,
    )
    print(f"Alert sent ({resp.status_code}): {subject}")


def run(target_date: date = None, dry_run: bool = False) -> bool:
    """
    Run reconciliation for target_date (default: yesterday).
    Returns True if clean, False if drift found.
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    print(f"\nStripe reconciliation for {target_date.isoformat()}")
    print("─" * 50)

    if not STRIPE_SECRET_KEY:
        print("ERROR: STRIPE_SECRET_KEY not set — cannot query Stripe API")
        return False

    stripe_events = fetch_stripe_events(target_date)
    processed_ids = fetch_processed_events(target_date)

    stripe_ids = {ev["id"] for ev in stripe_events}

    missed = stripe_ids - processed_ids       # In Stripe, not in our DB
    phantom = processed_ids - stripe_ids      # In our DB, not in Stripe (shouldn't happen)

    print(f"Stripe events ({', '.join(WATCHED_EVENT_TYPES)}): {len(stripe_events)}")
    print(f"Processed in DB: {len(processed_ids)}")
    print(f"Missed (not processed): {len(missed)}")
    print(f"Phantom (in DB only):   {len(phantom)}")

    if not missed and not phantom:
        print("✅ Clean — no drift")
        return True

    # Build alert
    lines = [f"<h2>Stripe reconciliation drift: {target_date.isoformat()}</h2>"]

    if missed:
        lines.append(f"<h3>⚠️ {len(missed)} Stripe event(s) NOT processed</h3>")
        lines.append("<p>These events were delivered by Stripe but never hit processed_events. "
                     "Check Railway logs for webhook failures around these times.</p><ul>")
        for ev in stripe_events:
            if ev["id"] in missed:
                ts = datetime.fromtimestamp(ev["created"], tz=timezone.utc).strftime("%H:%M UTC")
                lines.append(f"<li><code>{ev['id']}</code> — {ev['type']} @ {ts}</li>")
        lines.append("</ul>")
        lines.append("<p><strong>Action:</strong> Manually verify each user's tier in Supabase. "
                     "Re-trigger the webhook via Stripe Dashboard → Webhooks → [endpoint] → Resend.</p>")

    if phantom:
        lines.append(f"<h3>ℹ️ {len(phantom)} event(s) in DB but not in Stripe</h3>")
        lines.append("<ul>" + "".join(f"<li><code>{eid}</code></li>" for eid in phantom) + "</ul>")
        lines.append("<p>This is unexpected. Could be a date boundary edge case or test events.</p>")

    subject = f"[OddsIntel] Stripe drift {target_date.isoformat()} — {len(missed)} missed"
    send_alert(subject, "".join(lines), dry_run)

    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date to reconcile (YYYY-MM-DD), default=yesterday")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None
    ok = run(target, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
