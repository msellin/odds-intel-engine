#!/usr/bin/env python3
"""FLOOR-GRID-SWEEP — the full (edge floor x odds floor) matrix, per market, per
selection type, across every dataset we have. PRE-MATCH ONLY.

WHY THIS EXISTS (2026-09-11, owner request: "do a proper full sweep again... use
all edge % dimensions and all odds floors... present it all as very detailed
table where all possible combinations are tested, also over different data sets,
up to the largest we can do").

Every previous sweep in this repo answered a NARROWER question and they kept
disagreeing, which is how the 1x2 floor changed four times (0.03 -> 0.10 -> 0.15
-> 0.10 -> 0.13, three of those in one day):

  * `edge_floor_backtest.py`   — edge floors only, market pooled, no selection split
  * `favlong_floor_backtest.py`— fav-vs-long, then per selection, ONE odds floor
  * `bot_2d_audit.py`          — the 2D matrix, but PER BOT (held-out), not per
                                 market/selection

None of them swept edge x odds jointly per selection type. That is the actual
decision surface, because the two floors interact: the odds floor alone excludes
home favourites (a home fav is priced under ~2.0), so an edge floor that looks
necessary without an odds floor can be redundant with one.

METHOD (identical to the canonical one — this adds dimensions, not new maths):
  * EXECUTABLE price: `COALESCE(odds_at_pick_live, odds_at_pick)`. The plain
    `odds_at_pick` is a snapshot high-water mark overstating by ~+0.25 decimal
    points (STALE-BEST-ODDS), so it is not a price we could have taken.
  * WALK-FORWARD folds, and a cell counts as robust ONLY if every fold has bets
    AND every fold is positive. An empty fold FAILS robustness — "it never lost
    in a window it never traded in" is not evidence.
  * ONE FOLD PARTITION per (dataset, market), shared by every group and cell
    (see `_fold_bounds`). The first version of this script built folds per
    group, like `edge_floor_backtest._sweep` does, and that made the robust flag
    depend on the grouping: `HOME (all odds)` and `HOME-DOG >=2.80` at
    edge>=10%/odds>=3.20 are the SAME 129 bets, yet one printed robust and the
    other did not, purely because the boundaries came from 407 rows vs 236. A
    verdict that changes with the grouping is not a property of the bets and
    cannot support a floor decision.
  * PRE-MATCH ONLY — `inplay%` bots are excluded everywhere, no exceptions.
    They are retired (in-play betting retired 2026-08-21), they are a different
    bet type, and on 2026-09-11 they single-handedly faked an away-selection
    result (+15.4% "robust" on n=364 that was really retired in-play bots, one
    at n=14/+452%). See ANALYSIS_GOTCHAS §47.
  * MIN-N GATE: a cell is only ADOPTABLE at n >= --min-n. Without it the grid
    recommends 4-bet cells at +300%, which is how the 15% overfit happened.

DATASETS (smallest/most-trustworthy to largest, per gotcha 18: these are
DIFFERENT bot families writing to different ledgers, so they are separate
datasets and are never unioned):
  1. simulated_bets, calibrated only        — the real-money-relevant slice
  2. simulated_bets, calibrated+beta+active — the working cohort
  3. simulated_bets, ALL pre-match bots     — incl. retired
  4. shadow_bets_unique, ALL pre-match      — the sweep/shadow families, largest
     (always the deduped VIEW, never the base table — gotcha 5)

READING THE OUTPUT: agreement ACROSS datasets is the signal. A cell that is
robust in the calibrated slice and also in the largest one is a real floor. A
cell robust in only one is a regime, and the wider dataset is usually the one
telling the truth about volume while the calibrated one tells the truth about
the model we actually run.

Usage:
  python3 scripts/floor_grid_sweep.py                      # everything
  python3 scripts/floor_grid_sweep.py --market 1x2
  python3 scripts/floor_grid_sweep.py --min-n 100 --folds 3
  python3 scripts/floor_grid_sweep.py --summary-only       # skip the matrices
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

EDGE_FLOORS = [0.00, 0.03, 0.05, 0.08, 0.10, 0.12, 0.13, 0.15, 0.18, 0.20]
ODDS_FLOORS = [1.00, 1.50, 1.80, 2.00, 2.20, 2.50, 2.80, 3.20, 4.00]

# Selection groups per market. Each is (label, predicate(sel, odds)).
# The home-fav/mid/underdog bands are ODDS-defined, so they interact with the
# odds-floor axis by construction: an odds floor above a band empties it, which
# shows up as n=0. That is informative, not a bug — it is the interaction the
# owner asked about ("their odds are below 2.8 anyways?").
_1X2_GROUPS = [
    ("ALL selections",          lambda s, o: True),
    ("HOME (all odds)",         lambda s, o: s == "home"),
    ("HOME-FAV  <2.00",         lambda s, o: s == "home" and o < 2.00),
    ("HOME-MID  2.00-2.80",     lambda s, o: s == "home" and 2.00 <= o < 2.80),
    ("HOME-DOG  >=2.80",        lambda s, o: s == "home" and o >= 2.80),
    ("DRAW",                    lambda s, o: s in ("draw", "x")),
    ("AWAY",                    lambda s, o: s == "away"),
]
_OU_GROUPS = [
    ("ALL selections",          lambda s, o: True),
    ("OVER",                    lambda s, o: s.startswith("over")),
    ("UNDER",                   lambda s, o: s.startswith("under")),
]
MARKETS = [
    ("1x2",           _1X2_GROUPS),
    ("over_under_25", _OU_GROUPS),
    ("over_under_35", _OU_GROUPS),
]

# The gates that are LIVE today, so every grid can be read against reality
# rather than in the abstract. Sourced from the engine, never re-typed.
def _live_gate(market: str) -> tuple[float, float] | None:
    try:
        from workers.automation.coolbet_placer import _min_edge_for, _min_odds_for
        key = "1x2" if market == "1x2" else "o/u"
        e = _min_edge_for(key)
        return (None if e == float("inf") else e), _min_odds_for(key)
    except Exception:  # noqa: BLE001
        return None


# ── data loading ─────────────────────────────────────────────────────────────
_DATASETS = [
    ("sim/calibrated",      "simulated_bets",      "calibrated"),
    ("sim/cohort",          "simulated_bets",      "cohort"),
    ("sim/all-prematch",    "simulated_bets",      "all"),
    ("shadow/all-prematch", "shadow_bets_unique",  "all"),
]


def _load(table: str, maturity: str, market: str) -> list[dict]:
    """Settled PRE-MATCH picks for one market, at the executable price."""
    if maturity == "calibrated":
        mat = "AND b.maturity_label = 'calibrated'"
    elif maturity == "cohort":
        mat = "AND b.maturity_label IN ('calibrated','beta','active')"
    else:
        mat = ""
    # shadow_bets has `pick_time`; simulated_bets has it too. Both carry
    # odds_at_pick_live for the executable price.
    return execute_query(f"""
        SELECT lower(x.selection) sel,
               x.edge_percent::float ep,
               COALESCE(x.odds_at_pick_live, x.odds_at_pick)::float odds,
               x.pick_time,
               (CASE WHEN x.result = 'won'
                     THEN (COALESCE(x.odds_at_pick_live, x.odds_at_pick) - 1)
                     ELSE -1 END)::float ret
          FROM {table} x JOIN bots b ON b.id = x.bot_id
         WHERE lower(x.market) = %s
           AND x.result IN ('won','lost')
           AND x.edge_percent IS NOT NULL
           AND COALESCE(x.odds_at_pick_live, x.odds_at_pick) IS NOT NULL
           AND b.name NOT LIKE 'inplay%%'          -- PRE-MATCH ONLY, always
           {mat}
    """, (market,))


# ── grid maths (same semantics as edge_floor_backtest._sweep) ────────────────
def _fold_bounds(rows: list[dict], n: int) -> list:
    """Fixed time boundaries for the whole (dataset, market), used by EVERY
    group and cell.

    FOLD-PARTITION-FIX (2026-09-11). The first version of this script built
    folds per GROUP, the way `edge_floor_backtest._sweep` does — and that makes
    the robustness flag depend on which group you happen to be looking at.
    Observed directly: `HOME (all odds)` at edge>=10% / odds>=3.20 and
    `HOME-DOG >=2.80` at the same cell are the SAME 129 bets, yet one printed
    robust and the other did not, purely because the fold boundaries were
    derived from 407 rows in one case and 236 in the other.

    A robustness verdict that changes with the grouping is not a property of the
    bets, so it cannot support a floor decision. Fixing the boundaries once per
    (dataset, market) makes every cell in every group comparable: the folds are
    the same calendar windows throughout, and a cell is robust iff it was
    positive in each of those windows.

    Returns the n-1 cutoff timestamps (equal-count quantiles of the full set).
    """
    xs = sorted(r["pick_time"] for r in rows)
    if not xs:
        return []
    step = len(xs) / n
    return [xs[min(len(xs) - 1, int(step * (i + 1)))] for i in range(n - 1)]


def _fold_of(when, bounds: list) -> int:
    for i, b in enumerate(bounds):
        if when < b:
            return i
    return len(bounds)


def _cell(rows: list[dict], bounds: list, ef: float, of: float,
          n_folds: int) -> dict:
    """One (edge, odds) cell, scored against the FIXED fold boundaries.

    A cell is robust only if every fold has bets AND every fold is positive. An
    empty fold therefore fails it — deliberately: "it never lost in a window it
    never traded in" is not evidence of robustness.
    """
    kept = [r for r in rows if r["ep"] >= ef and r["odds"] >= of]
    if not kept:
        return {"n": 0, "roi": None, "robust": False, "folds": []}
    roi = 100 * st.mean([r["ret"] for r in kept])
    buckets: list[list[float]] = [[] for _ in range(n_folds)]
    for r in kept:
        buckets[_fold_of(r["pick_time"], bounds)].append(r["ret"])
    frois = [100 * st.mean(b) if b else None for b in buckets]
    robust = all(f is not None and f > 0 for f in frois)
    return {"n": len(kept), "roi": roi, "robust": robust, "folds": frois}


def _fmt(c: dict, min_n: int) -> str:
    if c["n"] == 0:
        return f"{'—':>13}"
    mark = "✓" if c["robust"] else " "
    thin = "" if c["n"] >= min_n else "?"   # ? = below the min-n gate
    return f"{c['roi']:>+7.1f}{mark}{thin:<1}{c['n']:>4}"


def run(markets: list[str], n_folds: int, min_n: int, summary_only: bool) -> int:
    best: list[tuple] = []
    for ds_label, table, maturity in _DATASETS:
        for market, groups in MARKETS:
            if markets and market not in markets:
                continue
            rows = _load(table, maturity, market)
            if not rows:
                continue
            gate = _live_gate(market)
            # ONE fold partition for the whole (dataset, market) — see
            # _fold_bounds. Every group and cell below is scored against these
            # same calendar windows, so robustness is comparable across them.
            bounds = _fold_bounds(rows, n_folds)
            print(f"\n{'='*118}")
            print(f"DATASET {ds_label}   ·   MARKET {market}   ·   "
                  f"n={len(rows)} settled pre-match   ·   walk-forward {n_folds} folds")
            if gate:
                ge, go = gate
                print(f"LIVE GATE today: edge >= "
                      f"{'RETIRED' if ge is None else f'{ge:.0%}'}  ·  odds >= {go:.2f}")
            print(f"cell = ROI% [✓=robust in every fold] [?=n<{min_n}, not adoptable] n")
            print('='*118)
            for glabel, pred in groups:
                grp = [r for r in rows if pred(r["sel"], r["odds"])]
                if not grp:
                    continue
                cells = {}
                for ef in EDGE_FLOORS:
                    for of in ODDS_FLOORS:
                        cells[(ef, of)] = _cell(grp, bounds, ef, of, n_folds)
                # record the best ADOPTABLE cell for the summary
                adoptable = [((ef, of), c) for (ef, of), c in cells.items()
                             if c["robust"] and c["n"] >= min_n]
                if adoptable:
                    (bef, bof), bc = max(adoptable, key=lambda kv: kv[1]["roi"])
                    best.append((ds_label, market, glabel, bef, bof,
                                 bc["roi"], bc["n"], len(adoptable)))
                else:
                    best.append((ds_label, market, glabel, None, None,
                                 None, len(grp), 0))
                if summary_only:
                    continue
                print(f"\n  {glabel}   (n={len(grp)})")
                print("    edge\\odds " + "".join(f"{o:>13.2f}" for o in ODDS_FLOORS))
                for ef in EDGE_FLOORS:
                    line = "".join(_fmt(cells[(ef, of)], min_n) for of in ODDS_FLOORS)
                    print(f"    >={ef*100:>4.0f}%   {line}")

    # ── summary ──────────────────────────────────────────────────────────────
    print(f"\n\n{'='*118}")
    print(f"SUMMARY — best ADOPTABLE cell per (dataset, market, selection)  "
          f"[robust in all {n_folds} folds AND n >= {min_n}]")
    print('='*118)
    print(f"{'dataset':21}{'market':15}{'selection':22}"
          f"{'edge':>7}{'odds':>7}{'ROI%':>8}{'n':>6}{'#robust':>9}")
    for ds, mkt, g, ef, of, roi, n, cnt in best:
        if ef is None:
            print(f"{ds:21}{mkt:15}{g:22}{'—':>7}{'—':>7}"
                  f"{'none':>8}{n:>6}{0:>9}   (no robust cell at n>={min_n})")
        else:
            print(f"{ds:21}{mkt:15}{g:22}{ef*100:>6.0f}%{of:>7.2f}"
                  f"{roi:>+8.1f}{n:>6}{cnt:>9}")
    print(f"\n#robust = how many of the {len(EDGE_FLOORS)*len(ODDS_FLOORS)} "
          f"(edge x odds) cells were robust AND above the min-n gate. A group "
          f"with many robust cells has a broad, stable frame; a group with one "
          f"or two has a fragile one that is likely selection noise.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--market", action="append", default=[],
                    help="restrict to a market (repeatable): 1x2, "
                         "over_under_25, over_under_35")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=50,
                    help="a cell is only ADOPTABLE at this many bets (default "
                         "50). Cells below it print with '?' and never win the "
                         "summary — without this the grid recommends 4-bet "
                         "cells at +300%%.")
    ap.add_argument("--summary-only", action="store_true",
                    help="skip the per-group matrices, print only the summary")
    a = ap.parse_args()
    return run(a.market, a.folds, a.min_n, a.summary_only)


if __name__ == "__main__":
    raise SystemExit(main())
