"""ANCHOR RESOLVER ([[#113]] ANCHOR-WIDENING, 2026-09-23) — ONE answer to "what is the
fair price of this market right now, and what is that answer based on?"

WHY THIS EXISTS
---------------
Every check that needs a fair price read AF-Pinnacle, so on the ~41% of priced
fixtures with no Pinnacle quote they went dark (clv_sharp NULL, sharp triggers
silent, PIN-veto and anchor sanity failing open). Four separate consensus
implementations had grown meanwhile (publisher, promo_ev, mirror_guard,
daily_pipeline_v2), each with a different defect.

Measured before building (scripts/anchor_consensus_composition.py, 120 d, 24,625
finished fixtures): on the 19,697 fixtures WITH Pinnacle, a de-vigged consensus
ties AF-Pinnacle on outcome log-loss — consensus of every book, a 5-book panel,
5 random books, even the 5 SOFTEST books (all |t| < 1). So a consensus is a
legitimate anchor. Outcome log-loss cannot see a 1–2% price error, though, so
every result carries its SOURCE and consumers that stake money keep their own
validation gate before trusting a consensus.

ORDER OF PREFERENCE (first that forms wins)
  1. pinnacle_tight  Pinnacle complete set, fresh (<= PIN_MAX_AGE_MIN), overround
                     <= PIN_TIGHT_OVERROUND — a real line, not a goodwill quote.
  2. consensus       >= min_books (default 5) books, each a complete set from ONE
                     fetch, all fresh and within a common window, ratio-guarded,
                     Shin-de-vigged, probabilities averaged and renormalised.
  3. pinnacle_wide   fresh Pinnacle that is not tight — better than nothing, labelled.
  4. consensus_thin  3–4 books; only when the caller passes min_thin_books <= 4.
  5. none            no anchor. Never guess.

DEFECTS OF THE OLDER IMPLEMENTATIONS, FIXED HERE (ANALYSIS_GOTCHAS refs)
  * §62 latest-row-per-LEG mixes fetches → one complete set per book from a single
    fetch (all legs within SET_TOLERANCE_S of each other).
  * §65 each book's own latest quote smears time → every member within
    COMMON_WINDOW_MIN of the newest member and within max_age_min of `at`.
  * §9/§68 no outlier guard → a book whose any leg is > GUARD_RATIO from the
    leave-one-out median of the others is dropped (home/away inversions).
  * the book being priced is never part of its own anchor (`exclude_book`).
  * Coolbet is out of the anchor set — the one book measured WORSE than Pinnacle
    on outcomes (ΔLL +0.0058, t +2.94, docs/PUBLISHED_PICKS_GRADING_2026_09_23.md).

WHY EQUAL WEIGHT ([[#116]], 2026-09-24): accuracy-, margin- and de-correlation-weighted
means, a trimmed mean and a median were tested against this mean on held-out data
(scripts/anchor_weighting_research.py, 30 tests, Holm). None beat it on outcome log-loss;
the best closing-line gain was 0.03–0.04 pp. Equal weight stays — ANALYSIS_GOTCHAS §74.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median

PIN = "Pinnacle"
PIN_MAX_AGE_MIN = 60           # AF-Pinnacle matches the site to +0.02 pp when < 1 h old
PIN_TIGHT_OVERROUND = 0.04     # the pre-registered ≤4% gate of the published sharp arm
DEFAULT_MAX_AGE_MIN = 180
COMMON_WINDOW_MIN = 120
SET_TOLERANCE_S = 120
GUARD_RATIO = 1.5625           # 1.25² — kills inversions/data faults, not opinions
# Aggregates, retired or unplaceable-and-divergent feeds, and the one book measured
# worse than Pinnacle. Never anchor members.
NEVER_IN_ANCHOR = frozenset({"Max", "Avg", "Betfair Exchange", "BetWin", "Betfred",
                             "Unibet", "Unibet-Kambi", "Coolbet", "Coolbet-OddsAPI"})
# Same platform, same prices: counting both would double one opinion.
SKIN_OF = {"20bet": "Tonybet", "X3000": "Paf", "Speedybet": "Paf"}

MARKET_SIDES = {"1x2": ("home", "draw", "away"), "1x2_1h": ("home", "draw", "away"),
                "btts": ("yes", "no"),
                "double_chance": ("1x", "12", "x2")}



# ── SHARP-ANCHOR-MAX-AGE (#162 W8.3, 2026-09-25): ONE staleness rule for a Pinnacle line used as fair value.
# The two sharp producers (pick_generator._candidates_from_sharp, pick_triggers sharp strategies) de-vigged
# the LATEST Pinnacle line with no age limit, on paths that can feed real money. A line is too old when ANY
# side is older than SHARP_ANCHOR_MAX_AGE_H, or older than SHARP_ANCHOR_NEAR_KO_MAX_AGE_H within
# SHARP_ANCHOR_NEAR_KO_H of kick-off (where a pulled/moved market makes the "fair" price fiction).
# 7 h, not 6: tomorrow's fixtures are fetched at 10:00 and 16:00 UTC, a 6 h gap (review 2026-09-25).
# Measured that day: ~1/3 of upcoming lines were < 2 h old and ~2/3 at 2-4 h (normal cadence); this
# drops 2 per market that are > 24 h old and ~10 per market that are 2 h+ old inside 12 h of kick-off.
SHARP_ANCHOR_MAX_AGE_H = 7.0
SHARP_ANCHOR_NEAR_KO_H = 12.0
SHARP_ANCHOR_NEAR_KO_MAX_AGE_H = 2.0


def anchor_line_too_old(oldest_side_age_h: float, ko_in_h: float) -> bool:
    """True when a sharp anchor line must not be used as fair value (see SHARP_ANCHOR_MAX_AGE above)."""
    return oldest_side_age_h > SHARP_ANCHOR_MAX_AGE_H or (
        ko_in_h < SHARP_ANCHOR_NEAR_KO_H and oldest_side_age_h > SHARP_ANCHOR_NEAR_KO_MAX_AGE_H)

def market_sides(market: str) -> tuple[str, ...] | None:
    m = (market or "").lower()
    if m in MARKET_SIDES:
        return None if m == "double_chance" else MARKET_SIDES[m]   # DC legs overlap: not a de-viggable set
    if m.startswith(("over_under", "team_total", "corners_ou")):
        return ("over", "under")
    return None


@dataclass
class Anchor:
    source: str                      # pinnacle_tight | consensus | pinnacle_wide | consensus_thin | none
    probs: dict = field(default_factory=dict)       # side -> fair probability
    n_books: int = 0
    books: dict = field(default_factory=dict)       # book -> {side: prob}
    dropped: dict = field(default_factory=dict)     # book -> reason
    anchor_ts: datetime | None = None               # newest member quote
    max_age_min: float | None = None                # oldest member, minutes before `at`
    spread: float | None = None                     # max over sides of (max−min member prob)
    overround: float | None = None                  # Pinnacle's, when a Pinnacle tier

    @property
    def ok(self) -> bool:
        return self.source != "none"

    def prob(self, side: str) -> float | None:
        return self.probs.get((side or "").lower())

    def fair_odds(self, side: str) -> float | None:
        p = self.prob(side)
        return 1.0 / p if p else None


def _overround(odds: list[float]) -> float:
    return sum(1.0 / o for o in odds) - 1.0


def _guard(sets: dict[str, list[float]]) -> tuple[dict, dict]:
    """Drop a book whose any leg is > GUARD_RATIO from the median of the OTHERS."""
    if len(sets) < 3:
        return sets, {}
    keep, dropped = {}, {}
    for b, q in sets.items():
        others = [v for o, v in sets.items() if o != b]
        med = [median(o[i] for o in others) for i in range(len(q))]
        worst = max(max(q[i] / med[i], med[i] / q[i]) for i in range(len(q)))
        if worst <= GUARD_RATIO:
            keep[b] = q
        else:
            dropped[b] = f"outlier x{worst:.2f} vs others"
    return keep, dropped


def compute_anchor(sets: dict[str, tuple[list[float], datetime]], sides: tuple[str, ...], *,
                   at: datetime, exclude_book: str | None = None, min_books: int = 5,
                   min_thin_books: int = 99, max_age_min: float = DEFAULT_MAX_AGE_MIN) -> Anchor:
    """Pure core. `sets` = {book: ([odds in `sides` order], fetch_ts)} — one complete set
    per book from a single fetch. No DB access; unit-testable."""
    from workers.model.devig import devig
    dropped: dict = {}

    def age(ts):
        return (at - ts).total_seconds() / 60.0

    # 1 — Pinnacle, tight and fresh
    pin = sets.get(PIN)
    pin_probs = pin_or = None
    if pin and exclude_book != PIN and age(pin[1]) <= PIN_MAX_AGE_MIN:
        pin_probs = devig(pin[0])
        pin_or = _overround(pin[0])
        if pin_probs and pin_or <= PIN_TIGHT_OVERROUND:
            return Anchor("pinnacle_tight", dict(zip(sides, pin_probs)), 1,
                          {PIN: dict(zip(sides, pin_probs))}, {}, pin[1], round(age(pin[1]), 1),
                          0.0, pin_or)

    # 2 — consensus
    members: dict[str, tuple[list[float], datetime]] = {}
    seen_platform: set = set()
    for b, (q, ts) in sets.items():
        if b in NEVER_IN_ANCHOR or (exclude_book and exclude_book in (b, SKIN_OF.get(b))):
            continue
        platform = SKIN_OF.get(b, b)
        if platform in seen_platform:
            dropped[b] = f"skin of {platform}"
            continue
        if age(ts) > max_age_min or age(ts) < -1:
            dropped[b] = f"stale ({age(ts):.0f} min)"
            continue
        seen_platform.add(platform)
        members[b] = (q, ts)
    if members:
        newest = max(ts for _, ts in members.values())
        for b in list(members):
            if (newest - members[b][1]).total_seconds() / 60.0 > COMMON_WINDOW_MIN:
                dropped[b] = "outside common window"
                del members[b]
    kept, outl = _guard({b: q for b, (q, _) in members.items()})
    dropped.update(outl)
    per_book = {}
    for b, q in kept.items():
        p = devig(q)
        if p and min(p) > 0:
            per_book[b] = p
    need = min(min_books, min_thin_books)
    if len(per_book) >= need:
        m = [sum(p[i] for p in per_book.values()) / len(per_book) for i in range(len(sides))]
        s = sum(m)
        probs = [x / s for x in m]
        ts_list = [members[b][1] for b in per_book]
        spread = max(max(p[i] for p in per_book.values()) - min(p[i] for p in per_book.values())
                     for i in range(len(sides)))
        src = "consensus" if len(per_book) >= min_books else None
        if src or pin_probs is None:
            return Anchor(src or "consensus_thin", dict(zip(sides, probs)), len(per_book),
                          {b: dict(zip(sides, p)) for b, p in per_book.items()}, dropped,
                          max(ts_list), round(max(age(t) for t in ts_list), 1), round(spread, 4))

    # 3 — Pinnacle wide but fresh
    if pin_probs:
        return Anchor("pinnacle_wide", dict(zip(sides, pin_probs)), 1,
                      {PIN: dict(zip(sides, pin_probs))}, dropped, pin[1], round(age(pin[1]), 1),
                      0.0, pin_or)
    # 4 — thin consensus (only reached when requested and no Pinnacle)
    return Anchor("none", dropped=dropped)


def sets_from_rows(rows: list[dict], sides: tuple[str, ...]) -> dict:
    """Pure: rows of ONE match+market ({bookmaker, sel, odds, timestamp}) → the latest
    COMPLETE set per book whose legs all come from one fetch (within SET_TOLERANCE_S)."""
    by_book: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: r["timestamp"], reverse=True):
        by_book.setdefault(r["bookmaker"], []).append(r)
    out = {}
    for book, rs in by_book.items():
        # newest-first; the first timestamp at which every side is present within
        # SET_TOLERANCE_S is that book's latest complete fetch
        for head in rs:
            legs = {}
            for r in rs:
                if abs((r["timestamp"] - head["timestamp"]).total_seconds()) <= SET_TOLERANCE_S:
                    legs.setdefault(r["sel"], r["odds"])
            if all(x in legs for x in sides):
                out[book] = ([legs[x] for x in sides], head["timestamp"])
                break
    return out


def load_sets(match_id: str, market: str, sides: tuple[str, ...], *, at: datetime,
              lookback_min: float = DEFAULT_MAX_AGE_MIN) -> dict:
    """Latest COMPLETE set per book at or before `at` (and before kickoff), all legs
    from one fetch (within SET_TOLERANCE_S)."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT o.bookmaker, lower(o.selection) AS sel, o.odds::float AS odds, o.timestamp
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.match_id = %s AND o.market = %s AND COALESCE(o.is_live, false) = false
              AND o.odds > 1.01 AND o.timestamp <= LEAST(%s, m.date)
              AND o.timestamp > %s - make_interval(mins => %s)
              AND lower(o.selection) = ANY(%s)
            ORDER BY o.bookmaker, o.timestamp DESC""",
        (match_id, market, at, at, int(lookback_min), list(sides))) or []
    return sets_from_rows(rows, sides)


def resolve_anchor(match_id: str, market: str, *, at: datetime | None = None,
                   exclude_book: str | None = None, min_books: int = 5,
                   min_thin_books: int = 99, max_age_min: float = DEFAULT_MAX_AGE_MIN,
                   use_exchange: bool | None = None) -> Anchor:
    """The one entry point. `at` defaults to now (capped at kickoff by the query).
    Consumers MUST record `anchor.source` next to whatever they compute from it.
    `use_exchange` (default: env ANCHOR_USE_EXCHANGE, off) tries the sharp tier —
    Pinnacle + Betfair Exchange — first; a `sharp_conflict` is returned as-is (no
    probabilities), never silently replaced by a softer anchor."""
    import os
    sides = market_sides(market)
    if not sides:
        return Anchor("none", dropped={"_": f"market {market!r} has no de-viggable set"})
    at = at or datetime.now(timezone.utc)
    if use_exchange is None:
        use_exchange = os.getenv("ANCHOR_USE_EXCHANGE", "0") == "1"
    if use_exchange:
        sharp = resolve_sharp(match_id, market, at=at)
        if sharp.source != "none":
            return sharp
    sets = load_sets(match_id, market, sides, at=at, lookback_min=max_age_min)
    return compute_anchor(sets, sides, at=at, exclude_book=exclude_book, min_books=min_books,
                          min_thin_books=min_thin_books, max_age_min=max_age_min)



# ── SHARP TIER: Pinnacle + Betfair Exchange ([[#119]] SHARP-ANCHOR-V2, 2026-09-24) ──
#
# Owner: "whichever of those 2 provides odds, this means we have a sharp anchor, and
# consensus of both is a plus" — and every backtest should be able to lean on the
# exchange as much as on Pinnacle. Two independent sharp prices:
#   sharp_blend     both present and within SHARP_DISAGREE of each other → their mean
#   pinnacle_tight  only Pinnacle (fresh + tight)
#   exchange_liquid only a LIQUID exchange market (spread ≤5% every runner, ≥€1k matched)
#   sharp_conflict  both present but they disagree → NO probabilities; the flag is the
#                   point: a stale AF-Pinnacle quote or a placeholder line looks exactly
#                   like the biggest edge on the board (the phantom-edge class).
#   none
# `resolve_anchor(..., use_exchange=True)` tries this tier first. It is OFF by default
# (env ANCHOR_USE_EXCHANGE=1 turns it on globally) until the #117 study says the
# exchange is sharp enough near the close — no existing consumer changes behaviour.

EXCHANGE = "Betfair-Exchange"
EXCHANGE_MAX_AGE_MIN = 30          # the reader runs every 15 min
SHARP_DISAGREE = 0.025             # max-side prob gap; liquid ex-vs-Pin measured mean 1.03 pts (#117)
_EX_MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under"),
               "btts": ("yes", "no")}


def exchange_fair(quotes: dict[str, dict], sides: tuple[str, ...], *,
                  max_spread: float = 0.05, min_matched: float = 1000.0) -> tuple[dict | None, bool, dict]:
    """Pure. quotes: side -> {back, lay, market_matched}. Returns (probs, liquid, info).
    Fair price per runner = harmonic mid of best back and lay, normalised (an exchange
    book is ~100% so proportional normalisation is the de-vig)."""
    info: dict = {}
    if not all(s in quotes for s in sides):
        return None, False, {"reason": "incomplete market"}
    mids, spreads = [], []
    for s in sides:
        b, l = quotes[s].get("back"), quotes[s].get("lay")
        if not b or not l or b <= 1.0 or l < b:
            return None, False, {"reason": f"no two-sided price on {s}"}
        mids.append(2.0 / (1.0 / b + 1.0 / l))
        spreads.append(l / b - 1.0)
    inv = [1.0 / m for m in mids]
    tot = sum(inv)
    probs = {s: v / tot for s, v in zip(sides, inv)}
    matched = float(quotes[sides[0]].get("market_matched") or 0.0)
    liquid = max(spreads) <= max_spread and matched >= min_matched
    info.update(max_spread=round(max(spreads), 4), market_matched=round(matched), book_sum=round(tot, 4))
    return probs, liquid, info


def compute_sharp(pin: tuple[list[float], datetime] | None, ex: dict | None,
                  sides: tuple[str, ...], *, at: datetime) -> Anchor:
    """Pure. `pin` = (odds, ts) Pinnacle set; `ex` = {"probs", "liquid", "ts", "info"}."""
    from workers.model.devig import devig
    pin_probs = pin_or = None
    if pin and (at - pin[1]).total_seconds() / 60 <= PIN_MAX_AGE_MIN:
        p = devig(pin[0])
        if p:
            pin_or = _overround(pin[0])
            if pin_or <= PIN_TIGHT_OVERROUND:
                pin_probs = dict(zip(sides, p))
    ex_ok = bool(ex and ex.get("probs") and ex.get("liquid")
                 and (at - ex["ts"]).total_seconds() / 60 <= EXCHANGE_MAX_AGE_MIN)
    ex_probs = ex["probs"] if ex_ok else None
    books = {}
    if pin_probs:
        books[PIN] = pin_probs
    if ex_probs:
        books[EXCHANGE] = ex_probs
    if pin_probs and ex_probs:
        gap = max(abs(pin_probs[s] - ex_probs[s]) for s in sides)
        if gap > SHARP_DISAGREE:
            return Anchor("sharp_conflict", {}, 2, books, {"_": f"Pinnacle vs exchange gap {gap:.3f}"},
                          max(pin[1], ex["ts"]), None, round(gap, 4), pin_or)
        blend = {s: (pin_probs[s] + ex_probs[s]) / 2 for s in sides}
        tot = sum(blend.values())
        return Anchor("sharp_blend", {s: v / tot for s, v in blend.items()}, 2, books, {},
                      max(pin[1], ex["ts"]), round((at - min(pin[1], ex["ts"])).total_seconds() / 60, 1),
                      round(gap, 4), pin_or)
    if pin_probs:
        return Anchor("pinnacle_tight", pin_probs, 1, books, {}, pin[1],
                      round((at - pin[1]).total_seconds() / 60, 1), 0.0, pin_or)
    if ex_probs:
        return Anchor("exchange_liquid", ex_probs, 1, books, {}, ex["ts"],
                      round((at - ex["ts"]).total_seconds() / 60, 1), 0.0, None)
    return Anchor("none")


def load_exchange(match_id: str, market: str, sides: tuple[str, ...], *, at: datetime) -> dict | None:
    """Latest exchange capture (one market_id, one captured_at) at or before `at`."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """WITH last AS (
               SELECT market_id, captured_at FROM exchange_quotes
                WHERE match_id = %s AND market = %s AND captured_at <= %s
                  AND captured_at > %s - make_interval(mins => %s)
                ORDER BY captured_at DESC LIMIT 1)
           SELECT q.selection, q.back::float back, q.lay::float lay,
                  q.market_matched::float market_matched, q.captured_at
             FROM exchange_quotes q JOIN last l
               ON q.market_id = l.market_id AND q.captured_at = l.captured_at""",
        (match_id, market, at, at, EXCHANGE_MAX_AGE_MIN)) or []
    if not rows:
        return None
    quotes = {r["selection"]: r for r in rows}
    probs, liquid, info = exchange_fair(quotes, sides)
    return {"probs": probs, "liquid": liquid, "ts": rows[0]["captured_at"], "info": info}


def resolve_sharp(match_id: str, market: str, *, at: datetime | None = None) -> Anchor:
    """The sharp tier on its own (Pinnacle and/or exchange). For research benchmarks
    and the v2 sharp bots; returns `none` when neither sharp source is usable."""
    sides = market_sides(market)
    if not sides or market not in _EX_MARKETS and not market.startswith("over_under"):
        return Anchor("none", dropped={"_": f"market {market!r} not supported by the sharp tier"})
    at = at or datetime.now(timezone.utc)
    pin = load_sets(match_id, market, sides, at=at, lookback_min=PIN_MAX_AGE_MIN).get(PIN)
    ex = load_exchange(match_id, market, sides, at=at) if market in _EX_MARKETS else None
    return compute_sharp(pin, ex, sides, at=at)
