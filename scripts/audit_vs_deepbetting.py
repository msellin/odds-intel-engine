"""
DeepBetting vs OddsIntel — apples-to-apples ROI audit.

Reads dev/active/deepbetting_stats.json (produced by scripts/scrape_deepbetting.py)
and computes ROI on the football subset, restricted to the markets our model
trades (Moneyline = 1X2, Over-Under = OU 2.5). Compares against our own
simulated_bets in the same date window.

DeepBetting status semantics (from their dashboard JS):
    "Won"   -> stake returned + odds-1 unit profit. Counts in ROI denominator.
    "Lost"  -> stake lost.                          Counts in ROI denominator.
    "Push"  -> stake refunded (neutral).            Does NOT count.
    null    -> not graded yet.                      Skipped.

Stake methodology: 10 EUR flat per bet (same as audit_vs_signalodds.py and
audit_vs_winnerodds.py).

Note: Bet-Analytix (the third-party auditor DeepBetting embeds) requires a
paid subscription to read via API — see scripts/scrape_bet_analytix.py for
details. This audit therefore uses DeepBetting's own /backend/api/
predictions-api.php endpoint as the authoritative settled history.
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

INPUT_PATH = ROOT / "dev" / "active" / "deepbetting_stats.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_deepbetting.json"

STAKE = 10.0
MIN_SAMPLE = 50
DEFAULT_START = "2026-05-04"   # calibrated tier launch

# DeepBetting market label → internal vocabulary. BTTS + DNB are tracked but
# our production model doesn't trade them so they're outside the scope here.
DB_MARKETS_OK = {
    "Moneyline": "1x2",
    "Over-Under": "over_under_25",
}


def _norm_date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd:
        return None
    s = str(yyyymmdd)
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def load_deepbetting() -> list[dict]:
    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_deepbetting.py first.",
              file=sys.stderr)
        sys.exit(2)
    body = json.loads(INPUT_PATH.read_text())
    data = body.get("data") or {}
    fb = data.get("football") or []
    print(f"Loaded {len(fb)} football picks from {INPUT_PATH.name}")
    return fb


def filter_picks(rows: list[dict], start: str, end: str) -> tuple[list[dict], dict]:
    drops: Counter = Counter()
    kept = []
    for r in rows:
        d = _norm_date(r.get("date_norm"))
        if d is None:
            drops["no_date"] += 1
            continue
        if not (start <= d < end):
            drops["out_of_window"] += 1
            continue
        if r.get("game_status") != "Match Finished":
            drops[f"game_status_{r.get('game_status')}"] += 1
            continue
        if r.get("forecast_status") not in ("Won", "Lost"):
            # Push refunds stake — not in denominator
            drops[f"status_{r.get('forecast_status')}"] += 1
            continue
        ft = r.get("forecast_type")
        if ft not in DB_MARKETS_OK:
            drops[f"market_{ft}"] += 1
            continue
        try:
            o = float(r.get("odds"))
            if o < 1.01:
                drops["bad_odds"] += 1
                continue
        except (TypeError, ValueError):
            drops["bad_odds"] += 1
            continue
        kept.append(r)
    return kept, dict(drops)


def deepbetting_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    pnl = 0.0
    won = 0
    odds_sum = 0.0
    for r in rows:
        o = float(r["odds"])
        if r["forecast_status"] == "Won":
            pnl += (o - 1.0) * STAKE
            won += 1
        else:
            pnl -= STAKE
        odds_sum += o
    stake = STAKE * len(rows)
    return {
        "n": len(rows),
        "stake_total": round(stake, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100.0 * pnl / stake, 2),
        "hit_rate_pct": round(100.0 * won / len(rows), 2),
        "avg_odds": round(odds_sum / len(rows), 3),
    }


def deepbetting_breakdown(rows: list[dict]) -> dict:
    out: dict = {}
    by_market = defaultdict(list)
    by_conf = defaultdict(list)
    by_division = defaultdict(list)
    for r in rows:
        by_market[DB_MARKETS_OK.get(r["forecast_type"], r["forecast_type"])].append(r)
        by_conf[str(r.get("confidence") or "?")].append(r)
        by_division[r.get("division_label") or "?"].append(r)
    out["by_market"] = {k: deepbetting_stats(v) for k, v in sorted(by_market.items())}
    out["by_confidence"] = {k: deepbetting_stats(v) for k, v in sorted(by_conf.items())}
    # Only show divisions with >= 15 picks to keep noise down
    out["by_division_top"] = {
        k: deepbetting_stats(v)
        for k, v in sorted(by_division.items(), key=lambda kv: -len(kv[1]))[:15]
        if len(v) >= 15
    }
    return out


def our_stats(start: str, end: str) -> dict:
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
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    raw = load_deepbetting()
    kept, drops = filter_picks(raw, start, end)
    print(f"After scope filter: {len(kept)} kept, drops={drops}")
    db = deepbetting_stats(kept)
    bd = deepbetting_breakdown(kept) if kept else {}

    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    print()
    print("=" * 80)
    print(f"DeepBetting vs OddsIntel · {start} → {end} · stake 10 EUR flat · "
          "1X2 + OU only")
    print("=" * 80)
    _print_section("DeepBetting (Moneyline + Over-Under)", db)
    _print_section("OddsIntel   (calibrated+beta+active)", ours)

    if bd:
        print("\nDeepBetting — by market:")
        for k, v in bd["by_market"].items():
            _print_section(f"  market: {k}", v)
        print("\nDeepBetting — by confidence tier:")
        for k, v in bd["by_confidence"].items():
            _print_section(f"  conf {k}", v)
        if bd["by_division_top"]:
            print("\nDeepBetting — top divisions (>=15 picks):")
            for k, v in bd["by_division_top"].items():
                _print_section(f"  {k}", v)

    enough = db.get("n", 0) >= MIN_SAMPLE and ours.get("n", 0) >= MIN_SAMPLE
    status = "ok" if enough else "insufficient-data-pending"
    if not enough:
        print(f"\nNOTE: below MIN_SAMPLE={MIN_SAMPLE} on one side — "
              "publishing as insufficient-data-pending.")

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "source": "DeepBetting",
        "source_url": "https://deepbetting.io/dashboard/  "
                      "(via /backend/api/predictions-api.php?type=stats)",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start, "end": end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "Moneyline (1X2) + Over-Under (2.5) only, soccer, settled bets "
            "(Won/Lost — Push refunds excluded), 10 EUR flat stake. "
            "Bet-Analytix integration audit excluded (requires paid subscription)."
        ),
        "reproducible_via": "scripts/scrape_deepbetting.py + scripts/audit_vs_deepbetting.py",
        "their_stats": db,
        "their_breakdown": bd,
        "their_drop_reasons": drops,
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
