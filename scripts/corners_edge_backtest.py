#!/usr/bin/env python3
"""CORNERS-EDGE-TAIL — line-shop the corners market against de-vigged Pinnacle.

THE RULE
--------
For every (match, corners line, over/under) selection, take the best price at a
bookmaker we can actually place at, and compare it to Pinnacle's de-vigged
probability for the same selection. Bet when

    best_accessible_odds * devig(Pinnacle) - 1  >=  threshold

Settle on the real corner count from `match_stats`. No model is involved; this
is pure line shopping, which is the point — our binding constraint is repeatedly
measured as PRICE rather than model quality.

WHY THIS SCRIPT EXISTS RATHER THAN AN AD-HOC QUERY
--------------------------------------------------
The first pass at this (2026-09-05) reported +23.60% ROI on n=141 and contained
TWO bugs that had to be found and fixed before the number meant anything, both
of them this repo's signature errors:

  * `max(odds)` across all pre-kickoff snapshots — the STALE-BEST-ODDS
    high-water mark (gotcha 30). `odds_snapshots` is append-only, so a MAX is
    not a price anyone could have taken. Must be latest-per-book.
  * `split_part(market,'_',1) = 'corners'` also matches `corners_home_ou_*`,
    `corners_away_ou_*` and `corners_1h_ou_*`, so per-team and first-half
    markets were being settled against FULL-MATCH corner counts. Restricting to
    `^corners_ou_[0-9]+$` cut the sample from 5,862 sides to 2,652.

Both are handled here by construction, and the script is committed so the next
re-run cannot reintroduce them.

THE PLACEBO IS NOT THE OBVIOUS ONE
----------------------------------
The first pass shuffled Pinnacle's probabilities across selections and got
z=+11.47. That null is UNFAIR in this shape of problem, as NEW-MARKETS-LINESHOP
found the hard way: because `odds ~= 1/p`, breaking the coupling makes the rule
fire on far more selections than it really does, against a vig-shaped baseline —
any rule "wins" by a wide margin. The honest null permutes OUTCOMES within
(line, selection) strata, holding n and the odds distribution fixed. That change
took an unrelated finding from z=+21.72 to z=+0.90.

Both are reported here so the difference is visible rather than asserted.

USAGE
    python3 scripts/corners_edge_backtest.py                 # default 2% floor
    python3 scripts/corners_edge_backtest.py --sweep         # threshold ladder
    python3 scripts/corners_edge_backtest.py --perms 500
"""
from __future__ import annotations

import argparse
import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query
from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS

console = Console()

STAKE = 10.0
# Guard against a mislabelled or stale quote entering as a monster price. Same
# spirit as the 1.35x OU cap in the pipeline.
MAX_PLAUSIBLE_ODDS = 15.0


def load_rows(since: str) -> list[dict]:
    """One row per (match, line, selection) with the best ACCESSIBLE price, the
    best price at ANY book, Pinnacle's two-way prices, and the settled count.

    Every odds read is DISTINCT ON (…, bookmaker) ORDER BY timestamp DESC and
    bounded by `o.timestamp <= m.date`, so it is the latest PRE-KICKOFF price
    per book — never a high-water mark and never an in-play tick. `is_live` is
    deliberately not used as the bound: it only excludes the
    `api-football-live` pseudo-book, and real books keep posting after kickoff
    under their own name (AF-ISLIVE-CALLSITE-FIXES).
    """
    accessible = tuple(sorted(ACCESSIBLE_BOOKMAKERS))
    return execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
                   o.match_id, o.market, o.selection, o.bookmaker, o.odds::float AS odds
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.market ~ '^corners_ou_[0-9]+$'
               AND m.status = 'finished'
               AND m.date >= %s::date
               AND o.timestamp <= m.date
             ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC
        ),
        pin AS (
            SELECT match_id, market,
                   max(odds) FILTER (WHERE selection = 'over')  AS pin_over,
                   max(odds) FILTER (WHERE selection = 'under') AS pin_under
              FROM latest WHERE bookmaker = 'Pinnacle'
             GROUP BY 1, 2
        ),
        best AS (
            SELECT match_id, market, selection,
                   max(odds) FILTER (WHERE bookmaker = ANY(%s))            AS best_acc,
                   max(odds) FILTER (WHERE bookmaker <> 'Pinnacle')        AS best_any,
                   (array_agg(bookmaker ORDER BY odds DESC)
                      FILTER (WHERE bookmaker = ANY(%s)))[1]               AS acc_book
              FROM latest
             GROUP BY 1, 2, 3
        )
        SELECT b.match_id, b.market, b.selection,
               b.best_acc, b.best_any, b.acc_book,
               p.pin_over, p.pin_under,
               (ms.corners_home + ms.corners_away) AS corners_total,
               m.date
          FROM best b
          JOIN pin p        ON p.match_id = b.match_id AND p.market = b.market
          JOIN match_stats ms ON ms.match_id = b.match_id
          JOIN matches m    ON m.id = b.match_id
         WHERE p.pin_over IS NOT NULL AND p.pin_under IS NOT NULL
           AND ms.corners_home IS NOT NULL AND ms.corners_away IS NOT NULL
        """,
        (since, list(accessible), list(accessible)),
    )


def devig_two_way(o_over: float, o_under: float) -> tuple[float, float]:
    """Proportional two-way de-vig.

    Proportional is defensible HERE where Shin is not obviously better: a
    corners total is a symmetric two-way market with no favourite-longshot
    structure of the kind that made proportional manufacture edge on draws and
    away dogs in 1X2 (see workers/model/devig.py).
    """
    ip_o, ip_u = 1.0 / o_over, 1.0 / o_under
    tot = ip_o + ip_u
    return ip_o / tot, ip_u / tot


def decode_line(market: str) -> float | None:
    """`corners_ou_105` -> 10.5. Corners lines are 0.5-stepped and live roughly
    in 4..20, which resolves the `"105"` = 10.5 vs 1.05 ambiguity for this one
    family (MARKET-LINE-ENCODING-LOSSY — newer rows carry `handicap_line`, older
    ones do not, so decode defensively rather than trusting the name).
    """
    tok = market.rsplit("_", 1)[-1]
    if not tok.isdigit():
        return None
    for div in (10.0, 100.0):
        v = int(tok) / div
        if 2.0 <= v <= 25.0 and abs(v * 2 - round(v * 2)) < 1e-9:
            return v
    return None


def build_picks(rows: list[dict], threshold: float, *, accessible_only: bool):
    """Selections whose best price beats de-vigged Pinnacle by >= threshold."""
    picks = []
    for r in rows:
        line = decode_line(r["market"])
        if line is None:
            continue
        total = r["corners_total"]
        if total is None or total == line:      # exact push is impossible on .5
            continue
        price = r["best_acc"] if accessible_only else r["best_any"]
        if not price or price <= 1.0 or price > MAX_PLAUSIBLE_ODDS:
            continue
        p_over, p_under = devig_two_way(float(r["pin_over"]), float(r["pin_under"]))
        p = p_over if r["selection"] == "over" else p_under
        edge = price * p - 1.0
        if edge < threshold:
            continue
        won = (total > line) if r["selection"] == "over" else (total < line)
        picks.append({
            "match_id": r["match_id"], "market": r["market"], "line": line,
            "selection": r["selection"], "odds": price, "p": p, "edge": edge,
            "won": won, "book": r["acc_book"], "date": r["date"],
        })
    return picks


def roi_of(picks) -> tuple[int, float, float]:
    if not picks:
        return 0, 0.0, 0.0
    rets = [(p["odds"] - 1.0) if p["won"] else -1.0 for p in picks]
    n = len(rets)
    mean = st.mean(rets)
    se = (st.stdev(rets) / math.sqrt(n)) if n > 1 else 0.0
    return n, 100 * mean, 100 * se


def placebo_outcome_permutation(picks, rows, threshold, *, accessible_only, draws, seed=7):
    """Honest null: keep the SELECTED picks and their odds exactly as they are,
    and permute the win/loss outcomes within (line, selection) strata.

    This holds n, the odds distribution and the stratum mix fixed, and asks only
    whether the association between our selections and their outcomes is real.
    Contrast `placebo_prob_shuffle`, which does not.
    """
    # The stratum pool holds (odds, outcome) PAIRS, and the placebo draws a
    # whole pair — not our pick's odds with someone else's outcome.
    #
    # That distinction is the whole test. An earlier version of this function
    # kept each pick's own price (the BEST accessible one, high by
    # construction) and paired it with a random outcome from the stratum. That
    # null returns best-price x base-rate, which is favourable for free: it
    # scored +14.54% against a "bet everything" baseline of -4.42%, i.e. the
    # null itself was quietly assuming half the thing being tested.
    #
    # Drawing pairs asks the question we actually care about: within the same
    # (line, selection) strata and the same n, does choosing selections BY EDGE
    # beat choosing them at random?
    rng = random.Random(seed)
    strata = defaultdict(list)
    for r in rows:
        line = decode_line(r["market"])
        total = r["corners_total"]
        if line is None or total is None or total == line:
            continue
        price = r["best_acc"] if accessible_only else r["best_any"]
        if not price or price <= 1.0 or price > MAX_PLAUSIBLE_ODDS:
            continue
        won = (total > line) if r["selection"] == "over" else (total < line)
        strata[(line, r["selection"])].append((float(price), won))
    out = []
    for _ in range(draws):
        rets = []
        for p in picks:
            pool = strata.get((p["line"], p["selection"]))
            if not pool:
                continue
            odds, w = rng.choice(pool)
            rets.append((odds - 1.0) if w else -1.0)
        out.append(100 * st.mean(rets) if rets else 0.0)
    return out


def placebo_prob_shuffle(rows, threshold, *, accessible_only, draws, seed=7):
    """The FIRST pass's null, reproduced only so the difference is visible.

    Shuffling Pinnacle's probabilities across selections breaks the odds/prob
    coupling, so the rule fires on a far larger and differently-shaped set than
    it really does. Reported, not trusted.
    """
    rng = random.Random(seed)
    probs = []
    for r in rows:
        p_over, p_under = devig_two_way(float(r["pin_over"]), float(r["pin_under"]))
        probs.append(p_over if r["selection"] == "over" else p_under)
    out = []
    for _ in range(draws):
        shuffled = probs[:]
        rng.shuffle(shuffled)
        rets = []
        for r, p in zip(rows, shuffled):
            line = decode_line(r["market"])
            total = r["corners_total"]
            if line is None or total is None or total == line:
                continue
            price = r["best_acc"] if accessible_only else r["best_any"]
            if not price or price <= 1.0 or price > MAX_PLAUSIBLE_ODDS:
                continue
            if price * p - 1.0 < threshold:
                continue
            won = (total > line) if r["selection"] == "over" else (total < line)
            rets.append((price - 1.0) if won else -1.0)
        out.append(100 * st.mean(rets) if rets else 0.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-29")
    ap.add_argument("--threshold", type=float, default=0.02)
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    rows = load_rows(args.since)
    console.print(f"[cyan]{len(rows):,} (match, line, selection) rows with a Pinnacle "
                  f"two-way and a settled corner count, since {args.since}[/cyan]")
    if not rows:
        console.print("[red]nothing to test[/red]")
        return 1

    days = {r["date"].date() for r in rows if r.get("date")}
    console.print(f"[dim]window: {min(days)} .. {max(days)} ({len(days)} distinct days)[/dim]\n")

    if args.sweep:
        t = Table(title="Threshold ladder")
        for c in ("basis", "threshold", "n", "ROI %", "se ±pp", "t"):
            t.add_column(c, justify="right")
        for acc in (True, False):
            for th in (0.00, 0.02, 0.03, 0.05, 0.08):
                p = build_picks(rows, th, accessible_only=acc)
                n, roi, se = roi_of(p)
                tstat = roi / se if se else 0.0
                t.add_row("accessible" if acc else "any book", f"{th:.0%}",
                          f"{n:,}", f"{roi:+.2f}", f"{se:.2f}", f"{tstat:+.2f}")
        console.print(t)
        return 0

    for acc in (True, False):
        label = "ACCESSIBLE books only (placeable)" if acc else "ANY book (incl. unplaceable)"
        picks = build_picks(rows, args.threshold, accessible_only=acc)
        n, roi, se = roi_of(picks)
        console.print(f"[bold]{label}[/bold]  threshold {args.threshold:.0%}")
        if n == 0:
            console.print("   [yellow]no qualifying picks[/yellow]\n")
            continue
        tstat = roi / se if se else 0.0
        console.print(f"   n={n}  ROI={roi:+.2f}%  se={se:.2f}pp  t={tstat:+.2f}")
        console.print(f"   95% CI [{roi - 1.96 * se:+.2f}%, {roi + 1.96 * se:+.2f}%]")
        allp = build_picks(rows, -9.0, accessible_only=acc)
        na, ra, _ = roi_of(allp)
        console.print(f"   [dim]bet EVERY side for contrast: n={na}, ROI={ra:+.2f}% "
                      f"(should be ~-vig)[/dim]")
        if picks:
            books = defaultdict(int)
            for p in picks:
                books[p["book"] or "?"] += 1
            console.print(f"   [dim]books: {dict(sorted(books.items(), key=lambda kv: -kv[1]))}[/dim]")

        honest = placebo_outcome_permutation(picks, rows, args.threshold,
                                             accessible_only=acc, draws=args.perms)
        if honest:
            m, s = st.mean(honest), (st.stdev(honest) if len(honest) > 1 else 0.0)
            z = (roi - m) / s if s else float("nan")
            console.print(f"   [bold]placebo (outcome permutation — the honest one):[/bold] "
                          f"mean {m:+.2f}%, sd {s:.2f} -> z = {z:+.2f}")
        naive = placebo_prob_shuffle(rows, args.threshold, accessible_only=acc,
                                     draws=min(args.perms, 100))
        if naive:
            m2, s2 = st.mean(naive), (st.stdev(naive) if len(naive) > 1 else 0.0)
            z2 = (roi - m2) / s2 if s2 else float("nan")
            console.print(f"   [dim]placebo (probability shuffle — the first pass's, unfair): "
                          f"mean {m2:+.2f}%, sd {s2:.2f} -> z = {z2:+.2f}[/dim]")
        console.print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
