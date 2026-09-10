"""EXECUTABLE-SHADOW-EVAL (2026-09-10) — read-only measurement.

The honest validation ledger the owner asked for: for every active bot's SETTLED
picks, attach the price we could ACTUALLY have gotten at each placeable book
(Coolbet, Unibet-Site) and compute executable ROI per book — versus the recorded
ROI at the stored (best-of-books) odds. This is what tells us whether a bot would
make money AT A BOOK WE CAN BET, not at a reference price we can't (the
SHADOW-PAGE-ROI-INFLATED problem: bot_v10_all showed 17% best-of-books vs 12%
executable).

The odds already exist in `odds_snapshots` (the Coolbet/Unibet sweeps fill them per
match); this just JOINS them onto each pick. No sweep/schema change. Scope v1: the
two placeable families, 1x2 and O/U 2.5. Vocabulary goes through the ONE canonical
mapper `workers.canonical_market.normalize` — never hardcoded (MARKET-VOCAB-ENFORCED).
The executable price per pick = the book's snapshot with timestamp CLOSEST to
pick_time (what we'd have been offered when the pick fired). Read-only.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict


def _load_env():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def run(days: int = 45):
    from workers.api_clients.db import execute_query
    from workers.canonical_market import normalize

    picks = execute_query(
        """SELECT s.id, s.match_id::text AS mid, s.pick_time, s.result,
                  COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float AS rec_odds,
                  s.market, s.selection, b.name AS bot
             FROM shadow_bets s JOIN bots b ON b.id = s.bot_id
            WHERE b.retired_at IS NULL AND s.result IN ('won','lost')
              AND s.pick_time > now() - (%s || ' days')::interval""",
        (str(days),),
    )

    # Normalise every pick through the canonical mapper; keep the placeable families.
    norm_picks = []
    targets = set()  # (mid, canonical_market, canonical_selection) to fetch odds for
    for p in picks:
        n = normalize(p["market"], p["selection"])
        if not n or n["family"] not in ("1x2", "o/u") or not n["market"] or not n["selection"]:
            continue
        key = (p["mid"], n["market"], n["selection"])
        norm_picks.append((p, key))
        targets.add(key)
    if not targets:
        return []

    # One pass: all Coolbet/Unibet-Site snapshots for those (match,market,selection).
    mids = list({t[0] for t in targets})
    snaps = execute_query(
        """SELECT match_id::text AS mid, market, selection, bookmaker,
                  timestamp, odds::float AS odds
             FROM odds_snapshots
            WHERE bookmaker IN ('Coolbet','Unibet-Site') AND odds > 1
              AND match_id::text = ANY(%s)""",
        (mids,),
    )
    book_snaps = defaultdict(list)  # (mid,market,sel,book) -> [(ts, odds)]
    for r in snaps:
        book_snaps[(r["mid"], r["market"], r["selection"], r["bookmaker"])].append(
            (r["timestamp"], r["odds"]))

    def nearest(mid, mkt, sel, book, when):
        cand = book_snaps.get((mid, mkt, sel, book))
        if not cand:
            return None
        return min(cand, key=lambda ts_o: abs((ts_o[0] - when).total_seconds()))[1]

    agg = defaultdict(lambda: {"n": 0, "rec": 0.0,
                               "cb_cov": 0, "cb": 0.0, "ub_cov": 0, "ub": 0.0})
    for p, (mid, mkt, sel) in norm_picks:
        won = p["result"] == "won"
        a = agg[p["bot"]]
        a["n"] += 1
        a["rec"] += (p["rec_odds"] - 1) if won else -1
        for book, ck, ce in (("Coolbet", "cb_cov", "cb"), ("Unibet-Site", "ub_cov", "ub")):
            o = nearest(mid, mkt, sel, book, p["pick_time"])
            if o is not None:
                a[ck] += 1
                a[ce] += (o - 1) if won else -1

    out = []
    for bot, a in agg.items():
        out.append({
            "bot": bot, "n": a["n"],
            "rec_roi": round(100 * a["rec"] / a["n"], 1),
            "cb_cov": a["cb_cov"], "cb_roi": round(100 * a["cb"] / a["cb_cov"], 1) if a["cb_cov"] else None,
            "ub_cov": a["ub_cov"], "ub_roi": round(100 * a["ub"] / a["ub_cov"], 1) if a["ub_cov"] else None,
        })
    return sorted(out, key=lambda r: -r["n"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Executable per-book ROI for all active bots (read-only).")
    ap.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    _load_env()
    rows = run(a.days)
    print(f"EXECUTABLE-SHADOW-EVAL — settled 1x2 + O/U2.5 picks, last {a.days}d")
    print(f"{'bot':<34} {'n':>5} {'recROI':>7} | {'CBcov':>7} {'CBroi':>7} | {'UBcov':>7} {'UBroi':>7}")
    for r in rows:
        print(f"{r['bot']:<34} {r['n']:>5} {str(r['rec_roi'])+'%':>7} | "
              f"{str(r['cb_cov'])+'/'+str(r['n']):>7} {str(r['cb_roi'])+'%':>7} | "
              f"{str(r['ub_cov'])+'/'+str(r['n']):>7} {str(r['ub_roi'])+'%':>7}")
    print("\nrecROI = at stored (best-of-books) odds; CB/UBroi = at the executable book price on the "
          "covered subset. The recROI→CB/UBroi gap is the executable-price honesty gap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
