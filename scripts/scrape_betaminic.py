"""
Betaminic — public-strategies scrape attempt (auth-walled, deferred).

Investigated 2026-06-24:
  * /betamin-builder/public-strategies/ — marketing landing page only. The
    actual strategy list, ROI numbers, and per-bet history live behind a
    free-signup auth wall ("Access Betamin Builder"). Their AJAX endpoints
    under /betamin-builder/api/ are guarded by a session cookie minted only
    after email-confirmed registration.
  * Per-strategy detail at /betamin-builder/strategy/<id> 302-redirects to
    the login page when accessed without a session.
  * The home page (https://www.betaminic.com/) reveals a free trial flow
    ("Register"), but it requires email confirmation and a manual click-
    through, which isn't automatable inside our policy ("DON'T scrape
    paywalled content; document the constraint and skip").

Rather than fabricate numbers, this script writes an `auth_required` stub to
dev/active/betaminic_raw.json so the audit script downstream emits a status
"auth_required" comparison_betaminic.json. A future MANUAL run (operator
signs up, runs the scraper from their Mac with the session cookie passed in
via BETAMINIC_COOKIE env var) can then populate real data.

To activate the real scrape later:
  1. Sign up at https://www.betaminic.com/ (free trial).
  2. Open DevTools → Application → Cookies → copy the session cookie value.
  3. Export BETAMINIC_COOKIE="<value>".
  4. Re-run this script with --execute. The auth-required branch below will
     swap to the real fetch (TODO once endpoints are mapped).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "betaminic_raw.json"

PUBLIC_LANDING = "https://www.betaminic.com/betamin-builder/public-strategies/"
API_BASE = "https://www.betaminic.com/betamin-builder/api/"


def emit_stub(reason: str) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stub = {
        "status": "auth_required",
        "reason": reason,
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "public_landing_url": PUBLIC_LANDING,
        "api_base": API_BASE,
        "notes": (
            "Betaminic gates its per-strategy ROI data behind a free-signup "
            "email-confirmed account. Auto-signup is out of scope per the "
            "scraper policy (no fabricated numbers, no paywall bypass). "
            "Operator must sign up manually and re-run this scraper with "
            "BETAMINIC_COOKIE in env."
        ),
        "strategies": [],
    }
    OUT_PATH.write_text(json.dumps(stub, indent=2, ensure_ascii=False))
    print(f"Wrote auth-required stub: {OUT_PATH}")


def probe_public() -> str:
    """Verify the landing page is still gated. Returns a short summary string."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(PUBLIC_LANDING, headers=headers, timeout=30)
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    # The page is rendered, but the strategy data lives in /api/* which 401s.
    auth_required = (
        "Login" in r.text and "Register" in r.text and "Access Betamin" in r.text
    )
    return ("auth-gate confirmed (Login + Register + Access Betamin "
            f"strings present, body length {len(r.text)})"
            if auth_required else
            f"unexpected: gate strings missing (body length {len(r.text)})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="Attempt real fetch using $BETAMINIC_COOKIE")
    args = ap.parse_args()

    if args.execute and os.getenv("BETAMINIC_COOKIE"):
        # Future: real fetch using the session cookie. Endpoints still need
        # mapping from a logged-in DevTools session.
        emit_stub("execute_path_not_yet_implemented — "
                  "needs endpoint mapping from a logged-in DevTools session")
        return 1

    summary = probe_public()
    print(f"Public landing probe: {summary}")
    emit_stub(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
