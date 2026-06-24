"""
Tipstrr vs OddsIntel — apples-to-apples ROI audit.

Reads dev/active/tipstrr_raw.json (produced by scripts/scrape_tipstrr.py) and
computes a windowed ROI for each football-only tipster, then aggregates across
all of them as the headline number.

Important scoping caveat:
  Tipstrr's PUBLIC stats are at the (tipster × month) grain, not per-bet.
  Per-bet selection / market detail is paywalled. This means we cannot
  restrict to 1X2 + OU 2.5 like we do for SignalOdds / DeepBetting / Forebet —
  the aggregate covers ALL football bet types (1X2, OU, AH, BTTS, etc.) the
  tipster published that month. The headline ROI is therefore "all football
  bet types" for both sides — our_stats_same_window pulls our 1x2 + OU bets,
  but theirs may include AH/BTTS. That's still a fair comparison FOR THE
  CUSTOMER who can't buy individual picks anyway, but the scope_notes field
  must spell it out.

Aggregation: stake-weighted ROI across all selected tipsters' month-buckets
that overlap the window. We treat the monthly buckets as if all that month's
tips landed at the midpoint of the month — for the May 4 → June 25 window
this means we keep the May + June buckets in full (they're fully inside the
window).

Stake methodology: convert Tipstrr's reported "1 unit" stake to 10 EUR/bet
(stake × 10) for parity with the other audits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

INPUT_PATH = ROOT / "dev" / "active" / "tipstrr_raw.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_tipstrr.json"

STAKE = 10.0
MIN_SAMPLE = 50
DEFAULT_START = "2026-05-04"   # calibrated tier launch


def load_tipstrr() -> list[dict]:
    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_tipstrr.py first.",
              file=sys.stderr)
        sys.exit(2)
    rows = json.loads(INPUT_PATH.read_text())
    print(f"Loaded {len(rows)} tipsters from {INPUT_PATH.name}")
    return rows


def month_in_window(month_iso: str, start: str, end: str) -> bool:
    """Tipstrr month buckets are dated to the 1st of the month. We keep a
    bucket if its month overlaps the window — i.e. the 1st of the NEXT month
    is strictly after `start`, AND the 1st of THIS month is strictly before
    `end`. This means partial months at either edge of the window are still
    counted (we tolerate the slight over-counting at the edges because the
    alternative — dropping May + June entirely for a May 4 → June 25 audit —
    leaves us with no data at all).

    We still record which months are partial so the consumer can see the
    risk in the breakdown."""
    try:
        d = datetime.fromisoformat(month_iso.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1)
    else:
        next_month = d.replace(month=d.month + 1)
    return d.isoformat() < end and next_month.isoformat() > start


def aggregate_tipster(tipster: dict, start: str, end: str) -> dict:
    """Sum a single tipster's in-window monthly buckets into a stats dict."""
    months = tipster.get("monthly") or []
    in_window = [m for m in months if month_in_window(m.get("date", ""), start, end)]
    if not in_window:
        return {
            "slug": tipster["slug"],
            "n": 0,
            "months_in_window": 0,
        }
    n = sum(int(m.get("tips") or 0) for m in in_window)
    won = sum(int(m.get("win") or 0) for m in in_window)
    # Tipstrr profit is in "units" (their stake unit = 1.0). Multiply by STAKE
    # to convert to EUR for parity.
    profit_units = sum(float(m.get("levelStakeProfit") or 0) for m in in_window)
    staked_units = sum(int(m.get("staked") or 0) for m in in_window)
    if not n or not staked_units:
        return {"slug": tipster["slug"], "n": 0}
    pnl = profit_units * STAKE
    stake_total = staked_units * STAKE
    # Weighted avg odds across buckets
    odds_weighted = 0.0
    weights = 0
    for m in in_window:
        ao = m.get("averageOdds")
        t = int(m.get("tips") or 0)
        if ao and t:
            odds_weighted += float(ao) * t
            weights += t
    avg_odds = round(odds_weighted / weights, 3) if weights else None
    return {
        "slug": tipster["slug"],
        "name": tipster.get("name"),
        "n": n,
        "months_in_window": len(in_window),
        "stake_total": round(stake_total, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100.0 * pnl / stake_total, 2),
        "hit_rate_pct": round(100.0 * won / n, 2),
        "avg_odds": avg_odds,
    }


def aggregate_all(tipsters: list[dict], start: str, end: str) -> tuple[dict, list[dict]]:
    per_tipster = []
    eligible = [t for t in tipsters if t.get("football_only")]
    print(f"Football-only tipsters: {len(eligible)} of {len(tipsters)}")
    for t in eligible:
        agg = aggregate_tipster(t, start, end)
        per_tipster.append(agg)
        if agg["n"]:
            print(f"  {agg['slug']}: n={agg['n']} ROI={agg.get('roi_pct')}% "
                  f"hit={agg.get('hit_rate_pct')}% avg_odds={agg.get('avg_odds')}")
        else:
            print(f"  {agg['slug']}: 0 settled tips in window — skipping")

    # Pool all eligible tipsters with > 0 tips in window into the headline stats
    pooled = [t for t in per_tipster if t.get("n", 0) > 0]
    if not pooled:
        return {"n": 0}, per_tipster
    n_total = sum(t["n"] for t in pooled)
    stake_total = sum(t["stake_total"] for t in pooled)
    pnl_total = sum(t["pnl_total"] for t in pooled)
    won_total = sum(round(t["hit_rate_pct"] * t["n"] / 100.0) for t in pooled)
    odds_weight = sum((t.get("avg_odds") or 0) * t["n"] for t in pooled if t.get("avg_odds"))
    weights = sum(t["n"] for t in pooled if t.get("avg_odds"))
    return {
        "n": n_total,
        "stake_total": round(stake_total, 2),
        "pnl_total": round(pnl_total, 2),
        "roi_pct": round(100.0 * pnl_total / stake_total, 2) if stake_total else 0.0,
        "hit_rate_pct": round(100.0 * won_total / n_total, 2),
        "avg_odds": round(odds_weight / weights, 3) if weights else None,
        "tipsters_pooled": [t["slug"] for t in pooled],
    }, per_tipster


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


def _print_section(title: str, s: dict) -> None:
    print(f"\n[{title}]")
    if not s or s.get("n", 0) == 0:
        print("  (no data)")
        return
    print(f"  n={s['n']:>5}  stake={s.get('stake_total', 0):>9.2f}  "
          f"pnl={s.get('pnl_total', 0):>+9.2f}  "
          f"ROI={s.get('roi_pct', 0):>+6.2f}%  hit={s.get('hit_rate_pct', 0):>5.2f}%  "
          f"avg_odds={s.get('avg_odds', 0)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    raw = load_tipstrr()
    pooled, per_tipster = aggregate_all(raw, start, end)

    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    print()
    print("=" * 80)
    print(f"Tipstrr vs OddsIntel · {start} → {end} · stake 10 EUR flat · pooled")
    print("=" * 80)
    _print_section("Tipstrr (pooled football tipsters, all bet types)", pooled)
    _print_section("OddsIntel (calibrated+beta+active, 1X2 + OU only)", ours)

    enough = pooled.get("n", 0) >= MIN_SAMPLE and ours.get("n", 0) >= MIN_SAMPLE
    status = "ok" if enough else "insufficient-data-pending"
    if not enough:
        print(f"\nNOTE: below MIN_SAMPLE={MIN_SAMPLE} on one side — "
              "publishing as insufficient-data-pending.")

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "source": "Tipstrr",
        "source_url": "https://tipstrr.com/tipster/<slug>/stats",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start, "end": end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "Tipstrr public stats are at (tipster × month) grain — per-bet "
            "selection / market detail is paywalled. Their headline ROI here "
            "covers ALL football bet types (1X2, OU, AH, BTTS, …); ours covers "
            "1X2 + OU 2.5 only. Window-fit: only month-buckets whose entire "
            "span falls inside [start, end) are counted (partial months dropped). "
            "Stake normalised to 10 EUR/bet for parity."
        ),
        "reproducible_via": "scripts/scrape_tipstrr.py + scripts/audit_vs_tipstrr.py",
        "their_stats": pooled,
        "their_breakdown": {"by_tipster": per_tipster},
        "our_stats_same_window": ours,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                      sort_keys=True).encode()
    print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"Wrote: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
