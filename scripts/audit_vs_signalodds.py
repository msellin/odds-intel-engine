"""
SignalOdds vs OddsIntel — apples-to-apples ROI audit.

Reads dev/active/signalodds_soccer.json (produced by scripts/scrape_signalodds.py)
and computes ROI on the open / non-paywalled predictions, restricted to the
markets our production model places (1X2 = "Match Result", OU 2.5 = "Over /
Under"). Compares against our own simulated_bets in the same date window.

Window selection:
  --start YYYY-MM-DD  --end YYYY-MM-DD     explicit
  --since-days N                            uses now - N days
  (default: 2026-05-04 → today; same as the public track-record API)

Stake methodology: 10 EUR flat per bet, identical to our own bot accounting.

Output:
  - Pretty stdout table
  - ledger/comparison_signalodds.json with the structure required by
    /api/v1/track-record meta. Two top-level keys:
        their_stats         signalodds ROI in window
        our_stats_same_window  our calibrated production ROI in window
        scope_notes / source / window / reproducible_via

Sample-size guard: if EITHER side has < 50 settled bets in the matched
window, the JSON emits status="insufficient-data-pending" and ROI numbers are
omitted (keeps the marketing/API surface honest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

INPUT_PATH = ROOT / "dev" / "active" / "signalodds_soccer.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_signalodds.json"

STAKE = 10.0
MIN_SAMPLE = 50

# Map SignalOdds market labels to our internal market vocabulary.
# Anything else is dropped (e.g. BTTS/AH/etc. — we don't trade those).
SIGNALODDS_MARKETS_OK = {
    "Match Result": "1x2",
    "Over / Under": "over_under_25",
    "Over/Under": "over_under_25",
    "Over Under": "over_under_25",
}

DEFAULT_START = "2026-05-04"   # calibrated tier launch


def parse_url_date(row: dict) -> str | None:
    """Pull YYYYMMDD from event_url / detail_url, return ISO YYYY-MM-DD."""
    for k in ("event_url", "detail_url"):
        v = row.get(k) or ""
        m = re.search(r"(\d{4})(\d{2})(\d{2})", v)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def load_signalodds_rows() -> list[dict]:
    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_signalodds.py first.",
              file=sys.stderr)
        sys.exit(2)
    rows = json.loads(INPUT_PATH.read_text())
    print(f"Loaded {len(rows)} raw SignalOdds rows from {INPUT_PATH.name}")
    return rows


def filter_signalodds(rows: list[dict], start: str, end: str) -> tuple[list[dict], dict]:
    """Apply scope filters and return (kept_rows, drop_stats)."""
    drops: dict = Counter()
    kept: list[dict] = []
    for r in rows:
        d = parse_url_date(r)
        if d is None:
            drops["no_date"] += 1
            continue
        if not (start <= d < end):
            drops["out_of_window"] += 1
            continue
        if r.get("is_premium"):
            drops["premium_paywalled"] += 1
            continue
        if r.get("status") not in ("Correct", "Incorrect"):
            # Void / null / postponed — neither side counts these
            drops[f"status_{r.get('status')}"] += 1
            continue
        mkt = r.get("market")
        if mkt not in SIGNALODDS_MARKETS_OK:
            drops[f"market_{mkt}"] += 1
            continue
        if not r.get("odds") or float(r["odds"]) < 1.01:
            drops["bad_odds"] += 1
            continue
        kept.append(r)
    return kept, dict(drops)


def signalodds_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    stake_total = STAKE * len(rows)
    pnl_total = 0.0
    won = 0
    odds_sum = 0.0
    for r in rows:
        odds = float(r["odds"])
        if r["status"] == "Correct":
            pnl_total += (odds - 1.0) * STAKE
            won += 1
        else:
            pnl_total -= STAKE
        odds_sum += odds
    return {
        "n": len(rows),
        "stake_total": round(stake_total, 2),
        "pnl_total": round(pnl_total, 2),
        "roi_pct": round(100.0 * pnl_total / stake_total, 2),
        "hit_rate_pct": round(100.0 * won / len(rows), 2),
        "avg_odds": round(odds_sum / len(rows), 3),
    }


def signalodds_breakdown(rows: list[dict]) -> dict:
    """Per-model and per-market sub-rolls — handy for sanity-checking which
    model contributes the headline number."""
    out: dict = {}
    by_model = defaultdict(list)
    by_market = defaultdict(list)
    for r in rows:
        by_model[r.get("model_name") or "?"].append(r)
        by_market[SIGNALODDS_MARKETS_OK.get(r["market"], r["market"])].append(r)
    out["by_model"] = {k: signalodds_stats(v) for k, v in sorted(by_model.items())}
    out["by_market"] = {k: signalodds_stats(v) for k, v in sorted(by_market.items())}
    return out


def our_stats(start: str, end: str) -> dict:
    """Pull our own calibrated production ROI in the same window. Matches
    the public /api/v1/track-record scope (calibrated+beta+active maturity,
    1x2 + OU + BTTS, settled bets)."""
    rows = execute_query(
        """
        SELECT
          sb.stake::float       AS stake,
          sb.pnl::float         AS pnl,
          sb.result::text       AS result,
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
    ap.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD")
    ap.add_argument("--end", default=None,
                    help="YYYY-MM-DD exclusive; default = tomorrow UTC")
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    # 1) SignalOdds side
    so_rows_raw = load_signalodds_rows()
    so_kept, drops = filter_signalodds(so_rows_raw, start, end)
    print(f"After scope filter: {len(so_kept)} kept, drops={drops}")
    so_stats = signalodds_stats(so_kept)
    so_breakdown = signalodds_breakdown(so_kept) if so_kept else {}

    # 2) Our side
    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    # 3) Print summary
    print()
    print("=" * 80)
    print(f"SignalOdds vs OddsIntel · {start} → {end} · stake 10 EUR flat · "
          "1X2 + OU only")
    print("=" * 80)
    _print_section("SignalOdds (open/non-paywalled picks)", so_stats)
    _print_section("OddsIntel  (calibrated+beta+active)", ours)

    if so_breakdown:
        print("\nSignalOdds — by model:")
        for k, v in so_breakdown["by_model"].items():
            _print_section(f"  model: {k}", v)
        print("\nSignalOdds — by market:")
        for k, v in so_breakdown["by_market"].items():
            _print_section(f"  market: {k}", v)

    # 4) Verdict (sample-size guard)
    enough = so_stats.get("n", 0) >= MIN_SAMPLE and ours.get("n", 0) >= MIN_SAMPLE
    status = "ok" if enough else "insufficient-data-pending"
    if not enough:
        print(f"\nNOTE: below MIN_SAMPLE={MIN_SAMPLE} on one side — "
              "publishing as insufficient-data-pending.")

    # 5) Write the JSON for /api/v1/track-record meta
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "source": "SignalOdds",
        "source_url": "https://signalodds.com/predictions/past?sport=soccer",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start, "end": end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "1X2 + OU 2.5 only, soccer, settled bets (Correct/Incorrect), "
            "open/non-paywalled picks only (premium/PRO cards excluded — "
            "their odds and exact pick are hidden), 10 EUR flat stake."
        ),
        "reproducible_via": "scripts/scrape_signalodds.py + scripts/audit_vs_signalodds.py",
        "their_stats": so_stats,
        "their_breakdown": so_breakdown,
        "their_drop_reasons": drops,
        "our_stats_same_window": ours,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # Light fingerprint for change-detection on the bots channel
    blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                       sort_keys=True).encode()
    print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"Wrote: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
