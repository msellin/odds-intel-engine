"""
Audit OddsIntel vs WinnerOdds in a matched window. Writes the standard
ledger/comparison_winnerodds.json that the landing page reads at runtime.

Mirrors the shape of scripts/audit_vs_{signalodds,deepbetting,forebet,
tipstrr,betaminic}.py — same `their_stats`, `our_stats_same_window`,
`scope_notes` keys so the consolidated landing fetch works
uniformly.

Reuses the proven wo_pull_window + wo_summary helpers from
scripts/production_audit_vs_winnerodds.py (which is the heavy
multi-pass audit that produces the per-country breakdown). This
script just snapshots the headline numbers to a JSON that the web
landing can fetch.

Usage:
    python3 scripts/audit_vs_winnerodds.py
    python3 scripts/audit_vs_winnerodds.py --start 2026-05-04 --end 2026-06-25
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))
load_dotenv()

from scripts.production_audit_vs_winnerodds import wo_pull_window, wo_summary  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402


WINDOW_START_DEFAULT = "2026-05-04"
# COMPETITOR-SCRAPES-WEEKLY-2026-08-01: end defaults to tomorrow so the landing
# comparison card grows daily instead of staying frozen at the 2026-06-25 value
# hardcoded during initial scaffolding.
WINDOW_END_DEFAULT = (date.today() + timedelta(days=1)).isoformat()
STAKE = 10.0
MIN_SAMPLE = 50


# WINNERODDS-MARKET-RESOLVE (2026-09-02) ─────────────────────────────────────
# WinnerOdds' `apuesta` field is a compact pick code that fully identifies the
# market. We were writing market="mixed" and throwing that away, which made the
# landing compare their ALL-markets ROI against our 1X2+OU2.5 ROI. It is not a
# small mismatch: 47% of their bets are Asian Handicap, a market we do not model
# at all, and another 16% are OU 3.5.
#
# Codes seen across the full window (n=1852): 1 / x / 2, o2.5 / u2.5,
# o3.5 / u3.5, and ah{+,-}{0,0.5}_{1,2}.
_OU_RE = re.compile(r"^([ou])(\d+(?:\.\d+)?)$")


def market_of_pick(apuesta: str) -> str:
    """Market implied by a WinnerOdds pick code, or 'other' if unrecognised.

    'other' rather than a guess: an unknown code silently bucketed into 1x2
    would corrupt the comparable subset, which is the number the landing
    publishes.
    """
    p = (apuesta or "").strip().lower()
    if p in ("1", "x", "2"):
        return "1x2"
    m = _OU_RE.match(p)
    if m:
        return "over_under_" + m.group(2).replace(".", "")
    if p.startswith("ah"):
        return "asian_handicap"
    return "other"


# The market pair we actually compete on. OU 3.5 is deliberately excluded: our
# own cohort query is 1x2 + OU 2.5, so including their OU 3.5 would reintroduce
# the very mismatch this fixes.
COMPARABLE_MARKETS = ("1x2", "over_under_25")

# Their status vocabulary, from wo_summary. LOOSE is their spelling of a loss —
# the picks-CSV export used to test for "lose", so every one of the 752 losses
# in the window fell through to an empty result and the published CSV showed
# only wins and voids. Anyone recomputing ROI from that file got +86% instead
# of +4.5%. Kept as one mapping so the CSV and the summary cannot drift again.
_WON = ("WIN", "HALF_WIN")
_LOST = ("LOSE", "LOOSE", "HALF_LOSE")


def result_of_status(status: str) -> str:
    st = (status or "").upper()
    if st in _WON:
        return "won"
    if st in _LOST:
        return "lost"
    return "void" if st == "VOID" else ""


def their_comparable(rows: list[dict]) -> dict:
    """Their 1X2 + OU 2.5 record, restaked flat at €10 to match our method.

    Two corrections in one. (a) Market: only the markets we also price.
    (b) Staking: WinnerOdds stakes Kelly-style (mean stake €37 over the
    window, not the €10 the old scope_notes claimed), and ROI on turnover is
    not comparable across two different staking schemes. Re-settling their
    picks at a flat €10 removes that difference; it changes little here
    (+7.82% Kelly vs +7.67% flat) but it makes the comparison defensible
    rather than coincidentally close.
    """
    n = won = 0
    pnl = 0.0
    for r in rows:
        if market_of_pick(r.get("apuesta")) not in COMPARABLE_MARKETS:
            continue
        res = result_of_status(r.get("status"))
        if res not in ("won", "lost"):        # void is not a settled bet
            continue
        try:
            odds = float(r.get("cuota") or 0)
        except (TypeError, ValueError):
            continue
        if odds <= 1:
            continue
        n += 1
        if res == "won":
            won += 1
            pnl += (odds - 1) * STAKE
        else:
            pnl -= STAKE
    stake = n * STAKE
    return {
        "n": n,
        "stake_total": round(stake, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100 * pnl / stake, 2) if stake else 0,
        "hit_rate_pct": round(100 * won / n, 2) if n else 0,
    }


def their_by_market(rows: list[dict]) -> dict:
    """Per-market breakdown at flat stake — shows how much of their record is
    in markets we do not offer."""
    agg: dict = {}
    for r in rows:
        mk = market_of_pick(r.get("apuesta"))
        res = result_of_status(r.get("status"))
        if res not in ("won", "lost"):
            continue
        try:
            odds = float(r.get("cuota") or 0)
        except (TypeError, ValueError):
            continue
        if odds <= 1:
            continue
        a = agg.setdefault(mk, {"n": 0, "pnl": 0.0, "won": 0})
        a["n"] += 1
        if res == "won":
            a["won"] += 1
            a["pnl"] += (odds - 1) * STAKE
        else:
            a["pnl"] -= STAKE
    return {
        mk: {
            "n": a["n"],
            "roi_pct": round(100 * a["pnl"] / (a["n"] * STAKE), 2) if a["n"] else 0,
            "hit_rate_pct": round(100 * a["won"] / a["n"], 2) if a["n"] else 0,
        }
        for mk, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"])
    }



# STALE-ODDS-HISTORY-RESTATE-2026-09-02: our side of the comparison now comes
# from scripts/_our_stats.py, which prices at odds that were LIVE at pick time
# rather than summing `pnl` (settled from the inflated `odds_at_pick`). Six
# copies of this query existed; five would have kept publishing the old number.
from scripts._our_stats import our_stats  # noqa: E402,F401


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START_DEFAULT)
    ap.add_argument("--end", default=WINDOW_END_DEFAULT)
    args = ap.parse_args()

    print(f"Auditing OddsIntel vs WinnerOdds  {args.start} → {args.end}")
    print()
    print("Pulling WinnerOdds public picks...")
    rows = wo_pull_window(args.start, args.end)
    their_all = wo_summary(rows)
    their = their_comparable(rows)
    by_mkt = their_by_market(rows)
    print(f"  WO all markets : n={their_all['n']}  ROI {their_all['roi']:+.2f}%  "
          f"hit {their_all['hit_rate']:.1f}%   (their Kelly staking)")
    print(f"  WO comparable  : n={their['n']}  ROI {their['roi_pct']:+.2f}%  "
          f"hit {their['hit_rate_pct']:.1f}%   (1X2 + OU2.5, flat EUR{STAKE:.0f})")
    for mk, st in by_mkt.items():
        print(f"      {mk:18} n={st['n']:5}  ROI {st['roi_pct']:+7.2f}%")
    print()

    ours = our_stats(args.start, args.end)
    print(f"  OddsIntel matched: n={ours['n']}  ROI {ours['roi_pct']:+.2f}%")

    status = "ok" if (their["n"] >= MIN_SAMPLE and ours["n"] >= MIN_SAMPLE) else "insufficient_sample"

    payload = {
        "source": "WinnerOdds",
        "source_url": "https://winnerodds.com",
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start, "end": args.end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "WinnerOdds picks pulled from their public GraphQL endpoint "
            "(period=12, FOOTBALL). their_stats is the COMPARABLE subset: "
            "their 1X2 + OU 2.5 picks only, re-settled at €10 flat to match "
            "our staking. WINNERODDS-MARKET-RESOLVE 2026-09-02 — this used to "
            "publish their all-markets figure against our 1X2+OU2.5 figure, "
            "which is not the same measurement: 47% of their bets are Asian "
            "Handicap (a market we do not model) and 16% are OU 3.5, and they "
            "stake Kelly-style (mean €37) where we stake flat. Their "
            "all-markets number is kept as their_stats_all_markets, and the "
            "full split as their_by_market. OddsIntel cohort: production "
            "strategies (calibrated+beta+active maturity), 1X2 + OU 2.5, "
            "settled (won/lost), €10 flat, inplay_* excluded."
        ),
        "reproducible_via": "scripts/audit_vs_winnerodds.py",
        "their_stats": {**their, "avg_clv_pct": round(their_all["avg_clv"], 4)},
        "their_stats_all_markets": {
            "n": their_all["n"],
            "stake_total": round(their_all["stake"], 2),
            "pnl_total": round(their_all["pnl"], 2),
            "roi_pct": round(their_all["roi"], 2),
            "hit_rate_pct": round(their_all["hit_rate"], 2),
            "avg_clv_pct": round(their_all["avg_clv"], 4),
            "staking": "their own (Kelly-style, variable)",
        },
        "their_by_market": by_mkt,
        "our_stats_same_window": ours,
    }

    out = Path("ledger") / "comparison_winnerodds.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")

    from scripts._picks_csv import compute_pnl, write_picks_csv  # noqa: E402
    picks_out = Path("ledger") / "picks_winnerodds.csv"
    csv_rows = []
    for r in rows:
        try:
            odds_f = float(r.get("cuota") or 0)
        except (TypeError, ValueError):
            odds_f = None
        result = result_of_status(r.get("status"))
        csv_rows.append({
            "source": "winnerodds",
            "kickoff_date": (r.get("fecha_apuesta") or "")[:10],
            "league": r.get("country") or "",
            "home_team": "",
            "away_team": "",
            "market": market_of_pick(r.get("apuesta")),
            "pick": r.get("apuesta") or "",
            "odds": f"{odds_f:.3f}" if odds_f else "",
            "result": result,
            "pnl_per_unit": compute_pnl(odds_f, result),
            "ref_url": "https://winnerodds.com",
        })
    n_csv = write_picks_csv(picks_out, csv_rows)
    print(f"Wrote {n_csv} rows to {picks_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
