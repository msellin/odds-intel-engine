#!/usr/bin/env python3
"""#106 DE-VIG BAKE-OFF — step 0: the frozen BEFORE panel.

Owner, 2026-09-24: "take some example fixtures, measure them now, and after #106
measure again". The bookmaker's margin does not change with our method — what changes
is how we SPLIT it across outcomes, i.e. the fair probability, the edge and whether a
pick clears the 3% floor. So the panel freezes the RAW quotes a real publish run saw,
plus what today's method (Shin, everywhere) made of them. After #106 the same raw
quotes are re-priced under every method; nothing is re-fetched, so any difference is
the method and only the method.

REPLAY. The live rule (`scripts/publish_picks_forward_test.py::load_candidates`) runs
with now() = the moment of a past publish run: latest quote per (match, market,
selection, book) in the 6 h before, fixtures kicking off 45 min - 14 h later, phantom
books excluded. The rule's own constants and grading are imported, not copied.
CHECK P: before the panel is trusted, the replay must reproduce the stored p_sharp and
edge of the published legs.

PANEL. Every published leg of the two published arms (live = Pinnacle anchor,
consensus_anchor = de-vig-each-book-then-average), plus a stratified sample of pool
markets that were NOT published (by arm × market × league tier), so the panel shows
both what we published and what we passed on.

    python3 scripts/devig_bakeoff.py baseline      # writes dev/active/devig-panel-2026-09-24.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "dev" / "active" / "devig-panel-2026-09-24.json"
SAMPLE_PER_STRATUM = 25


def replay(asof):
    """side_q per (match, market) exactly as load_candidates would have seen it at `asof`."""
    from workers.api_clients.db import execute_query
    from scripts.publish_picks_forward_test import (
        EXCLUDED_BOOKS, LOOKAHEAD_H, MARKETS, MIN_LEAD_MIN)
    rows = execute_query("""
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id::text mid, o.market, o.selection, o.bookmaker,
               o.odds::float AS odds, o.timestamp, m.date AS kickoff,
               l.name AS league, l.tier AS league_tier
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE o.market = ANY(%s) AND o.is_live IS NOT TRUE
           AND NOT (o.bookmaker = ANY(%s))
           AND m.date > %s::timestamptz + make_interval(mins => %s)
           AND m.date < %s::timestamptz + make_interval(hours => %s)
           AND o.timestamp > %s::timestamptz - interval '6 hours'
           AND o.timestamp <= %s::timestamptz
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC""",
        (list(MARKETS), list(EXCLUDED_BOOKS), asof, MIN_LEAD_MIN, asof, LOOKAHEAD_H, asof, asof))
    q, meta = defaultdict(lambda: defaultdict(dict)), {}
    for r in rows:
        q[(r["mid"], r["market"])][r["selection"]][r["bookmaker"]] = (r["odds"], r["timestamp"])
        meta[r["mid"]] = r
    return q, meta


def price_market(sides, side_q, anchor, devig_fn, tier):
    """Mirror of load_candidates' per-market body, with the de-vig method as a parameter.
    Returns {side: leg} or None when the market has no usable anchor."""
    from scripts.publish_picks_forward_test import (
        ALIGN_MIN, CONSENSUS_MIN_BOOKS, GRADE_PANEL, MAX_ANCHOR_OVERROUND, MAX_ODDS, MAX_RATIO,
        MIN_EDGE, grade_consensus_pick)
    if anchor == "consensus":
        per, stamps = [], []
        for b in {b for s in sides for b in (side_q.get(s) or {})}:
            qs = [(side_q.get(s) or {}).get(b) for s in sides]
            if any(x is None or x[0] <= 1.0 for x in qs):
                continue
            p = devig_fn([x[0] for x in qs])
            if p:
                per.append(p); stamps.append(max(x[1] for x in qs))
        if len(per) < CONSENSUS_MIN_BOOKS:
            return None
        probs = [sum(p[i] for p in per) / len(per) for i in range(len(sides))]
        anchor_ts, anchor_odds, overround = max(stamps), {s: 1 / p for s, p in zip(sides, probs)}, 0.0
    else:
        pin = {s: (side_q.get(s) or {}).get("Pinnacle") for s in sides}
        if any(pin[s] is None for s in sides):
            return None
        anchor_ts = max(pin[s][1] for s in sides)
        anchor_odds = {s: pin[s][0] for s in sides}
        overround = sum(1 / o for o in anchor_odds.values()) - 1
        if overround > MAX_ANCHOR_OVERROUND:
            return None
        probs = devig_fn([anchor_odds[s] for s in sides])
        if probs is None:
            return None
    out = {}
    for s, p in zip(sides, probs):
        aligned = {b: v for b, v in (side_q.get(s) or {}).items()
                   if abs((v[1] - anchor_ts).total_seconds()) / 60 <= ALIGN_MIN}
        if not aligned:
            continue
        book, (odds, _ts) = max(aligned.items(), key=lambda kv: kv[1][0])
        if odds > MAX_ODDS or odds / anchor_odds[s] - 1 > MAX_RATIO:
            continue
        edge = p * odds - 1
        grade = None
        if anchor == "consensus":
            idx = sides.index(s)
            panel = {}
            for pb in GRADE_PANEL:
                qs = [(side_q.get(x) or {}).get(pb) for x in sides]
                if all(x is not None and x[0] > 1.0 for x in qs):
                    bp = devig_fn([x[0] for x in qs])
                    if bp:
                        panel[pb] = bp[idx]
            grade = grade_consensus_pick(edge, odds, book, tier, panel)[0]
        out[s] = {"p": p, "odds": odds, "book": book, "edge": edge,
                  "qualifies": edge >= MIN_EDGE, "grade": grade, "overround": overround}
    return out


def _jsonable_quotes(side_q):
    return {s: {b: [o, t.isoformat()] for b, (o, t) in books.items()} for s, books in side_q.items()}


def baseline() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig
    from scripts.publish_picks_forward_test import MARKETS

    pub = execute_query("""
        SELECT arm, match_id::text mid, market, selection, odds::float odds, bookmaker,
               edge::float edge, p_sharp::float p_sharp, grade, published_at, rule_version
          FROM picks_forward_test WHERE arm IN ('live', 'consensus_anchor')
         ORDER BY published_at""")
    runs = sorted({p["published_at"].replace(second=0, microsecond=0) for p in pub})
    records, seen, check = [], set(), []
    pool_by_stratum = defaultdict(list)
    pub_keys = {(p["arm"], p["mid"], p["market"]) for p in pub}
    pub_by_run = defaultdict(list)
    for p in pub:
        pub_by_run[p["published_at"].replace(second=0, microsecond=0)].append(p)
    for i, run in enumerate(runs):
        asof = run + timedelta(minutes=1)          # the run's own quotes, nothing later
        q, meta = replay(asof)
        for (mid, market), side_q in q.items():
            sides = MARKETS[market]
            tier = meta[mid].get("league_tier")
            for anchor, arm in (("pinnacle", "live"), ("consensus", "consensus_anchor")):
                legs = price_market(sides, side_q, anchor, devig, tier)
                if not legs:
                    continue
                rec = {"run": run.isoformat(), "arm": arm, "match_id": mid, "market": market,
                       "sides": sides, "kickoff": meta[mid]["kickoff"].isoformat(),
                       "league": meta[mid].get("league"), "tier": tier,
                       "quotes": _jsonable_quotes(side_q),
                       "shin": legs, "published": None}
                for p in pub_by_run.get(run, []):
                    if p["arm"] == arm and p["mid"] == mid and p["market"] == market:
                        rec["published"] = {k: p[k] for k in ("selection", "odds", "bookmaker", "edge",
                                                              "p_sharp", "grade", "rule_version")}
                        leg = legs.get(p["selection"])
                        check.append((p, leg))
                key = (arm, mid, market)
                if rec["published"]:
                    records.append(rec); seen.add(key)
                elif key not in pub_keys and key not in seen:
                    seen.add(key)
                    pool_by_stratum[(arm, market, tier)].append(rec)
        print(f"  run {i + 1}/{len(runs)} {run:%m-%d %H:%M}  markets {len(q)}", flush=True)

    # CHECK P — the replay must reproduce what was actually published
    ok = sum(1 for p, leg in check if leg and abs(leg["p"] - p["p_sharp"]) < 1e-6
             and abs(leg["odds"] - p["odds"]) < 1e-9)
    print(f"\nCHECK P: replay reproduces {ok}/{len(pub)} published legs exactly "
          f"({len(check)} found in a replayed pool)")
    rng = random.Random(106)
    for k in sorted(pool_by_stratum, key=str):
        v = pool_by_stratum[k]
        records += rng.sample(v, min(SAMPLE_PER_STRATUM, len(v)))
    PANEL.write_text(json.dumps({"created": "2026-09-24", "method_before": "shin (every market)",
                                 "check_p": {"reproduced": ok, "published": len(pub)},
                                 "records": records}, indent=0, default=str))
    print(f"panel: {sum(1 for r in records if r['published'])} published + "
          f"{sum(1 for r in records if not r['published'])} sampled pool markets -> {PANEL.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["baseline"])
    a = ap.parse_args()
    return baseline()


if __name__ == "__main__":
    raise SystemExit(main())
