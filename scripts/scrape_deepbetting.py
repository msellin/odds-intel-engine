"""
DeepBetting public stats scraper.

DeepBetting exposes its full settled history via an undocumented but public
endpoint used by its own dashboard JS:

    GET https://deepbetting.io/backend/api/predictions-api.php?type=stats

Response is JSON:
    {
      "success": true,
      "type": "stats",
      "data": {
        "football": [ { sport, division_label, date_norm (YYYYMMDD),
                        forecast_type, forecast_status, odds, game_status,
                        confidence } ... ],
        "nba":      [ ... ]
      },
      "count": ...
    }

Status values: "Won" | "Lost" | "Push" | null
Markets:       "Moneyline" | "Over-Under" | "BTTS" | "Draw No Bet"

This is the auditable proxy for "DeepBetting" in audit_vs_deepbetting.py.
The Bet-Analytix integration (scripts/scrape_bet_analytix.py) requires a
paid subscription and cannot be used here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "deepbetting_stats.json"
STATS_URL = "https://deepbetting.io/backend/api/predictions-api.php?type=stats"

HEADERS = {
    "User-Agent": "OddsIntel-Audit/1.0 (+https://oddsintel.com)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://deepbetting.io/dashboard/",
}


def fetch() -> dict:
    r = requests.get(STATS_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    print(f"Fetching {STATS_URL} ...")
    body = fetch()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False))
    print(f"Wrote: {out}")

    if not body.get("success"):
        print("API success flag is false — body:")
        print(json.dumps(body, indent=2)[:800])
        return 2

    data = body.get("data", {})
    for k, lst in data.items():
        if isinstance(lst, list):
            from collections import Counter
            statuses = Counter(p.get("forecast_status") for p in lst)
            print(f"  {k}: {len(lst)} picks, statuses={dict(statuses)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
