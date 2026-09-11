"""DIRECT-BOOK-CLV-2026-09-11 — where did each of OUR books close vs the price we took?

For every settled real bet: our price, then the close (last pre-kickoff,
non-live price within --max-min of kickoff) at each direct book we collect —
Coolbet, Unibet-Site, Epicbet — plus de-vigged Pinnacle CLV.

It answers three questions the single `real_bets.clv` number cannot:
  1. Did we bet too early?  -> own-book CLV by lead-time bucket. Negative =
     our own book drifted OUT after we bet (we could have had more).
  2. Would another venue have closed better? -> best-of-three close vs ours.
  3. Is the market moving against our picks? -> own-book CLV next to Pinnacle
     CLV. A price drifting out while Pinnacle CLV is also negative means the
     sharp market disagreed — waiting would not have produced the bet at all.

Coverage is honest: a book with no fresh close is "—", never a stale price.
Until NEAR-KICKOFF-CAPTURE has been writing a few days, direct-book closes
exist only when a sweep happened to land near kickoff.

    python3 scripts/direct_book_clv_report.py
    python3 scripts/direct_book_clv_report.py --days 14 --max-min 30
"""
import argparse
import sys
from pathlib import Path
from statistics import mean, median

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _normalize_bet_market, _normalize_bet_selection, _VENUE_SNAPSHOT_BOOK,
    get_book_close, get_devigged_pinnacle_close_prob,
)

BOOKS = ("Coolbet", "Unibet-Site", "Epicbet")
BUCKETS = ((1, "<1h"), (3, "1-3h"), (6, "3-6h"), (12, "6-12h"), (1e9, "12h+"))


def pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def summarise(label, vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return f"  {label:<24} n=0"
    return (f"  {label:<24} n={len(vals):<4} mean {pct(mean(vals)):>7}  "
            f"median {pct(median(vals)):>7}  beat/equal close {sum(v >= 0 for v in vals) / len(vals):.0%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--max-min", type=int, default=60, help="freshness bound for a close")
    args = ap.parse_args()

    days = f"AND rb.placed_at >= NOW() - INTERVAL '{int(args.days)} days'" if args.days else ""
    bets = execute_query(
        f"""SELECT rb.id, rb.match_id::text AS match_id, rb.market, rb.selection,
                   rb.bookmaker, rb.actual_odds, rb.placed_at, m.date AS kickoff
              FROM real_bets rb JOIN matches m ON m.id = rb.match_id
             WHERE rb.combo_legs IS NULL AND rb.actual_odds IS NOT NULL
               AND m.date < NOW() {days}
             ORDER BY m.date""",
        [],
    ) or []
    if not bets:
        print("No real bets with a kickoff in the past.")
        return

    rows = []
    for b in bets:
        mk = _normalize_bet_market(b["market"], b["selection"])
        sel = _normalize_bet_selection(b["selection"])
        odds = float(b["actual_odds"])
        closes = {bk: get_book_close(b["match_id"], mk, sel, bk, args.max_min) for bk in BOOKS}
        own = _VENUE_SNAPSHOT_BOOK.get((b["bookmaker"] or "").lower())
        own_close = closes.get(own) if own else None
        fresh = [c[0] for c in closes.values() if c]
        true_p = get_devigged_pinnacle_close_prob(b["match_id"], mk, sel)
        lead_h = (b["kickoff"] - b["placed_at"]).total_seconds() / 3600
        rows.append({
            "b": b, "odds": odds, "closes": closes, "lead_h": lead_h,
            "own_clv": odds / own_close[0] - 1 if own_close else None,
            "best_clv": odds / max(fresh) - 1 if fresh else None,
            "pin_clv": odds * true_p - 1 if true_p else None,
        })

    print(f"╔══ Direct-book CLV — {len(rows)} real bets, close = last pre-KO price ≤{args.max_min} min before kickoff ══╗\n")
    print(f"  {'kickoff':<12} {'bet':<22} {'ours':>5}  " + "  ".join(f"{bk[:10]:>14}" for bk in BOOKS) + f"  {'own CLV':>8} {'Pin CLV':>8}")
    for r in rows[-40:]:
        b = r["b"]
        cells = "  ".join(
            f"{r['closes'][bk][0]:>6.2f} ({r['closes'][bk][1]:>2}m)" if r["closes"][bk] else f"{'—':>14}"
            for bk in BOOKS)
        print(f"  {b['kickoff']:%m-%d %H:%M}  {(b['market'] + ' ' + b['selection'])[:22]:<22} {r['odds']:>5.2f}  "
              f"{cells}  {pct(r['own_clv']):>8} {pct(r['pin_clv']):>8}")
    if len(rows) > 40:
        print(f"  … {len(rows) - 40} older rows not shown")

    print("\n— Coverage (fresh close exists) —")
    for bk in BOOKS:
        n = sum(1 for r in rows if r["closes"][bk])
        print(f"  {bk:<12} {n}/{len(rows)}")

    print("\n— Summary —")
    print(summarise("own-book CLV", [r["own_clv"] for r in rows]))
    print(summarise("vs best direct close", [r["best_clv"] for r in rows]))
    print(summarise("Pinnacle CLV (de-vig)", [r["pin_clv"] for r in rows]))

    print("\n— Own-book CLV by how early we bet (negative = the price drifted out after we bet) —")
    for hi, label in BUCKETS:
        lo = max((h for h, _ in BUCKETS if h < hi), default=0)
        print(summarise(label, [r["own_clv"] for r in rows if lo <= r["lead_h"] < hi]))

    both = [r for r in rows if r["own_clv"] is not None and r["pin_clv"] is not None]
    drift_out = [r for r in both if r["own_clv"] < 0]
    if drift_out:
        against = sum(r["pin_clv"] < 0 for r in drift_out)
        print(f"\n  Of {len(drift_out)} bets whose own book drifted out, {against} also had negative Pinnacle CLV "
              f"(the market moved against the pick — waiting would likely have killed the edge, not improved it).")


if __name__ == "__main__":
    main()
