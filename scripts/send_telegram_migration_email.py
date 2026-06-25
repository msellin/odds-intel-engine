"""
One-time migration email to existing signed-up users — "we moved to
Telegram, here's where the picks are now."

Background: DIGEST-DISABLED 2026-06-25 paused six per-user mass-mailer
crons (daily/weekly/watchlist emails) because the product collapsed to
free + Telegram and Resend was hitting daily quota. Existing signed-up
users now get no per-user emails until they ask for one.

This script sends ONE final transactional email per user explaining
the pivot. After this run, they should join @oddsintelpicks if they
want to keep getting picks.

Safety:
  - DRY_RUN=1 by default. Prints the recipient list + first email
    preview. Set DRY_RUN=0 to actually send.
  - Excludes the operator email (sellinmargus@gmail.com).
  - Excludes anonymous-auth shells (email IS NULL).
  - Excludes anything @example.com / @test.com.
  - Idempotent — won't re-send if `migration_email_sent_at` is already
    set on the profile. (Migration 260 below adds the column.)
  - Rate-limited to 5 emails/sec so we don't trip Resend bursting limits.

Run:
    python3 scripts/send_telegram_migration_email.py            # dry run
    DRY_RUN=0 python3 scripts/send_telegram_migration_email.py  # actually send
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query, execute_write  # noqa: E402


OPERATOR_EMAIL = "sellinmargus@gmail.com"
EXCLUDED_DOMAINS = ("example.com", "test.com", "localhost")
SITE_URL = os.getenv("SITE_URL", "https://oddsintel.app")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_ADDR = os.getenv(
    "DIGEST_FROM_EMAIL",
    "OddsIntel <digest@oddsintel.app>",
)
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"
SUBJECT = "OddsIntel: we moved to Telegram"


HTML = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;">
    <tr><td align="center" style="padding:32px 16px 24px;">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="background:#0d1117;border-radius:10px 10px 0 0;padding:24px 32px;text-align:center;">
            <a href="{SITE_URL}" style="text-decoration:none;display:inline-block;">
              <span style="font-size:28px;font-weight:800;color:#ffffff;letter-spacing:0.04em;">ODDS</span><span style="font-size:28px;font-weight:800;color:#22c55e;letter-spacing:0.04em;">INTEL</span>
            </a>
          </td>
        </tr>
        <tr>
          <td style="background:#ffffff;padding:28px 32px 32px;border-left:1px solid #e2e8f0;border-right:1px solid #e2e8f0;">
            <h2 style="margin:0 0 12px;font-size:20px;color:#0f172a;">We simplified the product</h2>
            <p style="margin:0 0 16px;font-size:14px;color:#475569;line-height:1.6;">
              Hi — quick heads-up. We&apos;ve narrowed OddsIntel from a multi-tier site
              with daily emails to a focused free product: <strong>verified track
              record + live picks on Telegram</strong>. No paywall, no daily
              digests, no inbox clutter.
            </p>
            <p style="margin:0 0 20px;font-size:14px;color:#475569;line-height:1.6;">
              The model is the same one that&apos;s been running paper bets since May.
              You can see every pick, every result, every closing-line value in our
              public ledger — anchored to Bitcoin via OpenTimestamps so the numbers
              can&apos;t be retroactively edited.
            </p>

            <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px;">
              <tr>
                <td style="padding:12px 0;border-bottom:1px solid #f1f5f9;">
                  <span style="font-size:14px;color:#0f172a;font-weight:600;">📨 Live picks on Telegram</span><br>
                  <span style="font-size:13px;color:#64748b;">Every calibrated pre-match pick lands in <a href="https://t.me/oddsintelpicks" style="color:#22c55e;text-decoration:none;">@oddsintelpicks</a> pre-kickoff. Free, one click to subscribe.</span>
                </td>
              </tr>
              <tr>
                <td style="padding:12px 0;border-bottom:1px solid #f1f5f9;">
                  <span style="font-size:14px;color:#0f172a;font-weight:600;">📊 Verified track record</span><br>
                  <span style="font-size:13px;color:#64748b;">+11.91% ROI on 989 settled bets vs WinnerOdds +6.78%, SignalOdds -0.44%, DeepBetting -9.15%. <a href="{SITE_URL}/performance" style="color:#22c55e;text-decoration:none;">{SITE_URL}/performance</a></span>
                </td>
              </tr>
              <tr>
                <td style="padding:12px 0;">
                  <span style="font-size:14px;color:#0f172a;font-weight:600;">🔓 Open-source ledger</span><br>
                  <span style="font-size:13px;color:#64748b;">Every bet, every result on <a href="https://github.com/msellin/odds-intel-engine/tree/main/ledger" style="color:#22c55e;text-decoration:none;">GitHub</a>, Bitcoin-anchored. Anyone can audit.</span>
                </td>
              </tr>
            </table>

            <div style="margin-top:24px;text-align:center;">
              <a href="https://t.me/oddsintelpicks" style="display:inline-block;padding:12px 28px;background:#22c55e;color:#ffffff;font-weight:700;font-size:14px;text-decoration:none;border-radius:6px;">Join the Telegram channel →</a>
            </div>

            <p style="margin:24px 0 0;font-size:12px;color:#94a3b8;line-height:1.6;">
              The daily/weekly emails have been turned off — this is the only message
              we&apos;re sending. If you don&apos;t join Telegram, that&apos;s fine; you can still
              check <a href="{SITE_URL}/picks" style="color:#22c55e;text-decoration:none;">{SITE_URL}/picks</a>
              whenever you want.
            </p>
          </td>
        </tr>
        <tr>
          <td style="background:#f8fafc;border-radius:0 0 10px 10px;border:1px solid #e2e8f0;border-top:none;padding:18px 32px;text-align:center;">
            <p style="margin:0;font-size:11px;color:#94a3b8;line-height:1.5;">Not financial or gambling advice. Please gamble responsibly. 18+ only.</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def ensure_column() -> None:
    """Add migration_email_sent_at column if not present — idempotent."""
    try:
        execute_write(
            """ALTER TABLE profiles
               ADD COLUMN IF NOT EXISTS migration_email_sent_at TIMESTAMPTZ"""
        )
    except Exception as e:
        print(f"  WARN: could not ensure column (continuing): {e}")


def load_recipients() -> list[dict]:
    """Email + id for every signed-up user we should reach, minus exclusions."""
    rows = execute_query(
        """
        SELECT id, email
        FROM profiles
        WHERE email IS NOT NULL
          AND email != ''
          AND email != %s
          AND migration_email_sent_at IS NULL
        ORDER BY created_at ASC
        """,
        (OPERATOR_EMAIL,),
    )
    out = []
    for r in rows:
        e = (r["email"] or "").lower().strip()
        if not e or "@" not in e:
            continue
        domain = e.split("@", 1)[1]
        if any(domain.endswith(d) for d in EXCLUDED_DOMAINS):
            continue
        out.append({"id": r["id"], "email": e})
    return out


def send_one(to: str) -> bool:
    if not RESEND_API_KEY:
        print(f"  [no RESEND_API_KEY — would send to {to}]")
        return False
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"from": FROM_ADDR, "to": [to], "subject": SUBJECT, "html": HTML},
        timeout=20,
    )
    if resp.status_code in (200, 202):
        return True
    print(f"  FAIL {to}: {resp.status_code} {resp.text[:200]}")
    return False


def mark_sent(profile_id: str) -> None:
    execute_write(
        "UPDATE profiles SET migration_email_sent_at = %s WHERE id = %s",
        (datetime.now(timezone.utc), profile_id),
    )


def main() -> int:
    print(f"Telegram migration email — DRY_RUN={DRY_RUN}")
    ensure_column()
    recips = load_recipients()
    print(f"Recipients eligible: {len(recips)}")

    if not recips:
        print("Nothing to send.")
        return 0

    print("\nFirst 5 recipients:")
    for r in recips[:5]:
        print(f"  · {r['email']}")
    if len(recips) > 5:
        print(f"  ...and {len(recips) - 5} more")

    if DRY_RUN:
        print("\nDRY_RUN=1 — not sending. Set DRY_RUN=0 to actually send.")
        print("\n--- Email preview (first 1000 chars of HTML) ---")
        print(HTML[:1000])
        return 0

    sent = 0
    failed = 0
    for r in recips:
        ok = send_one(r["email"])
        if ok:
            mark_sent(r["id"])
            sent += 1
            print(f"  OK  {r['email']}")
        else:
            failed += 1
        time.sleep(0.2)  # 5/sec — well under Resend's rate caps

    print(f"\nDone. sent={sent}  failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
