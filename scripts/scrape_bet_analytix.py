"""
Bet-Analytix bankroll scraper.

Bet-Analytix is the third-party auditor that DeepBetting embeds for proof-of-
results. The bankroll pages at https://app.bet-analytix.com/bankroll/{ID} are
thin Nuxt SPAs — the data is fetched client-side from an internal API. We
reverse-engineered the call by reading the public JS bundle:

    Base URL : https://skxetzhvxe.execute-api.eu-west-3.amazonaws.com/prod-v1
    Headers  : {"app": "mobileBax", "sid": "152120"}
    Route    : GET /integration/bankroll/{ID}
               GET /integration/bankroll/{ID}?date_start=YYYY-MM-DD&date_end=YYYY-MM-DD

This script:
  1. Calls the integration endpoint for a given bankroll ID
  2. Saves the raw JSON to dev/active/bet_analytix_{ID}.json
  3. Reports the response shape

Important caveat (discovered during development):
    Both DeepBetting bankrolls (780105 current, 619433 historical) return
        {"errors": [{"msg": "premiumRequired"}]}
    when called without authentication. The integration view requires a paid
    Bet-Analytix subscription, so this scraper cannot publish a settled-bet
    ledger from those bankrolls without authenticated access.

This script is kept for the day Bet-Analytix exposes a public proof feed (or
for use by holders of a Bet-Analytix subscription who can pass a JWT). For
DeepBetting itself, see scripts/scrape_deepbetting.py — DeepBetting's own
public /backend/api/predictions-api.php endpoint exposes the full settled
history without any auth barrier.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dev" / "active"

API_BASE = "https://skxetzhvxe.execute-api.eu-west-3.amazonaws.com/prod-v1"
DEFAULT_HEADERS = {
    "User-Agent": "OddsIntel-Audit/1.0 (+https://oddsintel.com)",
    "Accept": "application/json",
    "app": "mobileBax",
    "sid": "152120",
    "Origin": "https://integration.bet-analytix.com",
    "Referer": "https://integration.bet-analytix.com/",
}


def fetch_bankroll(
    bankroll_id: int | str,
    *,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    bearer: Optional[str] = None,
    timeout: int = 30,
) -> tuple[int, dict]:
    """Returns (status_code, body_json_or_error_dict)."""
    headers = dict(DEFAULT_HEADERS)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    url = f"{API_BASE}/integration/bankroll/{bankroll_id}"
    params: dict = {}
    if date_start:
        params["date_start"] = date_start
    if date_end:
        params["date_end"] = date_end
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:1000]}
    return r.status_code, body


def write_payload(bankroll_id: str, body: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"bet_analytix_{bankroll_id}.json"
    p.write_text(json.dumps(body, indent=2, ensure_ascii=False))
    return p


def summarize_payload(body: dict) -> None:
    print("Top-level keys:", list(body.keys())[:20])
    if "bets" in body and isinstance(body["bets"], list):
        bets = body["bets"]
        print(f"Bets: {len(bets)}")
        if bets:
            print("First bet keys:", list(bets[0].keys())[:20])
    if "stats" in body:
        print("Stats:", list(body["stats"].keys()) if isinstance(body["stats"], dict)
              else type(body["stats"]).__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bankroll_id", help="Bet-Analytix bankroll ID (e.g. 780105 or 619433)")
    ap.add_argument("--date-start", help="YYYY-MM-DD")
    ap.add_argument("--date-end", help="YYYY-MM-DD")
    ap.add_argument("--bearer", help="Optional Bearer token for authenticated access "
                                       "(if you have a Bet-Analytix subscription).")
    args = ap.parse_args()

    print(f"Fetching Bet-Analytix bankroll {args.bankroll_id} "
          f"({args.date_start or 'all'} → {args.date_end or 'all'}) ...")
    code, body = fetch_bankroll(
        args.bankroll_id,
        date_start=args.date_start,
        date_end=args.date_end,
        bearer=args.bearer,
    )
    print(f"HTTP {code}")
    out = write_payload(args.bankroll_id, body)
    print(f"Wrote: {out}")
    if code == 200:
        summarize_payload(body)
        return 0
    # graceful failure: surface the API error so caller can decide
    err = body.get("errors") if isinstance(body, dict) else None
    if err:
        print(f"API error: {err}")
        if any(e.get("msg") == "premiumRequired" for e in err if isinstance(e, dict)):
            print("\nThe bankroll requires a paid Bet-Analytix subscription. "
                  "Pass --bearer <jwt> if you have one. Otherwise use "
                  "scripts/scrape_deepbetting.py to audit DeepBetting via its own "
                  "public stats endpoint.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
