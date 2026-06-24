"""
Betaminic vs OddsIntel — apples-to-apples ROI audit (deferred).

Reads dev/active/betaminic_raw.json. If that file is the auth-required stub
(see scripts/scrape_betaminic.py docstring), this audit writes a similarly
auth-gated ledger entry and exits. When the operator later runs the real
scrape with BETAMINIC_COOKIE set, this script will compute ROI in the same
way as audit_vs_signalodds.py / audit_vs_deepbetting.py.

The stub still emits comparison_betaminic.json so the landing page's
comparison block can show "Betaminic — pending" with a date stamp instead
of silently omitting the row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

INPUT_PATH = ROOT / "dev" / "active" / "betaminic_raw.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_betaminic.json"

STAKE = 10.0
MIN_SAMPLE = 50
DEFAULT_START = "2026-05-04"


def our_stats(start: str, end: str) -> dict:
    rows = execute_query(
        """
        SELECT
          sb.stake::float        AS stake,
          sb.pnl::float          AS pnl,
          sb.result::text        AS result,
          sb.market,
          sb.odds_at_pick::float AS odds,
          b.maturity_label
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.created_at >= %s::date
          AND sb.created_at <  %s::date
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2','over_under_25','o/u')
          AND b.maturity_label IN ('calibrated','beta','active')
        """,
        (start, end),
    )
    if not rows:
        return {"n": 0}
    stake_total = sum(float(r["stake"] or 0) for r in rows)
    pnl_total = sum(float(r["pnl"] or 0) for r in rows)
    won = sum(1 for r in rows if r["result"] == "won")
    odds_vals = [float(r["odds"]) for r in rows if r.get("odds")]
    return {
        "n": len(rows),
        "stake_total": round(stake_total, 2),
        "pnl_total": round(pnl_total, 2),
        "roi_pct": round(100.0 * pnl_total / stake_total, 2) if stake_total else 0.0,
        "hit_rate_pct": round(100.0 * won / len(rows), 2),
        "avg_odds": round(sum(odds_vals) / len(odds_vals), 3) if odds_vals else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_betaminic.py first.",
              file=sys.stderr)
        return 2

    raw = json.loads(INPUT_PATH.read_text())
    status = raw.get("status", "ok")
    reason = raw.get("reason")
    strategies = raw.get("strategies") or []

    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    if status == "auth_required" or not strategies:
        print(f"\nBetaminic data is auth-gated (reason: {reason!r}); "
              "writing auth_required ledger entry.")
        out = {
            "source": "Betaminic",
            "source_url": "https://www.betaminic.com/betamin-builder/public-strategies/",
            "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window": {"start": start, "end": end},
            "status": "auth_required",
            "min_sample_each_side": MIN_SAMPLE,
            "scope_notes": (
                "Betaminic gates its strategy ROI behind a free-signup auth "
                "wall. Auto-signup is out of policy (no paywall bypass / no "
                "fabricated numbers). Comparison-block row should render as "
                "\"Betaminic — pending audit (signup required)\" until the "
                "operator runs scripts/scrape_betaminic.py with a logged-in "
                "BETAMINIC_COOKIE."
            ),
            "reproducible_via": "scripts/scrape_betaminic.py + scripts/audit_vs_betaminic.py",
            "their_stats": {"n": 0, "note": reason},
            "our_stats_same_window": ours,
        }
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                          sort_keys=True).encode()
        print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
        print(f"Wrote: {OUT_PATH}")
        return 0

    # Future: real audit. Strategies expected to look like
    #   {"strategy_id": int, "name": str, "settled_bets": [{...}], ...}
    # The settled_bets array follows the same shape as our other audits — we
    # filter to 1X2 + OU 2.5, then compute ROI at STAKE=10.0.
    print("Real-data audit path not yet implemented — see "
          "scripts/scrape_betaminic.py for next steps.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
