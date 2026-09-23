"""ANCHOR CLOSING-LINE VALIDATION ([[#113]] phase 3, 2026-09-23) — the gate before any
OWN-betting consumer trusts a consensus anchor.

Read-only.   python3 scripts/anchor_closing_validation.py --days 7

WHY
---
scripts/anchor_consensus_composition.py showed a consensus TIES AF-Pinnacle on
outcomes. Outcome log-loss cannot see a 1–2% price error, and 1–2% is the size of
the edges we act on. The sharper test for an anchor used to call an edge is whether,
AT DECISION TIME, it predicts where the market CLOSES: an anchor that already sits
where the close will be is one whose "edge" survives to the close (= CLV).

DESIGN
  * Finished fixtures in the last `--days` (retention keeps full intraday history
    only ~7 days — ANALYSIS_GOTCHAS §59).
  * Decision times T = KO−120 and KO−30 min. Anchors at T, via the production
    resolver's pure core (workers/utils/anchor.py):
      pin    Pinnacle alone, fresh <= 60 min at T
      cons   consensus of >=5 books EXCLUDING Pinnacle
      bet365 single soft book (negative control — should lose to both)
  * Targets, BOTH reported so neither anchor gets home advantage:
      pin_close   Pinnacle's latest set in [KO−15, KO]
      cons_close  consensus (ex-Pinnacle) at KO
  * Error = max over sides of |p_anchor − p_target| (probability points).
    Paired per fixture: Δ = err(cons) − err(pin); negative = consensus closer.
  * 1X2 and O/U 2.5. |t| < 2.5 read as a tie (several simultaneous tests).
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import timedelta
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402
from workers.utils.anchor import PIN, compute_anchor, load_sets, market_sides  # noqa: E402


def err(a: dict | None, b: dict | None) -> float | None:
    if not a or not b:
        return None
    return max(abs(a[k] - b[k]) for k in a)


def single(sets: dict, book: str, at, max_age_min: float = 60) -> dict | None:
    s = sets.get(book)
    if not s or (at - s[1]).total_seconds() / 60 > max_age_min:
        return None
    p = devig(s[0])
    return p and dict(zip(SIDES_CUR, p))


SIDES_CUR: tuple = ()


def paired(rows, a, b):
    d = [r[a] - r[b] for r in rows if r.get(a) is not None and r.get(b) is not None]
    if len(d) < 30:
        return None
    n, mu = len(d), mean(d)
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1)) if n > 1 else 0
    t = mu / (sd / math.sqrt(n)) if sd else 0.0
    return n, mu, median(d), sum(1 for x in d if x < 0) / n, t


def main():
    global SIDES_CUR
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    fixtures = execute_query(
        """SELECT m.id::text id, m.date ko FROM matches m
            WHERE m.status = 'finished' AND m.date > now() - make_interval(days => %s)
              AND EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.match_id = m.id
                          AND o.bookmaker = 'Pinnacle' AND o.market = '1x2'
                          AND o.timestamp BETWEEN m.date - interval '15 minutes' AND m.date)
            ORDER BY m.date""", (a.days,)) or []
    if a.limit:
        fixtures = fixtures[:a.limit]
    print(f"{len(fixtures)} finished fixtures with a Pinnacle close in the last {a.days} d")
    for market in ("1x2", "over_under_25"):
        SIDES_CUR = market_sides(market)
        for lead in (120, 30):
            rows = []
            for f in fixtures:
                ko = f["ko"]
                t = ko - timedelta(minutes=lead)
                s_t = load_sets(f["id"], market, SIDES_CUR, at=t, lookback_min=240)
                s_c = load_sets(f["id"], market, SIDES_CUR, at=ko, lookback_min=240)
                pin_close = single(s_c, PIN, ko, max_age_min=15)
                cons_close = compute_anchor({b: v for b, v in s_c.items() if b != PIN}, SIDES_CUR,
                                            at=ko, max_age_min=60)
                cons_close = cons_close.probs if cons_close.source == "consensus" else None
                pin_t = single(s_t, PIN, t)
                cons_t = compute_anchor({b: v for b, v in s_t.items() if b != PIN}, SIDES_CUR, at=t,
                                        max_age_min=90)
                cons_t = cons_t.probs if cons_t.source == "consensus" else None
                junk_t = single(s_t, "Bet365", t)
                tight = bool(s_c.get(PIN)) and (sum(1 / o for o in s_c[PIN][0]) - 1) <= 0.04
                rows.append({"tight": tight,
                             "pin→pinC": err(pin_t, pin_close), "cons→pinC": err(cons_t, pin_close),
                             "b365→pinC": err(junk_t, pin_close),
                             "pin→consC": err(pin_t, cons_close), "cons→consC": err(cons_t, cons_close),
                             "b365→consC": err(junk_t, cons_close)})
            print(f"\n=== {market} · decision at KO−{lead} min ===")
            for label, sub in (("all", rows), ("Pinnacle close TIGHT (<=4%)", [r for r in rows if r["tight"]])):
                print(f"  [{label}]  (Δ in probability points; negative = first anchor closer)")
                for x, y in (("cons→pinC", "pin→pinC"), ("cons→consC", "pin→consC"),
                             ("b365→pinC", "pin→pinC"), ("b365→consC", "cons→consC")):
                    r = paired(sub, x, y)
                    if r:
                        n, mu, md, w, tt = r
                        mx = mean(v[x] for v in sub if v.get(x) is not None and v.get(y) is not None)
                        my = mean(v[y] for v in sub if v.get(x) is not None and v.get(y) is not None)
                        verdict = "tie" if abs(tt) < 2.5 else (x.split("→")[0] + " closer" if mu < 0 else y.split("→")[0] + " closer")
                        print(f"    {x:11s} vs {y:11s} n={n:5d} err {mx:.4f} vs {my:.4f}  Δ {mu:+.4f} "
                              f"(med {md:+.4f}, wins {100*w:4.1f}%, t {tt:+.2f})  {verdict}")


if __name__ == "__main__":
    main()
