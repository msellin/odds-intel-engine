"""CSV-FULL-EXTRACT backtest — quantify whether the newly-ingested fields
(Betfair Exchange closing, AH historicals, opening odds) actually move the
model needle.

Three independent measurements:

  A. Anchor swap — compare Brier + LogLoss for shrinkage-as-implied-prob,
     Pinnacle vs Betfair Exchange, on the same matched-pair holdout.

  B. AH market sanity — flat-stake ROI on Pinnacle closing AH home/away,
     plus edge-threshold sweep. Establishes whether an AH bot is feasible.

  C. Opening→closing drift — does Pinnacle (close - open) drift correlate
     with match outcome? Equivalent of a single-feature univariate AUC.

Run:
  python3 scripts/backtest_csv_full_extract.py
  python3 scripts/backtest_csv_full_extract.py --since 2024-01-01

Outputs results to dev/active/csv-full-extract-backtest-results.md.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────

def implied_from_decimal(odds: float) -> float:
    return 1.0 / odds if odds and odds > 1.0 else 0.0


def devig_three_way(h: float, d: float, a: float) -> tuple[float, float, float]:
    ph, pd, pa = implied_from_decimal(h), implied_from_decimal(d), implied_from_decimal(a)
    s = ph + pd + pa
    if s <= 0:
        return (0.0, 0.0, 0.0)
    return (ph / s, pd / s, pa / s)


def brier_three_way(pred: tuple[float, float, float], outcome: str) -> float:
    """Brier score for a 3-way prediction. outcome ∈ {H,D,A}."""
    actual = {"H": (1, 0, 0), "D": (0, 1, 0), "A": (0, 0, 1)}[outcome]
    return sum((p - a) ** 2 for p, a in zip(pred, actual))


def logloss_three_way(pred: tuple[float, float, float], outcome: str, eps: float = 1e-12) -> float:
    idx = {"H": 0, "D": 1, "A": 2}[outcome]
    p = max(eps, min(1 - eps, pred[idx]))
    return -math.log(p)


# ── A. Anchor swap ─────────────────────────────────────────────────────────

def _load_close_1x2(bookmakers: list[str], since: str) -> dict[str, dict[str, dict[str, float]]]:
    """Return {match_id: {bookmaker: {selection: odds}}} for closing 1X2 rows
    on matches with a settled score since the cutoff date."""
    rows = execute_query(
        """
        SELECT os.match_id::text AS mid, os.bookmaker, os.selection, os.odds
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = '1x2'
          AND os.is_closing = true
          AND os.bookmaker = ANY(%s::text[])
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s
        """,
        [bookmakers, since],
    )
    out: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        out.setdefault(r["mid"], {}).setdefault(r["bookmaker"], {})[r["selection"]] = float(r["odds"])
    return out


def _load_outcomes(match_ids: list[str]) -> dict[str, str]:
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT id::text AS mid, score_home, score_away
        FROM matches WHERE id = ANY(%s::uuid[])
          AND score_home IS NOT NULL AND score_away IS NOT NULL
        """,
        [match_ids],
    )
    out: dict[str, str] = {}
    for r in rows:
        if r["score_home"] > r["score_away"]: out[r["mid"]] = "H"
        elif r["score_home"] < r["score_away"]: out[r["mid"]] = "A"
        else: out[r["mid"]] = "D"
    return out


def anchor_swap_test(since: str) -> dict:
    """Compare Pinnacle-closing vs Betfair-Exchange-closing devig'd implied
    probabilities as predictors of outcomes. Same match-set, paired."""
    books = ["Pinnacle", "Betfair Exchange"]
    snaps = _load_close_1x2(books, since)
    paired = {
        mid: bm for mid, bm in snaps.items()
        if all(b in bm and len(bm[b]) == 3 for b in books)
    }
    outcomes = _load_outcomes(list(paired))
    paired = {mid: bm for mid, bm in paired.items() if mid in outcomes}
    if not paired:
        return {"n": 0, "note": "no paired matches with both Pinnacle and Exchange closing"}

    metrics = {b: {"brier": 0.0, "logloss": 0.0, "n": 0} for b in books}
    for mid, bm in paired.items():
        for b in books:
            o = bm[b]
            pred = devig_three_way(o["home"], o["draw"], o["away"])
            metrics[b]["brier"]   += brier_three_way(pred, outcomes[mid])
            metrics[b]["logloss"] += logloss_three_way(pred, outcomes[mid])
            metrics[b]["n"]       += 1
    for b in books:
        n = metrics[b]["n"]
        metrics[b]["brier_mean"]   = metrics[b]["brier"] / n if n else None
        metrics[b]["logloss_mean"] = metrics[b]["logloss"] / n if n else None
    return {"n": len(paired), "metrics": metrics}


# ── B. AH market sanity ────────────────────────────────────────────────────

def ah_sanity_test(since: str, bookmaker: str = "Pinnacle") -> dict:
    """Flat-stake outcome simulation on Pinnacle closing AH home/away.

    AH settlement rule (quarter-line aware):
      Margin = home_score - away_score + handicap_line.
      If margin > 0.25 → home wins full; 0 ≥ margin ≥ -0.25 → push; etc.
    This script only ingests main lines (integer/half from CSVs), so quarter
    half-wins don't apply — handicap_line is always int or half.
    """
    rows = execute_query(
        """
        SELECT os.match_id::text AS mid, os.selection, os.odds, os.handicap_line,
               m.score_home, m.score_away
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = 'asian_handicap'
          AND os.is_closing = true
          AND os.bookmaker = %s
          AND os.handicap_line IS NOT NULL
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s
        """,
        [bookmaker, since],
    )
    by_match: dict[str, dict] = {}
    for r in rows:
        by_match.setdefault(r["mid"], {})[r["selection"]] = r
    paired = [v for v in by_match.values() if "home" in v and "away" in v]

    home_pnl = 0.0; away_pnl = 0.0
    n = 0
    for p in paired:
        hl = float(p["home"]["handicap_line"])
        margin = (p["home"]["score_home"] - p["home"]["score_away"]) + hl
        odds_h = float(p["home"]["odds"])
        odds_a = float(p["away"]["odds"])
        # Half lines only — simple win/loss/push at integer margins.
        if margin > 0.001:        # home covers
            home_pnl += odds_h - 1; away_pnl -= 1
        elif margin < -0.001:     # away covers
            home_pnl -= 1; away_pnl += odds_a - 1
        else:                     # push
            pass
        n += 1
    roi_home = home_pnl / n if n else None
    roi_away = away_pnl / n if n else None

    # Edge-threshold sweep using devig'd implied vs the offered odds. With
    # only Pinnacle data this is mostly a sanity diagnostic — the implied is
    # the same data we'd be betting against. Reported for shape only.
    return {
        "n_matches_with_ah_pair": len(paired),
        "n_bets_each_side": n,
        "flat_roi_home": roi_home,
        "flat_roi_away": roi_away,
    }


# ── C. Opening→closing drift ───────────────────────────────────────────────

def drift_signal_test(since: str, bookmaker: str = "Pinnacle") -> dict:
    """Does Pinnacle closing − opening drift on the home side correlate with
    actual home win rate? Quintile binning + per-bin home win rate.
    """
    rows = execute_query(
        """
        SELECT os.match_id::text AS mid, os.selection, os.odds, os.is_opening,
               m.score_home, m.score_away
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = '1x2'
          AND os.bookmaker = %s
          AND (os.is_closing = true OR os.is_opening = true)
          AND m.status = 'finished'
          AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
          AND m.date >= %s
        """,
        [bookmaker, since],
    )
    cells: dict[str, dict] = {}
    for r in rows:
        sel = r["selection"]
        key = ("open" if r["is_opening"] else "close")
        cells.setdefault(r["mid"], {})[(sel, key)] = float(r["odds"])
        cells[r["mid"]]["__hs"] = r["score_home"]
        cells[r["mid"]]["__as"] = r["score_away"]

    drift_outcomes: list[tuple[float, int]] = []
    for mid, c in cells.items():
        if ("home", "open") not in c or ("home", "close") not in c:
            continue
        opn, cls = c[("home", "open")], c[("home", "close")]
        if opn <= 1.01 or cls <= 1.01:
            continue
        # Drift on implied prob: positive drift → market moved TOWARD home win
        implied_open = 1 / opn
        implied_close = 1 / cls
        drift = implied_close - implied_open  # > 0 = sharper money on home
        home_won = 1 if c["__hs"] > c["__as"] else 0
        drift_outcomes.append((drift, home_won))

    if not drift_outcomes:
        return {"n": 0, "note": "no paired open+close rows"}

    drift_outcomes.sort(key=lambda x: x[0])
    n = len(drift_outcomes)
    q = max(1, n // 5)
    bins = []
    for i in range(5):
        lo = i * q
        hi = (i + 1) * q if i < 4 else n
        slice_ = drift_outcomes[lo:hi]
        wr = sum(o for _, o in slice_) / len(slice_) if slice_ else None
        mean_drift = sum(d for d, _ in slice_) / len(slice_) if slice_ else None
        bins.append({"bin": i + 1, "n": len(slice_), "mean_drift": mean_drift, "home_win_rate": wr})
    # Top-bin minus bottom-bin home WR — clean univariate signal
    return {"n": n, "bins": bins,
            "spread_top_minus_bottom": (bins[-1]["home_win_rate"] - bins[0]["home_win_rate"])
                                       if bins[0]["home_win_rate"] is not None and bins[-1]["home_win_rate"] is not None else None}


# ── Driver ─────────────────────────────────────────────────────────────────

def fmt(v, digits=4):
    if v is None: return "—"
    if isinstance(v, float): return f"{v:.{digits}f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01-01", help="Lower bound for match.date")
    ap.add_argument("--out", default="dev/active/csv-full-extract-backtest-results.md")
    args = ap.parse_args()

    print(f"CSV-FULL-EXTRACT backtest — since={args.since}\n")

    print("[A] Anchor swap — Pinnacle vs Betfair Exchange 1X2 closing")
    A = anchor_swap_test(args.since)
    print(f"    Paired matches: {A.get('n', 0):,}")
    for b, m in (A.get("metrics") or {}).items():
        print(f"    {b:18s} brier={fmt(m.get('brier_mean'))}  "
              f"logloss={fmt(m.get('logloss_mean'))}  n={m.get('n'):,}")

    print("\n[B] AH sanity — Pinnacle closing")
    B = ah_sanity_test(args.since)
    print(f"    Matches with AH pair: {B['n_matches_with_ah_pair']:,}  "
          f"flat ROI home: {fmt(B['flat_roi_home'])}  flat ROI away: {fmt(B['flat_roi_away'])}")

    print("\n[C] Opening→closing drift — Pinnacle home")
    C = drift_signal_test(args.since)
    if C.get("n"):
        print(f"    Paired open+close matches: {C['n']:,}")
        for b in C["bins"]:
            print(f"    bin {b['bin']}  n={b['n']:>5}  mean_drift={fmt(b['mean_drift'])}  "
                  f"home_WR={fmt(b['home_win_rate'])}")
        print(f"    Top-minus-bottom WR spread: {fmt(C['spread_top_minus_bottom'])}")
    else:
        print(f"    {C.get('note')}")

    # Markdown out
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write(f"# CSV-FULL-EXTRACT — Backtest Results\n\n")
        f.write(f"Generated from `scripts/backtest_csv_full_extract.py`, since={args.since}.\n\n")
        f.write("## A. Anchor swap (Pinnacle vs Betfair Exchange, 1X2 closing devig'd implied)\n\n")
        f.write(f"Paired matches: {A.get('n', 0):,}\n\n")
        f.write("| Anchor | Brier (mean) | LogLoss (mean) | N |\n|---|---|---|---|\n")
        for b, m in (A.get("metrics") or {}).items():
            f.write(f"| {b} | {fmt(m.get('brier_mean'))} | {fmt(m.get('logloss_mean'))} | {m.get('n'):,} |\n")
        f.write("\n## B. AH market sanity (Pinnacle closing AH)\n\n")
        f.write(f"- Matches with AH pair: {B['n_matches_with_ah_pair']:,}\n")
        f.write(f"- Flat-stake ROI, home: {fmt(B['flat_roi_home'])}\n")
        f.write(f"- Flat-stake ROI, away: {fmt(B['flat_roi_away'])}\n")
        f.write("\n## C. Opening→closing drift (Pinnacle home implied prob)\n\n")
        if C.get("n"):
            f.write(f"Paired open+close matches: {C['n']:,}\n\n")
            f.write("| Bin | N | Mean drift | Home WR |\n|---|---|---|---|\n")
            for b in C["bins"]:
                f.write(f"| {b['bin']} | {b['n']:,} | {fmt(b['mean_drift'])} | {fmt(b['home_win_rate'])} |\n")
            f.write(f"\nTop-minus-bottom WR spread: **{fmt(C['spread_top_minus_bottom'])}**\n")
        else:
            f.write(f"_{C.get('note')}_\n")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
