#!/usr/bin/env python3
"""BOOK-SET-COUNTERFACTUAL — what the published PICKS would have looked like if
`ACCESSIBLE_BOOKMAKERS` had never been cut.

MEASUREMENT ONLY. Nothing here writes to the database or changes a production
gate. It answers PRIORITY_QUEUE #005 (ACCESSIBLE-BOOK-SET-SHRANK-UNMEASURED) and
the half of #065 (CALIBRATION-AFTER-THE-OU-BUG) that asks whether the book-set
shrink could have *selected for* the model's overconfident tail.

WHY A COUNTERFACTUAL IS POSSIBLE AT ALL
---------------------------------------
`odds_snapshots` still holds every book, including the ones the 2026-09-05 cut
(`bb39c6e8`, ACCESSIBLE-SET-VERIFY) removed from the placeable set — Marathonbet
alone is still our largest single feed. Nothing was un-collected; a config
constant stopped *reading* four books. So for any historical fixture we can
re-derive the best price under an arbitrary book set and re-run the gate stack.

THE THREE ARMS
--------------
  as_was       the set that was actually in force at the moment of the decision,
               reconstructed from git (see `_AS_WAS_TIMELINE`). This is the
               factual arm — it is what the pipeline really saw.
  restricted   today's `ACCESSIBLE_BOOKMAKERS`, imported, applied across the whole
               window. Isolates the policy from the feed-availability drift that
               `as_was` also contains.
  all_books    every book we collect, minus the known-bad sources.

WHAT THE MODEL DOES *NOT* HOLD CONSTANT ACROSS ARMS, AND WHY THAT IS THE POINT
------------------------------------------------------------------------------
`calibrated_prob` is NOT independent of the book set. `improvements.calibrate_prob`
shrinks the raw ensemble probability toward an anchor and takes BOTH the best
price (`odds`, for the CAL-ALPHA-ODDS longshot step) and its implied probability
(`implied_prob`, the fallback anchor when no Pinnacle line exists). A thinner book
set therefore moves the probability as well as the price, in the same pass. Any
counterfactual that recomputed `edge` while holding `cal_prob` fixed would be
measuring a model that does not exist. So each arm re-runs the real
`calibrate_prob` against that arm's own best price.

FIDELITY OF THE CALIBRATION
---------------------------
`model_calibration` keeps its full fit history (`fitted_at`), so the Platt
coefficients and shrinkage alphas are looked up AS OF the decision time rather
than as of today. That matters more than it sounds: the O/U curve at the centre
of #065 was created 2026-09-03 10:49 UTC and DELETED by migration 335 on
2026-09-13, with the deleted rows preserved in
`model_calibration_ou_domain_mismatch_backup`. Reading both tables reproduces all
three O/U regimes — no curve before 09-03, the domain-mismatched curve
09-03..09-13, no curve after — instead of smearing today's absence of a curve
backwards over a window that had one. That is ANALYSIS_GOTCHAS #39 applied to
the calibrator rather than to the book set.

WHAT IT CANNOT TELL US — read this before quoting any number below
-------------------------------------------------------------------
1. It reconstructs which candidates would have CLEARED. It cannot reconstruct
   how the bot's own state would have evolved: bankroll, Kelly stake, the
   per-league exposure cap, daily caps, and the dedup that stops the same
   fixture being picked twice all depend on the picks that came before. Every
   ROI here is therefore FLAT-STAKE, never Kelly, and is stated as such.
2. It applies the price/probability/edge/odds gates. It does NOT re-run the
   pipeline's later vetoes (odds-movement veto, PIN-CROSS-DRIFT, CAL-SHARP-GATE,
   news triggers) nor the per-bot league/tier filters. `--pin-veto` adds the
   Pinnacle disagreement veto as a sensitivity because that one gate is both
   cheap and book-set-DEPENDENT (it compares `cal_prob`, which moves with the
   price, against a fixed Pinnacle anchor) — leaving it out would bias the
   all-books arm.
3. A single evaluation instant per fixture (`--lead-hours`, default 6h, the
   median lead of the real v10 1x2 picks) stands in for a pipeline that
   re-evaluates hourly. Picks raised at a different hour saw different prices.
4. The universe is fixtures we MODELLED and that SETTLED. A book set cannot be
   judged on fixtures nobody priced.

Usage:
    PYTHONPATH=. python3 scripts/backtest_book_set_restriction.py \
        --start 2026-08-15 --end 2026-09-21
    PYTHONPATH=. python3 scripts/backtest_book_set_restriction.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env")

# ── the real constants, imported, never re-typed ─────────────────────────────
# Re-typing any of these is how this repo's gates have drifted apart before
# (SIGNAL-PLACER-1X2-ALIGN, PLACER-OU-VOCAB-FLOOR). A backtest that re-types a
# floor measures a system nobody runs.
from workers.jobs.daily_pipeline_v2 import (  # noqa: E402
    ACCESSIBLE_BOOKMAKERS,
    BOTS_CONFIG,
    ODDS_MAX_AGE_HOURS,
    ODDS_MAX_LAG_HOURS,
    PRICE_REFERENCE_BOOKMAKERS,
)
from workers.utils.odds_quality import BLACKLISTED_OU_SOURCES  # noqa: E402
from workers.automation.coolbet_placer import (  # noqa: E402
    _min_odds_for,
    clears_edge_floor,
    min_edge_for_pick,
)
from workers.registry.bot_registry import BOTS  # noqa: E402
from workers.model import improvements as _imp  # noqa: E402

# Pinnacle disagreement veto — the pipeline declares this inline at
# daily_pipeline_v2.py:2725 as a local, so it cannot be imported. Kept here with
# the same name so a grep for the constant finds both.
PINNACLE_VETO_GAP = 0.12

# Mirrors the pipeline's local `_OUTLIER_MULT` / `_OUTLIER_MIN_BOOKS`
# (ODDS-OUTLIER-FILTER-2026-08-18, re-calibrated 2026-09-16). Also locals, also
# not importable. The smoke test pins the values so a change to one side shows up.
OUTLIER_MULT = {"1x2": 1.25}
OUTLIER_MIN_BOOKS = 3

# Mirrors the pipeline's local `DATA_TIER_EDGE_BUMP` (daily_pipeline_v2.py:3352).
DATA_TIER_EDGE_BUMP = {"A": 0.00, "B": 0.02, "C": 0.08}

# THE picks bot. `coolbet_feed_watchdog.PICKS_BOT` names it, so this is not a
# choice made here — it is the bot whose silence the ops alert is built around.
PICKS_BOT = "bot_v10_all"

# Sources that are never a real offer, excluded from EVERY arm. `api-football`
# and `api-football-live` are synthetic/in-play feeds; `Unibet-Kambi` was proved
# to quote above what unibet.ee offers on 38% of selections
# (KAMBI-FEED-DIVERGENCE-2026-09-06) and was retired 2026-09-15. Including any of
# them would hand the all-books arm a price nobody could ever have taken, which
# is the exact failure this backtest exists to avoid recreating.
HARD_EXCLUDED_SOURCES = frozenset(
    {"api-football", "api-football-live", "Unibet-Kambi"} | set(BLACKLISTED_OU_SOURCES)
)

MARKETS = ("1x2", "over_under_25")
SELECTIONS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
# predictions-table market key for each (market, selection)
PRED_KEY = {
    ("1x2", "home"): "1x2_home",
    ("1x2", "draw"): "1x2_draw",
    ("1x2", "away"): "1x2_away",
    ("over_under_25", "over"): "over25",
    ("over_under_25", "under"): "under25",
}
# match_signals name carrying the Pinnacle implied probability for each selection
PIN_SIGNAL = {
    ("1x2", "home"): "pinnacle_implied_home",
    ("1x2", "draw"): "pinnacle_implied_draw",
    ("1x2", "away"): "pinnacle_implied_away",
    ("over_under_25", "over"): "pinnacle_implied_over25",
    ("over_under_25", "under"): "pinnacle_implied_under25",
}

# ── the as-was timeline, reconstructed from git ──────────────────────────────
# Each entry is (UTC instant the set took effect, the set). Recovered by walking
# every commit that touched `ACCESSIBLE_BOOKMAKERS` in daily_pipeline_v2.py and
# converting the commit time from EEST. The engine auto-deploys on push
# (ENGINE-DEPLOY-2026-08-24), so commit time is deploy time to within minutes.
_AS_WAS_TIMELINE: list[tuple[datetime, frozenset]] = [
    (datetime(2000, 1, 1, tzinfo=timezone.utc),
     frozenset({"Coolbet", "Betano", "Unibet", "Marathonbet", "10Bet", "888Sport", "Pinnacle"})),
    # PINNACLE-NOT-ACCESSIBLE-2026-09-04 (commit 2026-09-04 16:19 EEST)
    (datetime(2026, 9, 4, 13, 19, tzinfo=timezone.utc),
     frozenset({"Coolbet", "Betano", "Unibet", "Marathonbet", "10Bet", "888Sport"})),
    # ACCESSIBLE-SET-VERIFY / bb39c6e8 (2026-09-05 00:20 EEST) — THE CUT
    (datetime(2026, 9, 4, 21, 20, tzinfo=timezone.utc),
     frozenset({"Coolbet", "Betano", "Unibet", "Unibet-Kambi", "Epicbet"})),
    # KAMBI-FEED-DIVERGENCE (2026-09-06 00:55 EEST)
    (datetime(2026, 9, 5, 21, 55, tzinfo=timezone.utc),
     frozenset({"Coolbet", "Betano", "Unibet", "Epicbet"})),
    # AF-UNIBET-PHANTOM (2026-09-14 14:23 EEST) — AF "Unibet" -> "Unibet-Site"
    (datetime(2026, 9, 14, 11, 23, tzinfo=timezone.utc),
     frozenset({"Coolbet", "Betano", "Unibet-Site", "Epicbet"})),
]

THE_CUT = datetime(2026, 9, 4, 21, 20, tzinfo=timezone.utc)
# OU-CALIBRATOR-DOMAIN-MISMATCH window (fit written -> rows deleted by mig. 335)
OU_BUG_START = datetime(2026, 9, 3, 10, 50, tzinfo=timezone.utc)
OU_BUG_END = datetime(2026, 9, 13, 20, 51, tzinfo=timezone.utc)


def as_was_set(when: datetime) -> frozenset:
    """The `ACCESSIBLE_BOOKMAKERS` value that was live at `when`."""
    out = _AS_WAS_TIMELINE[0][1]
    for ts, s in _AS_WAS_TIMELINE:
        if when >= ts:
            out = s
    return out


def registry_floors(market: str) -> tuple[float, float]:
    """(edge_floor, odds_floor) for the REAL-MONEY model bot on `market`, read
    from `workers/registry/bot_registry.py` — the single source of truth for what
    a bot is. Cross-checked against `coolbet_placer` so a drift between the two
    fails here rather than silently changing the backtest's answer."""
    want = {"1x2": "bot_coolbet_1x2_model_v1",
            "over_under_25": "bot_coolbet_ou_model_v1"}[market]
    spec = next(b for b in BOTS if b.name == want)
    if spec.edge_floor is None or spec.odds_floor is None:
        raise SystemExit(f"registry spec {want} has no floors")
    placer_odds = float(_min_odds_for(market))
    if abs(placer_odds - float(spec.odds_floor)) > 1e-9:
        raise SystemExit(
            f"DRIFT: registry odds floor {spec.odds_floor} for {want} disagrees "
            f"with coolbet_placer._min_odds_for({market}) = {placer_odds}. "
            "Reconcile before trusting any number from this script.")
    return float(spec.edge_floor), float(spec.odds_floor)


def v10_gate(market: str, selection: str, tier: int, data_tier: str | None,
             price: float, cal_prob: float, edge: float,
             pin_implied: float | None) -> bool:
    """The gate stack of `bot_v10_all` — the bot that actually publishes PICKS.

    Two gate stacks exist in this system and they answer differently, so the
    backtest runs BOTH rather than picking one:

      * the PLACER stack (`clears_edge_floor` + the registry odds floor) is what
        decides whether a pick is stakeable with real money at Coolbet. Its 2.80
        1x2 odds floor is a REALMONEY-ODDS-BAND finding, not a pick criterion.
      * THIS stack is `BOTS_CONFIG['bot_v10_all']` as read by
        `daily_pipeline_v2`: per-tier edge thresholds, the +5pp/+3pp bump at
        league tier >= 3 (TIER-C-T3PLUS-GATE-EXPAND), the data-tier bump, an
        odds RANGE of 1.30-4.50 and a 0.30 minimum probability — plus the
        Pinnacle disagreement veto and its mid-band rule, which are included
        here because both compare `cal_prob` (which moves with the book set)
        against a fixed sharp anchor, so omitting them would flatter the
        all-books arm.

    Everything is read from the live config; nothing is re-typed. What is NOT
    modelled: the odds-movement veto, PIN-CROSS-DRIFT, CAL-SHARP-GATE and the
    news path, none of which depend on which books we are allowed to read.
    """
    cfg = BOTS_CONFIG[PICKS_BOT]
    th = dict(cfg["edge_thresholds"].get(tier, {}))
    if tier >= 3 and th:
        th = {k: v + (0.05 if k.startswith("1x2") else 0.03) for k, v in th.items()}
    if market == "1x2":
        base = (th.get("1x2_fav", 0.05) if (selection == "home" and price < 2.0)
                else th.get("1x2_long", 0.08))
    else:
        base = th.get("ou", 0.05)
    me = base + DATA_TIER_EDGE_BUMP.get(data_tier or "A", 0.0)
    odds_min, odds_max = cfg["odds_range"]
    if edge < me or price < odds_min or price > odds_max:
        return False
    if cal_prob < cfg["min_prob"]:
        return False
    anchor = pin_implied if pin_implied is not None else 1.0 / price
    gap = cal_prob - anchor
    if pin_implied is not None and 0.06 <= gap < 0.10 and edge < me + 0.02:
        return False            # PIN-ANCHOR-GAP-MID-BAND
    if gap > PINNACLE_VETO_GAP:
        return False            # Pinnacle disagreement veto
    return True


# ── time-aware calibration ───────────────────────────────────────────────────
class CalibrationHistory:
    """Platt coefficients and shrinkage alphas AS OF a moment in time.

    `improvements` caches both in module globals and only ever loads the newest
    row per market. This class loads the whole fit history once (both the live
    table and the O/U backup migration 335 moved aside) and swaps the caches to
    the vintage in force at a given instant, so the real `calibrate_prob` runs
    with the real historical curve.
    """

    def __init__(self) -> None:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT market, platt_a, platt_b, platt_c, fitted_at
                 FROM model_calibration
               UNION ALL
               SELECT market, platt_a, platt_b, platt_c, fitted_at
                 FROM model_calibration_ou_domain_mismatch_backup
                ORDER BY fitted_at""", [])
        self._rows = [
            (r["fitted_at"], r["market"], float(r["platt_a"]),
             float(r["platt_b"]),
             float(r["platt_c"]) if r.get("platt_c") is not None else None)
            for r in rows if r.get("platt_a") is not None
        ]
        self._applied: datetime | None = None

    def apply(self, when: datetime) -> None:
        if self._applied == when:
            return
        platt: dict[str, tuple[float, float, float | None]] = {}
        alphas: dict[str, float] = {}
        for ts, mkt, a, b, c in self._rows:
            if ts > when:
                break
            if mkt.startswith("shrinkage_alpha_"):
                alphas[mkt] = a
            else:
                platt[mkt] = (a, b, c)
        _imp._platt_params = platt
        _imp._shrinkage_alphas = alphas
        self._applied = when


# ── price reconstruction ─────────────────────────────────────────────────────
def _clean_offers(rows: list[dict], freshest_ts: dict[str, datetime],
                  eval_at: dict[str, datetime]) -> dict[tuple, dict[str, float]]:
    """Apply the SHARED quality rules — the ones that are not a function of which
    books you are allowed to bet — and return {(match, market, selection): {book: odds}}.

    Order matters and mirrors `_load_today_from_db`:
      1. drop hard-excluded sources (never a real offer at any book set)
      2. ODDS-NO-MAX-AGE: a quote lagging the fixture's freshest quote by more
         than ODDS_MAX_LAG_HOURS is a dead feed, not an offer; ODDS_MAX_AGE_HOURS
         is the backstop for the pathological tail. `now()` in production becomes
         the evaluation instant here.
      3. OU-PIN-REQUIRED / OU-PINNACLE-CAP on over/under
      4. ODDS-OUTLIER-FILTER on 1x2, anchored on PRICE_REFERENCE_BOOKMAKERS
         (a REFERENCE set, deliberately wider than the placeable one)
    """
    by_key: dict[tuple, dict[str, tuple[float, datetime]]] = defaultdict(dict)
    for r in rows:
        bm = r["bookmaker"] or "unknown"
        if bm in HARD_EXCLUDED_SOURCES:
            continue
        mid = str(r["match_id"])
        ts = r["timestamp"]
        lag_h = (freshest_ts[mid] - ts).total_seconds() / 3600.0
        age_h = (eval_at[mid] - ts).total_seconds() / 3600.0
        if lag_h > ODDS_MAX_LAG_HOURS or age_h > ODDS_MAX_AGE_HOURS:
            continue
        try:
            odds = float(r["odds"])
        except (TypeError, ValueError):
            continue
        if odds <= 1.0:
            continue
        by_key[(mid, r["market"], r["selection"])][bm] = (odds, ts)

    out: dict[tuple, dict[str, float]] = {}
    for key, books in by_key.items():
        _mid, market, _sel = key
        offers = {b: o for b, (o, _t) in books.items()}
        if market.startswith("over_under_"):
            pin = offers.get("Pinnacle")
            if pin is None:
                continue          # OU-PIN-REQUIRED
            offers = {b: o for b, o in offers.items()
                      if b == "Pinnacle" or o <= 2.0 * pin}   # OU-PINNACLE-CAP
        mult = OUTLIER_MULT.get(market)
        if mult is not None:
            ref = {b: o for b, o in offers.items() if b in PRICE_REFERENCE_BOOKMAKERS}
            anchor = ref.get("Pinnacle")
            if anchor is None:
                if len(ref) < OUTLIER_MIN_BOOKS:
                    continue      # no anchor can be formed — reject unvalidated
                vals = sorted(ref.values())
                n = len(vals)
                anchor = (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2)
            ceiling = anchor * mult
            offers = {b: o for b, o in offers.items()
                      if b == "Pinnacle" or o <= ceiling}
        if offers:
            out[key] = offers
    return out


def _outcome(market: str, selection: str, m: dict) -> int | None:
    if market == "1x2":
        res = m.get("result")
        return None if res is None else int(str(res) == selection)
    sh, sa = m.get("score_home"), m.get("score_away")
    if sh is None or sa is None:
        return None
    total = int(sh) + int(sa)
    return int(total > 2.5) if selection == "over" else int(total < 2.5)


def build_rows(start: date, end: date, lead_hours: float,
               progress: bool = True) -> list[dict]:
    """One row per (fixture, market, selection) candidate, carrying the best
    price and the gate decision under EVERY arm. Built day by day so the odds
    query stays bounded — `odds_snapshots` runs to millions of rows per day."""
    from workers.api_clients.db import execute_query

    cal = CalibrationHistory()
    arms = ("as_was", "restricted", "all_books")
    rows_out: list[dict] = []

    day = start
    while day <= end:
        nxt = day + timedelta(days=1)
        matches = execute_query(
            """SELECT m.id::text AS id, m.date, m.result, m.score_home, m.score_away,
                      COALESCE(l.tier, 3) AS tier
                 FROM matches m LEFT JOIN leagues l ON l.id = m.league_id
                WHERE m.date >= %s AND m.date < %s
                  AND m.status = 'finished' AND m.result IS NOT NULL""",
            (f"{day}T00:00:00Z", f"{nxt}T00:00:00Z"))
        if not matches:
            day = nxt
            continue
        mids = [m["id"] for m in matches]
        mmap = {m["id"]: m for m in matches}
        eval_at = {m["id"]: m["date"] - timedelta(hours=lead_hours) for m in matches}

        # Raw ensemble probability as of the evaluation instant. `predictions`
        # holds the pipeline's OWN ensemble output (verified: it equals
        # simulated_bets.model_probability on picked rows), i.e. the raw prob
        # that goes into calibrate_prob — not a second model.
        preds = execute_query(
            """SELECT DISTINCT ON (p.match_id, p.market)
                      p.match_id::text AS mid, p.market, p.model_probability::float AS praw
                 FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.match_id = ANY(%s::uuid[]) AND p.source = 'ensemble'
                  AND p.model_probability IS NOT NULL
                  AND p.created_at <= m.date - (%s * INTERVAL '1 hour')
                ORDER BY p.match_id, p.market, p.created_at DESC""",
            (mids, lead_hours))
        pmap = {(r["mid"], r["market"]): r["praw"] for r in preds}

        # data_tier drives the pipeline's edge bump (A none / B +2pp / C +8pp).
        dt = execute_query(
            """SELECT match_id::text AS mid, data_tier
                 FROM match_feature_vectors WHERE match_id = ANY(%s::uuid[])""",
            (mids,))
        dtmap = {r["mid"]: r["data_tier"] for r in dt}

        pins = execute_query(
            """SELECT DISTINCT ON (s.match_id, s.signal_name)
                      s.match_id::text AS mid, s.signal_name, s.signal_value::float AS v
                 FROM match_signals s JOIN matches m ON m.id = s.match_id
                WHERE s.match_id = ANY(%s::uuid[])
                  AND s.signal_name = ANY(%s)
                  AND s.captured_at <= m.date - (%s * INTERVAL '1 hour')
                ORDER BY s.match_id, s.signal_name, s.captured_at DESC""",
            (mids, list(set(PIN_SIGNAL.values())), lead_hours))
        pinmap = {(r["mid"], r["signal_name"]): r["v"] for r in pins}

        odds_rows = execute_query(
            """SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
                      o.match_id::text AS match_id, o.market, o.selection,
                      o.odds, o.bookmaker, o.timestamp
                 FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                WHERE o.match_id = ANY(%s::uuid[])
                  AND o.is_closing = false AND o.is_live = false
                  AND o.market = ANY(%s)
                  AND o.timestamp <= m.date - (%s * INTERVAL '1 hour')
                ORDER BY o.match_id, o.market, o.selection, o.bookmaker,
                         o.timestamp DESC""",
            (mids, list(MARKETS), lead_hours))

        freshest: dict[str, datetime] = {}
        for r in odds_rows:
            mid = str(r["match_id"])
            ts = r["timestamp"]
            if mid not in freshest or ts > freshest[mid]:
                freshest[mid] = ts
        offers = _clean_offers(odds_rows, freshest, eval_at)

        for (mid, market, selection), books in offers.items():
            praw = pmap.get((mid, PRED_KEY[(market, selection)]))
            if praw is None:
                continue
            m = mmap[mid]
            y = _outcome(market, selection, m)
            if y is None:
                continue
            when = eval_at[mid]
            cal.apply(when)
            pin_anchor = pinmap.get((mid, PIN_SIGNAL[(market, selection)]))
            e_floor_reg, o_floor = registry_floors(market)

            rec: dict = {
                "match_id": mid, "market": market, "selection": selection,
                "kickoff": m["date"].isoformat(), "eval_at": when.isoformat(),
                "tier": int(m["tier"] or 3), "praw": praw, "y": y,
                "data_tier": dtmap.get(mid),
                "pin_implied": pin_anchor, "n_books_all": len(books),
                "registry_edge_floor": e_floor_reg, "registry_odds_floor": o_floor,
            }
            for arm in arms:
                allowed = {"as_was": as_was_set(when),
                           "restricted": ACCESSIBLE_BOOKMAKERS,
                           "all_books": None}[arm]
                sub = books if allowed is None else {b: o for b, o in books.items()
                                                     if b in allowed}
                if not sub:
                    rec[arm] = None
                    continue
                book, price = max(sub.items(), key=lambda kv: kv[1])
                cal_prob = _imp.calibrate_prob(
                    praw, 1.0 / price, tier=int(m["tier"] or 3),
                    market=f"{market}_{selection}",
                    anchor_implied=pin_anchor, odds=price)
                if math.isnan(cal_prob):
                    rec[arm] = None
                    continue
                edge = cal_prob - 1.0 / price
                clears = (clears_edge_floor(market, selection, price, edge)
                          and price >= o_floor)
                veto = (pin_anchor is not None
                        and (cal_prob - pin_anchor) > PINNACLE_VETO_GAP)
                rec[arm] = {
                    "book": book, "price": price, "cal_prob": cal_prob,
                    "edge": edge, "clears": bool(clears), "pin_veto": bool(veto),
                    "clears_v10": v10_gate(market, selection, int(m["tier"] or 3),
                                           dtmap.get(mid), price, cal_prob, edge,
                                           pin_anchor),
                    "floor": min_edge_for_pick(market, selection, price),
                    "n_books": len(sub),
                }
            rows_out.append(rec)
        if progress:
            print(f"  {day}: {len(matches)} settled fixtures, "
                  f"{len(offers)} priced selections", file=sys.stderr)
        day = nxt
    return rows_out


# ── reporting ────────────────────────────────────────────────────────────────
def _pct(x: float | None) -> str:
    return "  n/a " if x is None else f"{100 * x:6.2f}"


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarise(picks: list[dict], days: int) -> dict:
    """Volume / price / edge / calibration gap / flat-stake ROI for one cell."""
    n = len(picks)
    if n == 0:
        return {"n": 0, "per_day": 0.0}
    prices = [p["price"] for p in picks]
    edges = [p["edge"] for p in picks]
    probs = [p["cal_prob"] for p in picks]
    wins = [p["y"] for p in picks]
    pnl = sum((p["price"] - 1.0) * 10.0 if p["y"] else -10.0 for p in picks)
    wr = sum(wins) / n
    mean_p = sum(probs) / n
    # Binomial SE on the realised win rate — the only honest way to say whether a
    # calibration gap is a finding or a small sample (ANALYSIS_GOTCHAS #39).
    se = math.sqrt(max(mean_p * (1 - mean_p), 1e-9) / n)
    return {
        "n": n, "per_day": n / days if days else 0.0,
        "mean_price": sum(prices) / n, "median_price": _median(prices),
        "mean_edge": sum(edges) / n, "median_edge": _median(edges),
        "mean_cal_prob": mean_p, "win_rate": wr,
        "cal_gap": wr - mean_p, "cal_gap_se": se, "cal_gap_z": (wr - mean_p) / se,
        "roi": pnl / (10.0 * n), "pnl": pnl,
    }


def _table(title: str, cells: list[tuple[str, dict]]) -> str:
    out = [f"\n{title}",
           "  arm/segment                    n   /day   meanOdds  meanEdge  "
           "calProb   winRate   gap(pp)     z    ROI@€10"]
    for label, s in cells:
        if s["n"] == 0:
            out.append(f"  {label:<28} {0:5d}  {0:5.2f}       —         —"
                       "        —         —         —       —        —")
            continue
        out.append(
            f"  {label:<28} {s['n']:5d}  {s['per_day']:5.2f}   "
            f"{s['mean_price']:7.3f}   {_pct(s['mean_edge'])}   "
            f"{_pct(s['mean_cal_prob'])}   {_pct(s['win_rate'])}   "
            f"{100 * s['cal_gap']:+7.2f}  {s['cal_gap_z']:+5.2f}  "
            f"{_pct(s['roi'])}")
    return "\n".join(out)


def _clears(a: dict, profile: str) -> bool:
    """Did this arm's best price clear the gate stack named by `profile`?"""
    if profile == "v10":
        return bool(a["clears_v10"])
    return bool(a["clears"]) and not a["pin_veto"]


def _picks(rows: list[dict], arm: str, profile: str) -> list[dict]:
    out = []
    for r in rows:
        a = r.get(arm)
        if not a or not _clears(a, profile):
            continue
        out.append({**a, "y": r["y"], "market": r["market"],
                    "selection": r["selection"], "eval_at": r["eval_at"],
                    "pin_implied": r["pin_implied"], "tier": r["tier"]})
    return out


PROFILE_LABEL = {
    "v10": "bot_v10_all gate stack (the bot that publishes PICKS)",
    "placer": "placer/registry floors (edge 10%/8% + odds 2.80/1.80) + Pinnacle veto",
}


def report(rows: list[dict], start: date, end: date, profile: str) -> str:
    days_total = (end - start).days + 1
    out: list[str] = []
    out.append(f"Window {start} → {end} ({days_total}d), "
               f"{len(rows)} settled priced candidates")
    out.append(f"GATE PROFILE: {profile} — {PROFILE_LABEL[profile]}")
    out.append(f"restricted set = {sorted(ACCESSIBLE_BOOKMAKERS)}")
    out.append(f"excluded from every arm = {sorted(HARD_EXCLUDED_SOURCES)}")

    def seg(rs, pred):
        return [r for r in rs if pred(r)]

    before = seg(rows, lambda r: datetime.fromisoformat(r["eval_at"]) < THE_CUT)
    after = seg(rows, lambda r: datetime.fromisoformat(r["eval_at"]) >= THE_CUT)
    d_before = (THE_CUT.date() - start).days + 1
    d_after = (end - THE_CUT.date()).days + 1

    for market in MARKETS:
        for label, subset, d in (("BEFORE the cut (→09-04)", before, d_before),
                                 ("AFTER the cut (09-05→)", after, d_after)):
            rs = [r for r in subset if r["market"] == market]
            cells = [(arm, summarise(_picks(rs, arm, profile), d))
                     for arm in ("as_was", "restricted", "all_books")]
            out.append(_table(f"[{market}] {label}   "
                              f"({len(rs)} candidates over {d}d)", cells))

    # O/U calibrator regimes — the #065 confound, split explicitly rather than
    # pooled across a window that contains the regime change (gotcha #39).
    ou = [r for r in rows if r["market"] == "over_under_25"]
    regimes = [
        ("O/U pre-curve (→09-03 10:50)",
         [r for r in ou if datetime.fromisoformat(r["eval_at"]) < OU_BUG_START],
         (OU_BUG_START.date() - start).days + 1),
        ("O/U domain-mismatch curve",
         [r for r in ou if OU_BUG_START <= datetime.fromisoformat(r["eval_at"]) < OU_BUG_END],
         (OU_BUG_END.date() - OU_BUG_START.date()).days + 1),
        ("O/U curve deleted (09-13→)",
         [r for r in ou if datetime.fromisoformat(r["eval_at"]) >= OU_BUG_END],
         (end - OU_BUG_END.date()).days + 1),
    ]
    for label, rs, d in regimes:
        cells = [(arm, summarise(_picks(rs, arm, profile), max(d, 1)))
                 for arm in ("as_was", "restricted", "all_books")]
        out.append(_table(f"[O/U regime] {label}   ({len(rs)} candidates)", cells))

    out.append(selection_effect(rows, profile))
    return "\n".join(out)


def price_only(rows: list[dict]) -> str:
    """The book set's effect on PRICE and COVERAGE, before any gate.

    This is the only part of the exercise that is free of selection: it compares
    the two arms on the SAME candidates, so it cannot be moved by which picks
    each arm happens to raise. Everything downstream inherits these two numbers —
    a published edge is a price, and a selection with no price in the restricted
    set cannot be published at all.
    """
    out = ["\n" + "=" * 78,
           "PRICE AND COVERAGE — same candidates, no gate, no selection",
           "=" * 78]
    for market in MARKETS:
        rs = [r for r in rows if r["market"] == market]
        for label, cut in (("→ 09-04 (before)", lambda r: datetime.fromisoformat(r["eval_at"]) < THE_CUT),
                           ("09-05 → (after)", lambda r: datetime.fromisoformat(r["eval_at"]) >= THE_CUT)):
            sub = [r for r in rs if cut(r)]
            if not sub:
                continue
            both = [r for r in sub if r.get("restricted") and r.get("all_books")]
            no_price = sum(1 for r in sub if not r.get("restricted"))
            gains = [r["all_books"]["price"] / r["restricted"]["price"] - 1.0 for r in both]
            better = [g for g in gains if g > 1e-9]
            winners: dict[str, int] = defaultdict(int)
            for r in both:
                if r["all_books"]["price"] > r["restricted"]["price"] + 1e-9:
                    winners[r["all_books"]["book"]] += 1
            top = ", ".join(f"{b} {n}" for b, n in
                            sorted(winners.items(), key=lambda kv: -kv[1])[:6])
            out.append(
                f"\n  [{market}] {label}: {len(sub)} candidates; "
                f"{no_price} ({100 * no_price / len(sub):.1f}%) have NO price in the "
                f"restricted set at all")
            out.append(
                f"    on the {len(both)} co-priced: mean best price "
                f"{sum(r['restricted']['price'] for r in both) / len(both):.3f} → "
                f"{sum(r['all_books']['price'] for r in both) / len(both):.3f} "
                f"({100 * sum(gains) / len(gains):+.2f}% mean uplift; "
                f"{len(better)} ({100 * len(better) / len(both):.1f}%) strictly better)")
            out.append(f"    better price came from: {top or '—'}")
    return "\n".join(out)


def selection_effect(rows: list[dict], profile: str) -> str:
    """THE HYPOTHESIS (#005 x #065).

    A thinner book set lowers the best price, so `edge = cal_prob - 1/odds`
    shrinks and fewer candidates clear the floor. The claim under test is that
    the survivors are not a random sample of the wide-set picks: to clear a floor
    at a WORSE price you need a HIGHER `cal_prob` relative to the market, so the
    restriction would select for exactly the region where the model is most
    overconfident — making the book-set cut a partial cause of the ACCURACY
    collapse, not only the volume collapse.

    The test partitions the all-books picks into
      BOTH  — cleared under the restricted set too (the picks we actually made)
      LOST  — cleared only with the wider set (the picks the cut destroyed)
    and compares, in each group, the model's distance from the sharp line
    (`cal_prob - pinnacle_implied`) and the realised calibration gap
    (`win_rate - mean cal_prob`). If the hypothesis holds, BOTH sits further
    above the market AND realises a worse gap than LOST.
    """
    out = ["\n" + "=" * 78,
           "SELECTION-EFFECT TEST — does the thin book set pick the model's "
           "overconfident tail?", "=" * 78]
    for market in MARKETS:
        rs = [r for r in rows if r["market"] == market]
        both, lost = [], []
        for r in rs:
            a, b = r.get("all_books"), r.get("restricted")
            if not a or not _clears(a, profile):
                continue
            rec = {**a, "y": r["y"], "pin_implied": r["pin_implied"],
                   "market": market, "selection": r["selection"],
                   "eval_at": r["eval_at"]}
            (both if (b and _clears(b, profile)) else lost).append(rec)
        s_both, s_lost = summarise(both, 1), summarise(lost, 1)
        cells = [("cleared under BOTH", s_both),
                 ("cleared ONLY all-books", s_lost)]
        out.append(_table(f"[{market}] all-books picks, partitioned", cells))
        for label, grp in (("BOTH", both), ("ONLY all-books", lost)):
            gaps = [g["cal_prob"] - g["pin_implied"] for g in grp
                    if g.get("pin_implied") is not None]
            if gaps:
                out.append(f"    {label:<16} model-minus-Pinnacle: "
                           f"mean {100 * sum(gaps) / len(gaps):+.2f}pp, "
                           f"median {100 * _median(gaps):+.2f}pp  (n={len(gaps)})")
            else:
                out.append(f"    {label:<16} model-minus-Pinnacle: no Pinnacle anchor")
        # The hypothesis is a DIFFERENCE between the two groups, so it must be
        # tested as one. A gap that is negative in both groups says the model is
        # overconfident; only a gap that is MORE negative among the restricted
        # survivors supports the selection-effect claim.
        if s_both["n"] and s_lost["n"]:
            d = s_both["cal_gap"] - s_lost["cal_gap"]
            se = math.sqrt(s_both["cal_gap_se"] ** 2 + s_lost["cal_gap_se"] ** 2)
            z = d / se
            sign = ("negative — the DIRECTION the hypothesis predicts" if d < 0
                    else "positive — the OPPOSITE direction to the hypothesis")
            # MEASURED 2026-09-22: this sign FLIPS between --lead-hours 6 and 12
            # in 3 of 4 cells, and no cell reaches |z| = 1.96. Never quote one
            # run's direction as a verdict — run at least two leads and compare.
            note = ("NOT SIGNIFICANT — and this sign is known to flip with "
                    "--lead-hours; run a second lead before concluding anything"
                    if abs(z) < 1.96 else "significant at |z| >= 1.96")
            out.append(f"    Δgap(BOTH − ONLY) = {100 * d:+.2f}pp, z = {z:+.2f} "
                       f"(n={s_both['n']} vs {s_lost['n']}) — {sign}; {note}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-08-15")
    ap.add_argument("--end", default="2026-09-21")
    ap.add_argument("--lead-hours", type=float, default=6.0,
                    help="evaluation instant = kickoff - this (default 6, the "
                         "median lead of the real bot_v10_all 1x2 picks)")
    ap.add_argument("--profile", choices=("v10", "placer", "both"),
                    default="both",
                    help="which gate stack decides a pick (default: report both)")
    ap.add_argument("--json", help="write the per-candidate rows here")
    ap.add_argument("--from-json", help="re-report a previous --json dump "
                                        "instead of re-querying the DB")
    a = ap.parse_args()

    start = date.fromisoformat(a.start)
    end = date.fromisoformat(a.end)
    if a.from_json:
        rows = json.loads(Path(a.from_json).read_text())
    else:
        rows = build_rows(start, end, a.lead_hours)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, default=str))
    print(price_only(rows))
    profiles = ("v10", "placer") if a.profile == "both" else (a.profile,)
    for p in profiles:
        print("\n" + "#" * 78)
        print(report(rows, start, end, p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
