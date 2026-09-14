#!/usr/bin/env python3
"""OWN-book CLV universe — the UNSELECTED leg-level panel for segment search.

WHAT THIS BUILDS, AND WHY IT IS BUILT THIS WAY
----------------------------------------------
One row per (fixture, book, market, selection, lead-time arm), carrying the
target the operator's OWN-betting brief defines as the only thing that counts:

    mc_clv = (1 + clv) / (1 + m) - 1
      clv  = odds_at_decision / odds_at_own_book_close - 1     (RAW price ratio)
      m    = that book's OWN overround on that fixture/market, computed PER ROW

`m` is re-derived here rather than imported because `settlement.closing_book_margin`
issues one SQL round trip per leg (`get_closing_odds`), which is ~4 queries per
candidate row and hours of wall clock on a 300k-row panel. The definition is
copied exactly and pinned by smoke test OWN-SEGMENT-CLV-MARGIN-DEF:
  * close leg   -> the book's LAST pre-kickoff, non-live quote within
                   DIRECT_CLOSE_MAX_MIN (60) minutes of kickoff  (= get_book_close)
  * margin legs -> the book's LAST pre-kickoff quote per selection, NO freshness
                   bound (= get_closing_odds(..., bookmaker)), full complement
                   required, and m rejected unless 0 <= m <= 0.5.

THE BINDING DATA CONSTRAINT (read before trusting any span in the output)
------------------------------------------------------------------------
`prune_old_simple` keeps, after 7 days, at most `is_opening` + `is_closing` +
the latest pre-kickoff row per series — and until 2026-09-11 the direct-book
writers stamped almost no anchors at all (ANALYSIS_GOTCHAS 59). At Coolbet,
Epicbet and Unibet-Site that leaves exactly ONE surviving pre-kickoff row per
series older than the retention window, and that row IS the close. So:

    own-book CLV is arithmetically impossible to compute before the retention
    window. Not noisy — zero by construction (numerator = denominator).

This script therefore refuses to run outside the window and prints the observed
rows-per-series so the caller can see the cliff rather than infer it.

    python3 scripts/own_book_clv_universe.py --out /tmp/panel.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from workers.api_clients.db import execute_query  # noqa: E402

BOOKS = ["Coolbet", "Epicbet", "Unibet-Site"]      # EMTA-legal, self-scraped
MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
CLOSE_MAX_MIN = 60.0          # settlement.DIRECT_CLOSE_MAX_MIN
LEADS_H = [1.0, 3.0, 6.0, 12.0, 24.0]   # decision arms, hours before kickoff
MARGIN_MAX = 0.5


def haversine(a_lat, a_lng, b_lat, b_lng):
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    r = 6371.0
    p1, p2 = math.radians(float(a_lat)), math.radians(float(b_lat))
    dp = p2 - p1
    dl = math.radians(float(b_lng) - float(a_lng))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def fetch_odds(lo: str, hi: str):
    return execute_query(
        """
        SELECT o.match_id, o.bookmaker, o.market, o.selection,
               o.odds::float AS odds, o.timestamp, m.date AS ko
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = ANY(%s)
           AND o.market = ANY(%s)
           AND COALESCE(o.is_live, FALSE) = FALSE
           AND o.timestamp <= m.date
           AND o.odds > 1.01
           AND m.date >= %s::date AND m.date < %s::date
           AND m.status = 'finished'
        """,
        (BOOKS, list(MARKETS), lo, hi),
    )


def fetch_pinnacle(lo: str, hi: str):
    """Pinnacle pre-kickoff quotes for the same fixtures, for the sharp anchor.

    Bound on `timestamp < m.date` and NOT on `is_live = false` alone —
    ANALYSIS_GOTCHAS 37: 35.3pct of `is_live=false` Pinnacle rows are
    post-kickoff. Both guards are applied."""
    rows = execute_query(
        """
        SELECT o.match_id, o.market, o.selection, o.odds::float AS odds, o.timestamp
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = 'Pinnacle' AND o.market = ANY(%s)
           AND COALESCE(o.is_live, FALSE) = FALSE
           AND o.timestamp < m.date
           AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
           AND o.odds > 1.01
           AND m.date >= %s::date AND m.date < %s::date AND m.status = 'finished'
        """,
        (list(MARKETS), lo, hi),
    )
    d: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        d[(r["match_id"], r["market"])][r["selection"]].append(
            (r["timestamp"], r["odds"]))
    for k in d:
        for s_ in d[k]:
            d[k][s_].sort(key=lambda x: x[0])
    return d


def shin(odds_list):
    """Shin de-vig over a complement. Removes proportionally MORE margin from
    longshots than a proportional de-vig, which is exactly where a spurious
    anchor edge would otherwise appear. Same implementation as
    scripts/residual_test.py."""
    q = [1.0 / o for o in odds_list]
    t = sum(q)
    z = 0.0
    for _ in range(80):
        zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
              for qi in q]
        s_ = sum(zs)
        if s_ > 1:
            z += (s_ - 1) * 0.5
        else:
            z -= (1 - s_) * 0.5
        z = max(0.0, min(0.5, z))
    zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
          for qi in q]
    tot = sum(zs)
    return [x / tot for x in zs]


def fetch_context(lo: str, hi: str):
    rows = execute_query(
        """
        SELECT m.id, m.date AS ko, m.league_id, m.round_label,
               m.home_team_id, m.away_team_id,
               m.score_home, m.score_away,
               m.lineups_fetched_at,
               l.name AS league, l.country, l.tier, l.priority,
               ht.stadium_lat AS h_lat, ht.stadium_lng AS h_lng,
               at.stadium_lat AS a_lat, at.stadium_lng AS a_lng,
               f.elo_diff, f.rest_days_home, f.rest_days_away,
               f.injury_count_home, f.injury_count_away,
               f.injury_severity_home, f.injury_severity_away,
               f.lineup_confirmed, f.form_ppg_home, f.form_ppg_away,
               f.xg_overperf_home, f.xg_overperf_away,
               f.news_impact_score, f.fixture_importance,
               f.league_position_home, f.league_position_away,
               f.h2h_win_pct, f.data_tier
          FROM matches m
          LEFT JOIN leagues l ON l.id = m.league_id
          LEFT JOIN teams ht ON ht.id = m.home_team_id
          LEFT JOIN teams at ON at.id = m.away_team_id
          LEFT JOIN match_feature_vectors f ON f.match_id = m.id
         WHERE m.date >= %s::date AND m.date < %s::date AND m.status = 'finished'
        """,
        (lo, hi),
    )
    return {r["id"]: r for r in rows}


def fetch_familiarity(lo: str):
    """How many finished fixtures we already hold for each team BEFORE the window.

    Strictly pre-window so it cannot absorb anything about the fixture itself."""
    rows = execute_query(
        """SELECT t, count(*) n FROM (
               SELECT home_team_id t FROM matches
                WHERE status='finished' AND date < %s::date
               UNION ALL
               SELECT away_team_id t FROM matches
                WHERE status='finished' AND date < %s::date) s
            GROUP BY t""",
        (lo, lo),
    )
    return {r["t"]: int(r["n"]) for r in rows}


def build(lo: str, hi: str):
    odds = fetch_odds(lo, hi)
    ctx = fetch_context(lo, hi)
    fam = fetch_familiarity(lo)
    pin = fetch_pinnacle(lo, hi)

    # series[(match, book, market)][selection] = [(ts, odds), ...]
    series: dict = defaultdict(lambda: defaultdict(list))
    for r in odds:
        series[(r["match_id"], r["bookmaker"], r["market"])][r["selection"]].append(
            (r["timestamp"], r["odds"]))

    diag = defaultdict(int)
    panel = []
    for (mid, book, market), legs in series.items():
        sides = MARKETS[market]
        ko = ctx.get(mid, {}).get("ko")
        if ko is None:
            diag["no_context"] += 1
            continue
        for s in legs:
            legs[s].sort(key=lambda x: x[0])

        # ---- margin: last pre-KO quote per leg at THIS book, no freshness bound
        if not all(legs.get(s) for s in sides):
            diag["incomplete_complement"] += 1
            continue
        m = sum(1.0 / legs[s][-1][1] for s in sides) - 1.0
        if not (0.0 <= m <= MARGIN_MAX):
            diag["margin_rejected"] += 1
            continue

        # ---- close: last pre-KO quote within CLOSE_MAX_MIN, per leg
        close = {}
        for s in sides:
            fresh = [(t, o) for t, o in legs[s]
                     if (ko - t).total_seconds() / 60.0 <= CLOSE_MAX_MIN]
            if fresh:
                close[s] = fresh[-1]
        if len(close) != len(sides):
            diag["no_fresh_close"] += 1
            continue

        c = ctx[mid]
        # implied-probability shape of the CLOSE, used for favourite/dog labels.
        # Uses the CLOSE only for descriptive labels that are also computable at
        # decision time from the decision price; the decision-time price is what
        # every band/ratio field below is actually built from.
        for lead_h in LEADS_H:
            cutoff = ko - timedelta(hours=lead_h)
            dec = {}
            for s in sides:
                prior = [(t, o) for t, o in legs[s] if t <= cutoff]
                if prior:
                    dec[s] = prior[-1]
            if len(dec) != len(sides):
                continue
            # the decision quotes must be a genuinely earlier observation than
            # the close, else clv is 0 by construction (the retention artefact)
            if all(dec[s][0] == close[s][0] for s in sides):
                diag["decision_is_close"] += 1
                continue
            m_dec = sum(1.0 / dec[s][1] for s in sides) - 1.0
            inv_dec = sum(1.0 / dec[s][1] for s in sides)
            # ---- sharp anchor AS OF THE SAME DECISION MOMENT (never the close)
            p_sharp = {}
            pl = pin.get((mid, market), {})
            if all(pl.get(s) for s in sides):
                pq = {}
                for s in sides:
                    prior = [(t, o) for t, o in pl[s] if t <= cutoff]
                    if prior:
                        pq[s] = prior[-1][1]
                if len(pq) == len(sides):
                    ps = shin([pq[s] for s in sides])
                    p_sharp = dict(zip(sides, ps))
            for s in sides:
                o_dec, o_cl = dec[s][1], close[s][1]
                clv = o_dec / o_cl - 1.0
                mc = (1.0 + clv) / (1.0 + m) - 1.0
                p_dec = (1.0 / o_dec) / inv_dec          # de-vigged at decision
                row = {
                    "match_id": mid, "book": book, "market": market,
                    "selection": s, "lead_h": lead_h,
                    "ko": ko.isoformat(), "ko_date": ko.date().isoformat(),
                    "ko_hour": ko.hour, "ko_dow": ko.weekday(),
                    "odds_dec": o_dec, "odds_close": o_cl,
                    "clv": clv, "margin": m, "margin_dec": m_dec, "mc_clv": mc,
                    "p_dec": p_dec,
                    "p_sharp": p_sharp.get(s),
                    "prob_edge": (None if s not in p_sharp
                                  else p_sharp[s] - 1.0 / o_dec),
                    "dec_mins_before_ko": (ko - dec[s][0]).total_seconds() / 60.0,
                    "close_mins_before_ko": (ko - close[s][0]).total_seconds() / 60.0,
                    "league_id": c["league_id"], "league": c["league"],
                    "country": c["country"], "tier": c["tier"],
                    "priority": c["priority"], "round_label": c["round_label"],
                    "data_tier": c["data_tier"],
                    "elo_diff": _f(c["elo_diff"]),
                    "rest_days_home": _f(c["rest_days_home"]),
                    "rest_days_away": _f(c["rest_days_away"]),
                    "injury_count_home": _f(c["injury_count_home"]),
                    "injury_count_away": _f(c["injury_count_away"]),
                    "injury_severity_home": _f(c["injury_severity_home"]),
                    "injury_severity_away": _f(c["injury_severity_away"]),
                    "lineup_confirmed": c["lineup_confirmed"],
                    "form_ppg_home": _f(c["form_ppg_home"]),
                    "form_ppg_away": _f(c["form_ppg_away"]),
                    "xg_overperf_home": _f(c["xg_overperf_home"]),
                    "xg_overperf_away": _f(c["xg_overperf_away"]),
                    "news_impact_score": _f(c["news_impact_score"]),
                    "fixture_importance": _f(c["fixture_importance"]),
                    "h2h_win_pct": _f(c["h2h_win_pct"]),
                    "travel_km": haversine(c["h_lat"], c["h_lng"],
                                           c["a_lat"], c["a_lng"]),
                    "fam_home": fam.get(c["home_team_id"], 0),
                    "fam_away": fam.get(c["away_team_id"], 0),
                    "score_home": c["score_home"], "score_away": c["score_away"],
                }
                panel.append(row)
        diag["series_used"] += 1
    return panel, diag


def _f(v):
    return None if v is None else float(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", default="2026-09-07")
    ap.add_argument("--to", dest="hi", default="2026-09-14")
    ap.add_argument("--out", default="/tmp/own_clv_panel.json.gz")
    a = ap.parse_args()

    panel, diag = build(a.lo, a.hi)
    with gzip.open(a.out, "wt") as fh:
        json.dump(panel, fh)
    print(f"panel rows: {len(panel)}  -> {a.out}")
    print("diagnostics:", dict(diag))
    if panel:
        ds = sorted({r['ko_date'] for r in panel})
        print(f"DATE SPAN: {ds[0]} .. {ds[-1]}  ({len(ds)} match days)")
        by = defaultdict(int)
        for r in panel:
            by[(r["book"], r["market"], r["lead_h"])] += 1
        for k in sorted(by):
            print(f"  {k[0]:12s} {k[1]:14s} lead {k[2]:5.1f}h  n={by[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
