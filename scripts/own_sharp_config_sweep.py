#!/usr/bin/env python3
"""OWN-path SHARP-EDGE configuration sweep — exhaustive backtest with a junk-anchor control.

THE QUESTION (2026-09-14). For the 🤖 OWN betting path we place ourselves at
EMTA-legal books (Coolbet, Epicbet, Unibet-Site — the three we SELF-SCRAPE, so
the price is one we can verify), is there ANY configuration of the sharp rule

    edge = P_shin(Shin-de-vigged Pinnacle) x book_price - 1

with enough volume AND a confidence interval that excludes zero? Or is the whole
family what the time-aligned publish rule already looks like — a positive point
estimate whose CI spans zero at every n we can reach?

WHAT THIS SCRIPT IS CAREFUL ABOUT, and why each guard exists
------------------------------------------------------------
1. TIME ALIGNMENT (docs/SYSTEM_MAP.md §1, OWN_PATH_VERDICT). Taking each book's
   latest pre-kickoff quote independently of the anchor harvests SOFT-BOOK
   STALENESS, not mispricing: the identical publish rule reads +8.47% unaligned
   and +5.54% time-aligned, and the median anchor-to-bet gap on the legs the
   rule selected was 360 MINUTES. Every leg here carries its anchor gap, every
   cell is filtered on it, and every published cell reports the median gap.

2. NEVER JOIN BOOKS ON EXACT TIMESTAMP (ANALYSIS_GOTCHAS §63). `odds_snapshots`
   stamps each ROW; Coolbet's 1x2 triple lands across ~100 ms, so equality
   grouping finds a complete triple in 0.1% of Coolbet groups against 99.9-100%
   for the others. Each book's market is assembled from a +/-2 min window FIRST
   (`assemble`, the same algorithm as
   `scripts/own_path_kill_criterion.py::assemble`, generalised over the side
   list and pinned identical by smoke test OWN-SHARP-SWEEP-ASSEMBLE), and only
   then aligned across books.

3. PHANTOM FEEDS (PLAN_AFTER_AUDITS §5). Only the three self-scraped books are
   bettable. AF's 'Unibet' is phantom-high on 33.1% of selections and stopped
   writing 2026-09-12; 'Unibet-Kambi' 38%; 'Max'/'Avg'/'Betfair Exchange'/
   'BetWin'/'Betfred' are football-data.co.uk CSV imports and synthetic
   aggregates. None of them can appear here — the book list is a whitelist.

4. ODDS OUTLIERS (ANALYSIS_GOTCHAS §9). A mislabelled line produces an enormous
   fake edge. The production guard is applied leg by leg: a book price above
   Pinnacle x 1.35 (1x2) / x 1.30 (O/U) on the SAME aligned anchor is dropped.

5. MULTIPLE COMPARISONS (ANALYSIS_GOTCHAS §52, §60). This is a grid search, i.e.
   a machine for manufacturing significance. So: the number of cells tested is
   printed; finalists are chosen on a time-ordered IN-SAMPLE period and reported
   on an untouched OUT-OF-SAMPLE one; every finalist reports all three
   time-ordered folds; and every cell reports the n required to detect its own
   point estimate at 80% power.

6. CLUSTERED STANDARD ERRORS. Legs on one fixture are not independent (home,
   draw and away are mutually exclusive; the same selection at three books is
   nearly the same bet). All CIs cluster on `match_id`.

7. THE NEGATIVE CONTROL. `--control` reruns the identical harness with a JUNK
   ANCHOR: each leg keeps its own book price, outcome and timing, but its
   de-vigged anchor probabilities are taken from a DIFFERENT fixture's Pinnacle
   triple (seeded permutation within the same market). A junk anchor should lose
   roughly the vig. If it does not, the harness is broken and no other number in
   this script means anything.

WHAT THIS SCRIPT CANNOT DO, and you must not read past it
---------------------------------------------------------
`prune_old_simple` keeps only `is_opening`, `is_closing` and the LATEST
pre-kickoff row per series after 7 days (ANALYSIS_GOTCHAS §59), and our three
books had almost no `is_closing` anchors before 2026-09-11. So beyond the
7-day window each book has exactly ONE surviving quote per series. Consequences,
both reported by `--diagnostics`:
  * the LEAD-TIME dimension is only honestly sweepable inside the last ~7 days;
    outside it, requiring a quote >= L minutes before kickoff selects on which
    era a fixture is from, not on when we would have bet.
  * own-book CLV is ~0 by construction on `--lead 0` legs, because the leg IS
    the last surviving pre-kickoff row. It is computed only where a strictly
    later own-book pre-kickoff quote exists.

Read-only. Touches no bot, no config, no table.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras

from workers.api_clients.db import get_conn
from workers.model.devig import devig

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

# EMTA-legal AND self-scraped. This is a whitelist on purpose — see guard 3.
BOOKS = ["Coolbet", "Epicbet", "Unibet-Site"]
ANCHOR_BOOK = "Pinnacle"
POOLED = "POOLED"           # best aligned price across the three books

SIDES: dict[str, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "over_under_15": ("over", "under"),
    "over_under_25": ("over", "under"),
    "over_under_35": ("over", "under"),
}

# ODDS-OUTLIER-FILTER-2026-08-18, same multipliers the pipeline uses
# (daily_pipeline_v2._PIN_1X2_OUTLIER_MULT / _PIN_OU_OUTLIER_MULT).
OUTLIER_MULT = {"1x2": 1.35, "over_under_15": 1.30,
                "over_under_25": 1.30, "over_under_35": 1.30}

ASSEMBLE_WINDOW_MIN = 2.0   # a book's own market may straddle this

# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------
EDGE_FLOORS = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10]
ODDS_BANDS = [(1.01, 2.50), (1.01, 4.00), (1.01, 8.00), (1.01, 1000.0),
              (1.50, 2.50), (1.50, 4.00), (1.50, 8.00), (1.50, 1000.0),
              (2.00, 2.50), (2.00, 4.00), (2.00, 8.00), (2.00, 1000.0),
              (2.80, 4.00), (2.80, 8.00), (2.80, 1000.0)]
LEADS = [0, 60, 240]        # minutes before kickoff the bet quote must precede
MIN_N = 100                 # refuse to draw a conclusion below this (trap 8)


# ---------------------------------------------------------------------------
# Assembly — the §63 guard
# ---------------------------------------------------------------------------
def assemble(obs, sides, window: float = ASSEMBLE_WINDOW_MIN):
    """Return [(anchor_ts, {sel: odds})] — a book's complete markets, allowing
    the rows to straddle `window` minutes.

    Identical algorithm to `own_path_kill_criterion.assemble`, generalised over
    `sides` so it also serves the 2-way O/U markets. Coolbet needs ~100 ms of
    tolerance; the window is generous so this is not tuned to one book.
    """
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked: dict[str, float] = {}
        for t, sel, o in obs[i:]:
            if (t - t0) / 60.0 > window:
                break
            picked.setdefault(sel, o)
        if all(s in picked for s in sides):
            out.append((t0, {s: picked[s] for s in sides}))
    return out


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load(days: int, markets: list[str]):
    """Return (matches, odds) with odds[mid][book][market] = [(ts_epoch, sel, odds)].

    Two queries so the Pinnacle pull is restricted to fixtures at least one
    bettable book quoted — otherwise the anchor book alone is ~70% of the rows.
    """
    matches: dict[str, tuple[float, int, int]] = {}
    odds: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, extract(epoch from m.date) AS ko,
                       m.score_home, m.score_away
                  FROM matches m
                 WHERE m.status = 'finished'
                   AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
                   AND m.date > now() - (%s || ' days')::interval
                   AND m.date < now()
                """,
                (str(days),),
            )
            for r in cur.fetchall():
                matches[r["id"]] = (float(r["ko"]), int(r["score_home"]),
                                    int(r["score_away"]))

        # `o.timestamp <= m.date` is the authoritative pre-kickoff predicate
        # (ANALYSIS_GOTCHAS §37 — `is_live = false` is NOT one, it only excludes
        # the api-football-live pseudo-book, and 26% of is_live=false rows are
        # post-kickoff).
        sql = """
            SELECT o.match_id, o.bookmaker, o.market, o.selection,
                   o.odds::float AS odds, extract(epoch from o.timestamp) AS ts
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.bookmaker = ANY(%s)
               AND o.market = ANY(%s)
               AND o.timestamp <= m.date
               AND m.status = 'finished' AND m.score_home IS NOT NULL
               AND m.date > now() - (%s || ' days')::interval
               AND m.date < now()
               {extra}
        """
        with conn.cursor(name="soft_cur") as cur:
            cur.itersize = 50_000
            cur.execute(sql.format(extra=""), (BOOKS, markets, str(days)))
            for mid, book, market, sel, o, ts in cur:
                if mid in matches:
                    odds[mid][book][market].append((float(ts), sel, o))

        covered = list(odds.keys())
        with conn.cursor(name="pin_cur") as cur:
            cur.itersize = 50_000
            cur.execute(sql.format(extra="AND o.match_id = ANY(%s::uuid[])"),
                        ([ANCHOR_BOOK], markets, str(days), covered))
            for mid, book, market, sel, o, ts in cur:
                odds[mid][book][market].append((float(ts), sel, o))

    return matches, odds


# ---------------------------------------------------------------------------
# Leg construction
# ---------------------------------------------------------------------------
def won(market: str, sel: str, sh: int, sa: int) -> bool | None:
    if market == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sh < sa}[sel]
    if market.startswith("over_under_"):
        line = float(market.rsplit("_", 1)[1]) / 10.0   # '25' -> 2.5
        tot = sh + sa
        if tot == line:          # .5 lines never push; guard anyway
            return None
        return (tot > line) if sel == "over" else (tot < line)
    return None


def build_legs(matches, odds, markets, align_min: float, control_seed: int | None):
    """One leg per (fixture, book, market, selection, lead).

    The book quote is the LATEST assembled quote at least `lead` minutes before
    kickoff; the anchor is the Pinnacle assembled quote NEAREST IN TIME to it.
    Nothing about the selection depends on the edge, so no cell is cherry-picked.
    """
    # --- anchor pool, for the junk-anchor control -------------------------
    anchor_pool: dict[str, list[tuple[str, float, dict]]] = defaultdict(list)
    assembled_pin: dict = defaultdict(dict)
    for mid, bybook in odds.items():
        for market in markets:
            rows = bybook.get(ANCHOR_BOOK, {}).get(market)
            if not rows:
                continue
            tri = assemble(rows, SIDES[market])
            if tri:
                assembled_pin[mid][market] = tri
                anchor_pool[market].append((mid, tri[-1][0], tri[-1][1]))

    junk: dict[tuple[str, str], dict] = {}
    if control_seed is not None:
        rng = random.Random(control_seed)
        for market, pool in anchor_pool.items():
            if len(pool) < 2:
                continue
            src = list(pool)
            rng.shuffle(src)
            for i, (mid, _, _) in enumerate(pool):
                # walk until we land on a DIFFERENT fixture
                j = i
                while src[j % len(src)][0] == mid:
                    j += 1
                junk[(mid, market)] = src[j % len(src)][2]

    legs = []
    for mid, (ko, sh, sa) in matches.items():
        bybook = odds.get(mid)
        if not bybook:
            continue
        for market in markets:
            pin_tri = assembled_pin.get(mid, {}).get(market)
            if not pin_tri:
                continue
            sides = SIDES[market]
            mult = OUTLIER_MULT[market]
            for book in BOOKS:
                rows = bybook.get(book, {}).get(market)
                if not rows:
                    continue
                bq = assemble(rows, sides)
                if not bq:
                    continue
                # the book's own last surviving pre-kickoff market — the CLOSE
                # we score own-book CLV against (ANALYSIS_GOTCHAS §63: own-book
                # close, not "whichever AF book sorted last"). On a lead-0 leg
                # this IS the leg, so its CLV is 0 by construction and is
                # reported as undefined rather than as a result.
                t_c, q_c = bq[-1]
                close_margin = sum(1.0 / q_c[s] for s in sides) - 1.0
                for lead in LEADS:
                    cand = [(t, q) for t, q in bq if (ko - t) / 60.0 >= lead]
                    if not cand:
                        continue
                    t_b, q_b = max(cand, key=lambda x: x[0])
                    t_p, q_p = min(pin_tri, key=lambda x: abs(x[0] - t_b))
                    gap = abs(t_p - t_b) / 60.0
                    if gap > align_min:
                        continue
                    anchor_q = (junk.get((mid, market), q_p)
                                if control_seed is not None else q_p)
                    probs = devig([anchor_q[s] for s in sides])
                    if not probs:
                        continue
                    for i, s in enumerate(sides):
                        o = q_b[s]
                        if o is None or o <= 1.0:
                            continue
                        # outlier guard on the REAL anchor always — a junk
                        # anchor must not be allowed to wave outliers through,
                        # or the control tests a different sample.
                        if o > q_p[s] * mult:
                            continue
                        w = won(market, s, sh, sa)
                        if w is None:
                            continue
                        has_close = t_c > t_b
                        clv = (o / q_c[s] - 1.0) if has_close else None
                        legs.append({
                            "mid": mid, "book": book, "market": market,
                            "sel": s, "lead": lead,
                            "odds": o, "edge": probs[i] * o - 1.0,
                            "ret": (o - 1.0) if w else -1.0,
                            "ko": ko, "gap": gap,
                            "lead_actual": (ko - t_b) / 60.0,
                            # raw price ratio, NO de-vig — break-even is the
                            # closing book's own margin, not zero (trap 3)
                            "clv": clv,
                            "clv_ev": ((1.0 + clv) / (1.0 + close_margin) - 1.0)
                                      if has_close else None,
                            "close_margin": close_margin,
                        })

    # --- pooled: best aligned price across the three books ----------------
    best: dict[tuple, dict] = {}
    for lg in legs:
        k = (lg["mid"], lg["market"], lg["sel"], lg["lead"])
        cur = best.get(k)
        if cur is None or lg["odds"] > cur["odds"]:
            best[k] = lg
    for lg in best.values():
        p = dict(lg)
        p["book"] = POOLED
        legs.append(p)

    # --- per-BOOK time-ordered era and fold labels ------------------------
    # A single global cut would put almost all of Epicbet (first row
    # 2026-08-27) and all of Unibet-Site (2026-09-09) on one side of it, so
    # every book gets its own 70/30 cut and its own terciles. The split is on
    # KICKOFF, never on anything the strategy can see.
    by_book: dict[str, list[float]] = defaultdict(list)
    for lg in legs:
        by_book[lg["book"]].append(lg["ko"])
    bounds = {}
    for b, kos in by_book.items():
        kos.sort()
        bounds[b] = (kos[int(len(kos) * 0.70)],
                     kos[len(kos) // 3], kos[2 * len(kos) // 3],
                     max((kos[-1] - kos[0]) / 86400.0, 1.0))
    for lg in legs:
        cut, f1, f2, span = bounds[lg["book"]]
        lg["era"] = "IS" if lg["ko"] <= cut else "OOS"
        lg["fold"] = 0 if lg["ko"] <= f1 else (1 if lg["ko"] <= f2 else 2)
        lg["book_span_days"] = span

    return legs


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def stats(rets, mids):
    """Mean return with a cluster-robust (on fixture) 95% CI."""
    n = len(rets)
    if n == 0:
        return None
    mean = sum(rets) / n
    by: dict[str, float] = defaultdict(float)
    for r, m in zip(rets, mids):
        by[m] += r - mean
    var = sum(v * v for v in by.values()) / (n * n)
    se = math.sqrt(var) if var > 0 else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / n) if n > 1 else 0.0
    # n per arm to detect THIS point estimate against zero at 80% power
    need = (((1.96 + 0.84) * sd / mean) ** 2) if mean else float("inf")
    return {
        "n": n, "clusters": len(by), "roi": mean, "se": se, "sd": sd,
        "lo": mean - 1.96 * se, "hi": mean + 1.96 * se,
        "t": (mean / se) if se > 0 else 0.0,
        "n_for_80pct_power": need,
    }


def cell_rows(legs, edge_floor, lo, hi, sel_filter):
    out = []
    for lg in legs:
        if lg["edge"] < edge_floor:
            continue
        if not (lo <= lg["odds"] <= hi):
            continue
        if sel_filter != "ALL" and lg["sel"] != sel_filter:
            continue
        out.append(lg)
    return out


def summarise(rows, days_span=None):
    """Cell statistics. `days_span` defaults to the book's own coverage span —
    picks/day must be divided by the days the configuration could have run, not
    by the days its picks happened to land on."""
    if not rows:
        return None
    s = stats([r["ret"] for r in rows], [r["mid"] for r in rows])
    s["median_gap_min"] = median(r["gap"] for r in rows)
    s["median_lead_min"] = median(r["lead_actual"] for r in rows)
    span = days_span if days_span else median(r["book_span_days"] for r in rows)
    s["picks_per_day"] = len(rows) / span if span else 0.0
    # own-book CLV: RAW price ratio (break-even = the closing book's margin,
    # NOT zero) and the margin-corrected EV it implies. Undefined wherever the
    # leg IS the last surviving quote — see the retention caveat in the header.
    cl = [r["clv"] for r in rows if r["clv"] is not None]
    ev = [r["clv_ev"] for r in rows if r["clv_ev"] is not None]
    s["clv_n"] = len(cl)
    s["clv_raw_mean"] = (sum(cl) / len(cl)) if cl else None
    s["clv_ev_mean"] = (sum(ev) / len(ev)) if ev else None
    s["close_margin_median"] = median(r["close_margin"] for r in rows)
    return s


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def sweep(legs, markets, min_n=MIN_N):
    """Return every cell of the grid. Cells are (lead, book, market, sel,
    edge_floor, odds_lo, odds_hi)."""
    groups: dict[tuple, list] = defaultdict(list)
    for lg in legs:
        groups[(lg["lead"], lg["book"], lg["market"])].append(lg)

    cells = []
    tested = 0
    for (lead, book, market), rows in groups.items():
        sels = ["ALL"] + list(SIDES[market])
        for sel in sels:
            base = rows if sel == "ALL" else [r for r in rows if r["sel"] == sel]
            for ef in EDGE_FLOORS:
                for lo, hi in ODDS_BANDS:
                    tested += 1
                    sub = cell_rows(base, ef, lo, hi, "ALL")
                    if len(sub) < min_n:
                        continue
                    s = summarise(sub)
                    s.update(lead=lead, book=book, market=market, sel=sel,
                             edge_floor=ef, odds_lo=lo, odds_hi=hi)
                    cells.append(s)
    return cells, tested


def subset(legs, cell):
    base = [lg for lg in legs
            if lg["lead"] == cell["lead"] and lg["book"] == cell["book"]
            and lg["market"] == cell["market"]]
    return cell_rows(base, cell["edge_floor"], cell["odds_lo"], cell["odds_hi"],
                     cell["sel"])


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
BOOK_MARGIN = 0.076   # measured fleet-wide closing margin (PLAN_AFTER_AUDITS #4)


def bot_clv_audit():
    """The live SHARP trigger bots' own-book CLV, split by whether the close it
    was scored against is actually THEIR book's.

    Why this lives in the sweep. The reason to run a sharp-edge grid at all is
    the claim that the four SHARP-anchored trigger bots are the fleet's only
    CLV-positive engines. That claim is read off `shadow_bets.clv`, and two
    things in it need separating before it can be believed:

      * `clv` is a RAW price ratio with NO de-vig (settlement.py:613), so
        break-even is the closing book's MARGIN, not zero:
        `EV = (1+clv)/(1+m) - 1`, m ~ 7.6%.
      * `settle_shadow_bets` prefers the bet's own book
        (SHADOW-CLV-BOOKMAKER-FIX-2026-08-26) but FALLS BACK to the unfiltered
        `get_closing_odds`, whose own docstring says comparing a price against
        an arbitrary book "makes the resulting CLV structurally positive
        regardless of whether the bet had any edge". Rows where that fallback
        fired carry `closing_bookmaker IS NULL` — and they are not a small
        remainder.

    So the honest figure is the `closing_bookmaker = the bot's own book` row,
    margin-corrected. Report both, and report the date span: a CLV measured
    over three days is not a track record.
    """
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT bot_name, closing_bookmaker, count(*) AS n,
               avg(clv)::float AS clv, stddev(clv)::float AS sd,
               avg(CASE WHEN result = 'won'
                        THEN COALESCE(odds_at_pick_live, odds_at_pick) - 1
                        ELSE -1 END)::float AS roi,
               min(pick_time)::date AS d0, max(pick_time)::date AS d1
          FROM shadow_bets_unique
         WHERE bot_name LIKE %s AND clv IS NOT NULL
           AND result IN ('won', 'lost')
         GROUP BY 1, 2 ORDER BY 1, 3 DESC
        """,
        ("%trigger_sharp%",),
    )
    print("\n=== SHARP TRIGGER BOTS — own-book CLV vs the arbitrary-book "
          "fallback (shadow_bets_unique, dedup view per §5) ===")
    print(f"{'bot':34s} {'close@':14s} {'n':>4s} {'rawCLV':>8s} "
          f"{'EV':>8s} {'t':>6s} {'execROI':>9s}  span")
    for r in rows:
        n, c, sd = r["n"], r["clv"], (r["sd"] or 0.0)
        ev = (1 + c) / (1 + BOOK_MARGIN) - 1
        se = (sd / (1 + BOOK_MARGIN)) / math.sqrt(n) if n > 1 and sd else 0.0
        note = "" if r["closing_bookmaker"] else "   <- UNANCHORED, inflated"
        print(f"{r['bot_name']:34s} {str(r['closing_bookmaker']):14s} {n:4d} "
              f"{c*100:+7.2f}% {ev*100:+7.2f}% {(ev/se if se else 0):+6.2f} "
              f"{r['roi']*100:+8.2f}%  {r['d0']}..{r['d1']}{note}")


def fmt(s):
    if not s:
        return "n=0"
    return (f"n={s['n']:5d} ROI {s['roi']*100:+7.2f}% "
            f"CI [{s['lo']*100:+7.2f}, {s['hi']*100:+7.2f}] t={s['t']:+5.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--align-min", type=float, default=60.0,
                    help="max anchor-to-bet-quote gap, minutes")
    ap.add_argument("--markets", default="1x2,over_under_15,over_under_25,over_under_35")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--top-per-day", type=int, default=8,
                    help="daily cap for the published rule's ranking reference")
    ap.add_argument("--control", action="store_true",
                    help="junk-anchor negative control (anchor from another fixture)")
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument("--diagnostics", action="store_true")
    ap.add_argument("--bot-clv", action="store_true",
                    help="also audit the live sharp trigger bots' own-book CLV")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    markets = [m.strip() for m in a.markets.split(",") if m.strip()]
    for m in markets:
        if m not in SIDES:
            print(f"unknown market {m}", file=sys.stderr)
            return 2

    matches, odds = load(a.days, markets)
    legs = build_legs(matches, odds, markets, a.align_min,
                      a.seed if a.control else None)
    if not legs:
        print("no legs — nothing to evaluate")
        return 2

    kos = sorted(lg["ko"] for lg in legs)
    days_span = max((kos[-1] - kos[0]) / 86400.0, 1.0)
    label = "JUNK-ANCHOR CONTROL" if a.control else "REAL ANCHOR"

    print(f"\n=== OWN SHARP CONFIG SWEEP — {label} ===")
    print(f"books: {', '.join(BOOKS)} (+ {POOLED} = best aligned price)")
    print(f"anchor: Shin-de-vigged {ANCHOR_BOOK}"
          + ("  ** TAKEN FROM A DIFFERENT FIXTURE **" if a.control else ""))
    print(f"window: {a.days}d  |  alignment <= {a.align_min:.0f} min  |  "
          f"legs {len(legs):,}  |  fixtures {len({l['mid'] for l in legs}):,}")
    print(f"span: {datetime.fromtimestamp(kos[0]).date()} .. "
          f"{datetime.fromtimestamp(kos[-1]).date()} ({days_span:.0f}d)")
    print(f"median anchor gap (all legs): "
          f"{median(l['gap'] for l in legs):.1f} min")

    if a.diagnostics:
        print("\n-- per book/market leg counts and alignment --")
        g: dict[tuple, list] = defaultdict(list)
        for lg in legs:
            if lg["lead"] == 0:
                g[(lg["book"], lg["market"])].append(lg)
        for k in sorted(g):
            v = g[k]
            print(f"   {k[0]:12s} {k[1]:14s} legs={len(v):6d} "
                  f"median gap={median(x['gap'] for x in v):6.1f}m "
                  f"median lead={median(x['lead_actual'] for x in v):8.1f}m")

    # --- per-book time-ordered IS/OOS and folds (assigned in build_legs) --
    is_legs = [lg for lg in legs if lg["era"] == "IS"]
    oos_legs = [lg for lg in legs if lg["era"] == "OOS"]
    folds = [(f"fold{i+1}", [lg for lg in legs if lg["fold"] == i])
             for i in range(3)]
    print(f"\nIS/OOS: per-book 70/30 cut on kickoff — "
          f"IS {len(is_legs):,} legs, OOS {len(oos_legs):,} legs. "
          f"Folds: {', '.join(str(len(f[1])) for f in folds)} legs.")

    full_cells, tested = sweep(legs, markets, a.min_n)
    is_cells, _ = sweep(is_legs, markets, a.min_n)
    excl = sum(1 for c in full_cells if c["lo"] > 0 or c["hi"] < 0)
    print(f"\ncells in grid: {tested:,} tested, {len(full_cells):,} reached "
          f"n>={a.min_n} on the full sample, {len(is_cells):,} on the IS period.")
    print(f"MULTIPLE COMPARISONS: of the {len(full_cells):,} cells actually "
          f"evaluated, {len(full_cells)*0.05:,.0f} are expected to exclude zero "
          f"by chance at alpha=0.05 — and the cells are heavily NESTED (each is "
          f"a subset of others), so they are far from independent tests.")
    print(f"Observed: {excl:,} of {len(full_cells):,} exclude zero "
          f"({excl/max(len(full_cells),1)*100:.1f}%).")

    # --- honest finalists: selected on IS, reported on OOS ----------------
    ranked = sorted([c for c in is_cells if c["n"] >= a.min_n],
                    key=lambda c: -c["roi"])[:a.top]
    print("\n=== FINALISTS — chosen on IN-SAMPLE ROI only, then measured OOS ===")
    hdr = (f"{'book':12s} {'market':14s} {'sel':5s} {'ef':>5s} {'odds':>12s} "
           f"{'lead':>5s} | {'IS':>50s} | {'OOS':>50s} | folds")
    print(hdr)
    print("-" * len(hdr))
    finalists = []
    for c in ranked:
        oos = summarise(subset(oos_legs, c))
        fold_rois = [(name, summarise(subset(fl, c))) for name, fl in folds]
        fold_txt = " ".join(
            f"{(fs['roi']*100):+.1f}%(n={fs['n']})" if fs else "n/a"
            for _, fs in fold_rois)
        print(f"{c['book']:12s} {c['market']:14s} {c['sel']:5s} "
              f"{c['edge_floor']*100:4.0f}% {c['odds_lo']:5.2f}-{c['odds_hi']:6.2f} "
              f"{c['lead']:5d} | {fmt(c):50s} | {fmt(oos):50s} | {fold_txt}")
        finalists.append({"cell": c, "oos": oos,
                          "folds": {n: f for n, f in fold_rois}})

    # --- full-sample cells whose clustered CI excludes zero ---------------
    print(f"\n=== FULL-SAMPLE cells with n>={a.min_n} whose clustered CI "
          f"excludes zero — and their FOLDS (a cell that loses in any fold is "
          f"not a finding) ===")
    pos = sorted([c for c in full_cells if c["lo"] > 0], key=lambda c: -c["t"])
    if not pos:
        print("   (none)")
    for c in pos[:a.top]:
        fold_txt = " ".join(
            f"{(fs['roi']*100):+.1f}%(n={fs['n']})" if fs else "n/a"
            for _, fs in ((nm, summarise(subset(fl, c))) for nm, fl in folds))
        clv = ("n/a" if c["clv_raw_mean"] is None else
               f"{c['clv_raw_mean']*100:+.2f}%raw/{c['clv_ev_mean']*100:+.2f}%EV"
               f"(n={c['clv_n']})")
        print(f"{c['book']:12s} {c['market']:14s} {c['sel']:5s} "
              f"{c['edge_floor']*100:4.0f}% {c['odds_lo']:5.2f}-{c['odds_hi']:6.2f} "
              f"lead{c['lead']:4d} | {fmt(c)} | {c['picks_per_day']:.2f}/day "
              f"| gap {c['median_gap_min']:.0f}m "
              f"| need n={c['n_for_80pct_power']:,.0f} | CLV {clv} | {fold_txt}")

    # --- baseline: bet EVERY aligned leg ----------------------------------
    # Anchor-independent (no edge filter touches it), so the REAL and CONTROL
    # runs must print the same numbers here. It is the harness's own dipstick:
    # flat-backing every outcome must return approximately -m/(1+m), the book's
    # own margin. If this line is not near the measured close margin, the leg
    # construction or the settlement is wrong and nothing else can be read.
    print("\n=== BASELINE — every aligned leg, NO edge filter "
          "(must return ~ -margin/(1+margin); identical in both arms) ===")
    for book in BOOKS + [POOLED]:
        for market in markets:
            rows = [lg for lg in legs if lg["book"] == book
                    and lg["market"] == market and lg["lead"] == 0]
            s = summarise(rows)
            if s:
                m = s["close_margin_median"]
                print(f"   {book:12s} {market:14s} {fmt(s)} "
                      f"| close margin {m*100:5.2f}% "
                      f"-> expected {-m/(1+m)*100:+6.2f}%")

    # --- headline reference: the publish rule at the OWN books ------------
    print("\n=== REFERENCE — the publish rule (edge>=3%, odds<=4.0) at each book ===")
    for book in BOOKS + [POOLED]:
        for market in markets:
            rows = [lg for lg in legs
                    if lg["book"] == book and lg["market"] == market
                    and lg["lead"] == 0 and lg["edge"] >= 0.03
                    and lg["odds"] <= 4.0]
            s = summarise(rows)
            if s:
                print(f"   {book:12s} {market:14s} {fmt(s)} "
                      f"gap {s['median_gap_min']:.0f}m "
                      f"{s['picks_per_day']:.2f}/day "
                      f"close-margin {s['close_margin_median']*100:.2f}%")
        agg = summarise([lg for lg in legs
                         if lg["book"] == book and lg["lead"] == 0
                         and lg["edge"] >= 0.03 and lg["odds"] <= 4.0])
        if agg:
            print(f"   {book:12s} {'ALL MARKETS':14s} {fmt(agg)} "
                  f"gap {agg['median_gap_min']:.0f}m "
                  f"{agg['picks_per_day']:.2f}/day")

    # --- the publish rule AS PUBLISHED: top-N per day by edge -------------
    # PLAN_AFTER_AUDITS §3 states the rule as "edge >= 3%, odds <= 4.0, anchor
    # and bet quote within 60 min, TOP 8 PER DAY BY EDGE". The daily cap is not
    # a filter, it is a RANKING, so it cannot be expressed as a grid cell — and
    # it changes the composition sharply (it concentrates on the largest edges,
    # which are also the widest-anchor legs). Reported separately so the grid's
    # numbers and the published rule's number can be compared honestly.
    print(f"\n=== REFERENCE — publish rule WITH the top-{a.top_per_day}/day cap "
          f"(ranking, not a filter) ===")
    for book in BOOKS + [POOLED]:
        elig = [lg for lg in legs
                if lg["book"] == book and lg["lead"] == 0
                and lg["edge"] >= 0.03 and lg["odds"] <= 4.0]
        byday: dict[int, list] = defaultdict(list)
        for lg in elig:
            byday[int(lg["ko"] // 86400)].append(lg)
        picked = []
        for day, rows in byday.items():
            picked.extend(sorted(rows, key=lambda r: -r["edge"])[:a.top_per_day])
        s = summarise(picked)
        if s:
            print(f"   {book:12s} all markets  {fmt(s)} "
                  f"gap {s['median_gap_min']:.0f}m {s['picks_per_day']:.2f}/day")
        s1 = summarise([p for p in picked if p["market"] == "1x2"])
        if s1:
            print(f"   {book:12s} 1x2 only     {fmt(s1)} "
                  f"gap {s1['median_gap_min']:.0f}m {s1['picks_per_day']:.2f}/day")

    if a.bot_clv:
        bot_clv_audit()

    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump({"label": label, "tested": tested,
                       "cells": full_cells, "finalists": finalists},
                      fh, indent=1, default=str)
        print(f"\nwrote {a.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
