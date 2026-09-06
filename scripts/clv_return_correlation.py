#!/usr/bin/env python3
"""CLV-FINDING-REMEASURED-2026-09-06 — THE canonical CLV-vs-realised-return number.

This statistic is the entire justification for evaluating on CLV rather than ROI
(gotcha 8), and before this script existed it had been computed ad hoc at least
three times in one day with three different answers. Every one of those was a
DIFFERENT COHORT, not different data: the morning's r=+0.1381/n=721 and the
evening's r=+0.0980/n=713 differ by EIGHT ROWS dropped by an outlier guard, and
those 8 rows carry ~32% of the correlation. At that sample size a single row
moves r by up to 0.027 and any 8-row cohort difference can put r anywhere in
[+0.038, +0.187]. The disagreement was never a data question.

Rules this obeys, each of which has cost this repo time:

  * NEVER reads a stored clv_* column. Every one is a mixture of definitions
    written by different code versions -- `simulated_bets.clv_pinnacle` is RAW
    (settlement.py: odds/pin_close - 1) while `shadow_bets.clv_pinnacle` is
    DE-VIGGED (odds * true_p - 1), the same name for two quantities. Recomputing
    is the only way two runs of this script are comparable to each other.
  * ONE LEDGER PER BOT (gotcha 18) -- the rule `weekly_bot_review._fetch_paper_bets`
    uses, so the validation cohort IS the gate's cohort. A union double-counts.
  * NON-RETIRED BOTS ONLY (gotcha 22). Migration 300's n=10,542 headline is the
    all-bots count, ~89% dead double-chance strategies with no live exposure.
  * Pinnacle close via DISTINCT ON ... ORDER BY timestamp DESC, bounded
    `o.timestamp <= m.date` (gotchas 29/30/37). Never MAX(odds); `is_live=false`
    only excludes the api-football-live pseudo-book, so it is not a kickoff bound.
  * Executable price (gotcha 44). `odds_at_pick` is a high-water mark and its
    error is multiplicative, so it fakes an odds slope.
  * Cluster-robust t with match as the cluster. Several bots bet the same
    match+selection; the naive t is optimistic by ~30%.
  * The outlier guard |clv| <= 0.5 is part of the DEFINITION, not a footnote: it
    moves r by ~30%. Both values are always printed.

Usage:
    python3 scripts/clv_return_correlation.py            # headline + sensitivity
    python3 scripts/clv_return_correlation.py --json     # machine-readable only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _market_complement_selections,
    _normalize_bet_market,
    _normalize_bet_selection,
)
from workers.model.devig import devig  # noqa: E402

CLV_GUARD = 0.5    # |clv| cap; part of the definition, see header
MIN_N = 1500       # below this the number is not worth quoting

_SIM = """
SELECT s.id::text AS id, 'sim' AS ledger, b.name AS bot, s.match_id::text AS match_id,
       s.market, s.selection, (s.pnl / NULLIF(s.stake,0))::float AS ret,
       COALESCE(NULLIF(s.odds_at_pick_live,0), s.odds_at_pick)::float AS px,
       s.odds_at_pick::float AS px_hw, s.odds_at_pick_live::float AS px_live,
       m.date::date AS mdate
  FROM simulated_bets s
  JOIN bots b    ON b.id = s.bot_id
  JOIN matches m ON m.id = s.match_id
 WHERE s.result IN ('won','lost') AND s.stake > 0
   AND s.combo_legs IS NULL              -- no single closing anchor for a combo
   AND s.match_minute_at_pick IS NULL    -- gotcha 14: CLV is meaningless in-play
   AND b.retired_at IS NULL
"""

_SHADOW = """
SELECT s.id::text AS id, 'shadow' AS ledger, s.bot_name AS bot, s.match_id::text AS match_id,
       s.market, s.selection, (s.pnl / NULLIF(s.stake,0))::float AS ret,
       COALESCE(NULLIF(s.odds_at_pick_live,0), s.odds_at_pick)::float AS px,
       s.odds_at_pick::float AS px_hw, s.odds_at_pick_live::float AS px_live,
       m.date::date AS mdate
  FROM shadow_bets_unique s          -- gotcha 5: the VIEW, never the base table
  JOIN matches m ON m.id = s.match_id
 WHERE s.result IN ('won','lost') AND s.stake > 0
   AND s.bot_retired_at IS NULL
"""


def _pinnacle_closes(match_ids: list[str]) -> dict:
    closes: dict = {}
    for i in range(0, len(match_ids), 400):
        for o in execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.selection)
                   o.match_id::text AS mid, o.market, o.selection, o.odds::float AS odds
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[])
               AND o.bookmaker = 'Pinnacle'
               AND o.timestamp <= m.date
             ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
            """,
            [match_ids[i:i + 400]],
        ):
            closes[(o["mid"], o["market"], o["selection"])] = o["odds"]
    return closes


def _clv(row: dict, closes: dict, px) -> float | None:
    if not px:
        return None
    m = _normalize_bet_market(row["market"], row["selection"])
    s = _normalize_bet_selection(row["selection"])
    sides = _market_complement_selections(m, s)
    if not sides or s not in sides:
        return None                      # AH / double_chance: no line threaded through
    od = [closes.get((row["match_id"], m, sd)) for sd in sides]
    if any(o is None or o <= 1.0 for o in od):
        return None                      # partial market cannot be de-vigged
    probs = devig(od)                    # Shin for 3-way, proportional for 2-way
    if probs is None:
        return None
    p = probs[sides.index(s)]
    return px * p - 1.0 if 0.0 < p < 1.0 else None


def canonical_cohort() -> list[dict]:
    """One ledger per bot, non-retired, settled pre-match singles, CLV recomputed.

    Rows carry `clv` (executable price) and `clv_hw` (high-water). Callers apply
    the guard themselves so guarded and unguarded are always available together.
    """
    sim = execute_query(_SIM)
    shadow = execute_query(_SHADOW)
    sim_bots = {r["bot"] for r in sim}
    rows = sim + [r for r in shadow if r["bot"] not in sim_bots]

    closes = _pinnacle_closes(sorted({r["match_id"] for r in rows}))
    out = []
    for r in rows:
        r["clv"] = _clv(r, closes, r["px"])
        r["clv_hw"] = _clv(r, closes, r["px_hw"])
        if r["clv"] is not None and r["ret"] is not None:
            out.append(r)
    return out


# ── statistics ───────────────────────────────────────────────────────────────

def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sx * sy)


def tstat(r, n):
    return r * math.sqrt((n - 2) / (1 - r * r)) if n > 2 and abs(r) < 1 else 0.0


def partial(x, y, z):
    rxy, rxz, ryz = pearson(x, y), pearson(x, z), pearson(y, z)
    d = math.sqrt((1 - rxz ** 2) * (1 - ryz ** 2))
    return (rxy - rxz * ryz) / d if d else 0.0


def clustered_t(rows, key="clv"):
    """Cluster-robust t on the slope of ret ~ a + b*clv, clustered on match_id.

    Several bots bet the same match+selection, so the naive t overstates by ~30%.
    """
    x = [r[key] for r in rows]
    y = [r["ret"] for r in rows]
    n = len(rows)
    if n < 3:
        return 0.0, 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx == 0:
        return 0.0, 0.0
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    a0 = my - b * mx
    cl = defaultdict(float)
    for r in rows:
        cl[r["match_id"]] += (r[key] - mx) * (r["ret"] - (a0 + b * r[key]))
    var = sum(v * v for v in cl.values()) / sxx ** 2
    if var <= 0:
        return b, 0.0
    g = len(cl)
    if g < 2:
        return b, 0.0
    se = math.sqrt(var) * math.sqrt(g / (g - 1)) * math.sqrt((n - 1) / (n - 2))
    return b, (b / se if se else 0.0)


def measure(rows, key="clv", pxk="px") -> dict:
    x = [r[key] for r in rows]
    y = [r["ret"] for r in rows]
    z = [r[pxk] for r in rows]
    n = len(rows)
    r = pearson(x, y)
    _, tc = clustered_t(rows, key)
    return {
        "n": n, "matches": len({q["match_id"] for q in rows}),
        "r": round(r, 4), "t": round(tstat(r, n), 2), "t_clustered": round(tc, 2),
        "partial_r_given_odds": round(partial(x, y, z), 4),
        "r_odds_return": round(pearson(y, z), 4),
        "mean_clv": round(sum(x) / n, 4), "mean_return": round(sum(y) / n, 4),
    }


def headline() -> dict:
    """The one number. Shared with the smoke test — do not inline a second copy."""
    rows = canonical_cohort()
    guarded = [r for r in rows if abs(r["clv"]) <= CLV_GUARD]
    out = measure(guarded)
    out["guard"] = CLV_GUARD
    out["unguarded"] = measure(rows)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = canonical_cohort()

    def g(th):
        return [r for r in rows if abs(r["clv"]) <= th]

    head = headline()
    if args.json:
        print(json.dumps(head, indent=2))
        return 0

    print(f"CANONICAL  n={head['n']} over {head['matches']} matches  "
          f"r={head['r']:+.4f}  t={head['t']:+.2f}  t_clu={head['t_clustered']:+.2f}  "
          f"partial(|odds)={head['partial_r_given_odds']:+.4f}  "
          f"r(odds,ret)={head['r_odds_return']:+.4f}")
    if head["n"] < MIN_N:
        print(f"  *** n < {MIN_N} — do not quote this figure ***")

    print("\nSENSITIVITY (the point of this script — quote the spread, not the point)")
    variants = [
        ("no outlier guard", rows, "clv", "px"),
        ("guard |clv|<=0.3", g(0.3), "clv", "px"),
        ("guard |clv|<=0.8", g(0.8), "clv", "px"),
        ("price basis = high-water",
         [r for r in g(CLV_GUARD) if r["clv_hw"] is not None], "clv_hw", "px_hw"),
        ("HAS an executable price", [r for r in g(CLV_GUARD) if r["px_live"]], "clv", "px"),
        ("NO executable price", [r for r in g(CLV_GUARD) if not r["px_live"]], "clv", "px"),
        ("sim ledger only", [r for r in g(CLV_GUARD) if r["ledger"] == "sim"], "clv", "px"),
        ("shadow ledger only", [r for r in g(CLV_GUARD) if r["ledger"] == "shadow"], "clv", "px"),
        ("matches from 2026-08-01",
         [r for r in g(CLV_GUARD) if str(r["mdate"]) >= "2026-08-01"], "clv", "px"),
    ]
    for label, sub, key, pxk in variants:
        if len(sub) < 30:
            print(f"  {label:30s} n={len(sub)} (too few)")
            continue
        m = measure(sub, key, pxk)
        print(f"  {label:30s} n={m['n']:6d}  r={m['r']:+.4f}  "
              f"t={m['t']:+6.2f}  t_clu={m['t_clustered']:+6.2f}")

    print("\nPER BOT (gotcha 47 — an effect is a bot effect until you split by bot)")
    by = defaultdict(list)
    for r in g(CLV_GUARD):
        by[r["bot"]].append(r)
    for bot, ss in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(ss) < 30:
            continue
        m = measure(ss)
        print(f"  {bot:26s} n={m['n']:5d}  r={m['r']:+.4f}  t={m['t']:+6.2f}  "
              f"meanCLV={m['mean_clv']:+.4f}  ROI={m['mean_return']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
