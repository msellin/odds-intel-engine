"""
Test script for INFRA-8 Resend webhook endpoint.

Sends a properly Svix-signed email.opened event to the live webhook endpoint,
then checks the DB to confirm last_email_opened_at was updated.

Usage:
    python scripts/test_resend_webhook.py [email]

If no email given, uses the first profile in the DB.
"""

import sys
import time
import hmac
import hashlib
import base64
import uuid
import json
import httpx
import psycopg2
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

WEBHOOK_URL = "https://www.oddsintel.app/api/resend-webhook"
WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    raise RuntimeError("RESEND_WEBHOOK_SECRET not set in .env")
DATABASE_URL = os.getenv("DATABASE_URL")


def sign_svix(secret_b64: str, msg_id: str, timestamp: int, body: str) -> str:
    """Compute the Svix v1 signature."""
    # Strip whsec_ prefix and decode base64 to get raw secret bytes
    raw_secret = base64.b64decode(secret_b64.removeprefix("whsec_"))
    to_sign = f"{msg_id}.{timestamp}.{body}".encode()
    sig = hmac.new(raw_secret, to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(sig).decode()


def get_test_email() -> str:
    """Pull first profile email from DB."""
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT email FROM profiles ORDER BY created_at LIMIT 1")
        row = cur.fetchone()
    conn.close()
    if not row:
        raise RuntimeError("No profiles in DB")
    return row[0]


def check_db(email: str) -> dict:
    """Read last_email_opened_at for the given email."""
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT email, last_email_opened_at FROM profiles WHERE email = %s",
            (email,)
        )
        row = cur.fetchone()
    conn.close()
    return {"email": row[0], "last_email_opened_at": row[1]} if row else {}


def send_test_event(email: str, event_type: str = "email.opened"):
    msg_id = f"msg_test_{uuid.uuid4().hex[:12]}"
    timestamp = int(time.time())
    payload = {
        "type": event_type,
        "data": {
            "email_id": f"test_{uuid.uuid4().hex[:8]}",
            "to": [email],
            "from": "digest@oddsintel.app",
            "subject": "Test webhook event",
        }
    }
    body = json.dumps(payload)
    signature = sign_svix(WEBHOOK_SECRET, msg_id, timestamp, body)

    resp = httpx.post(
        WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "svix-id": msg_id,
            "svix-timestamp": str(timestamp),
            "svix-signature": signature,
        },
        timeout=10,
    )
    return resp


def main():
    email = sys.argv[1] if len(sys.argv) > 1 else get_test_email()
    print(f"\nTarget email: {email}")

    # Before state
    before = check_db(email)
    print(f"Before — last_email_opened_at: {before.get('last_email_opened_at')}")

    # Send test webhook
    print(f"\nSending email.opened webhook to {WEBHOOK_URL} ...")
    resp = send_test_event(email)
    print(f"Response: {resp.status_code} {resp.text}")

    if resp.status_code != 200:
        print("❌ Webhook returned non-200 — check Vercel function logs")
        return

    # After state
    time.sleep(1)
    after = check_db(email)
    print(f"After  — last_email_opened_at: {after.get('last_email_opened_at')}")

    if after.get("last_email_opened_at") != before.get("last_email_opened_at"):
        print("\n✅ PASS — last_email_opened_at updated correctly")
    else:
        print("\n❌ FAIL — column not updated. Check if migration 041 has been applied.")


if __name__ == "__main__":
    main()
