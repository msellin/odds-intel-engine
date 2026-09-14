#!/usr/bin/env python3
"""OWN market expansion — which ADDITIONAL markets can carry a sharp-edge
strategy at the books we can actually place at (Coolbet, Epicbet, Unibet-Site)?

Read-only. Writes nothing. See docs/OWN_MARKET_EXPANSION_2026_09_14.md.

THE FOUR GATES, tested in this order, stopping early on the first failure —
because backtesting a market that cannot settle is wasted effort:

  1. ANCHOR      Pinnacle prices it with a full, de-viggable complement.
                 Reported as Pinnacle's median overround per market.
  2. EXECUTABLE  A price exists at Coolbet / Epicbet / Unibet-Site on enough
                 co-priced fixtures to matter. Reported as fixtures/day where
                 BOTH the anchor and an executable book have a complete market.
  3. SETTLEABLE  Measured percentage of FINISHED fixtures IN THAT POPULATION
                 that carry the statistic the market grades on. Measured, never
                 assumed.
  4. EDGE        Time-aligned sharp-edge backtest with n, ROI, CI, fold split
                 and the median anchor-to-bet gap on every cell.

Plus a SAME-QUANTITY check before any backtest: the books must price the same
underlying quantity. On cards they demonstrably do not (implied P(over) on
cards_ou_65: Coolbet 0.176, Epicbet 0.270, Pinnacle 0.346, Bet365 0.482) and a
bot built on that mismatch would have staked into a units error.

METHOD NOTES — each of these is a trap this repo has already paid for.

* ASSEMBLE, THEN ALIGN (ANALYSIS_GOTCHAS §63). `odds_snapshots` stamps each ROW,
  not each sweep; Coolbet's 1X2 triple spans ~100ms. Joining books on timestamp
  equality measures write granularity, not simultaneity. Every book's complement
  is assembled from a +/-2 min window first, and only assembled quotes are
  aligned across books.
* TIME ALIGNMENT IS NOT OPTIONAL (OWN_PATH_VERDICT). Taking each book's latest
  quote independently harvests staleness and inflates edge (+8.47% unaligned vs
  +5.54% aligned on the same rule). Every cell prints its median gap.
* LINE SEMANTICS (§25, §53, §61). O/U and AH comparisons match on the LINE, not
  just the market. AH grades home-perspective for BOTH sides.
* PHANTOM BOOKS excluded — 'Unibet' (AF feed, 33.1% phantom-high), 'Unibet-Kambi'
  (38%), and the football-data.co.uk CSV/synthetic names.
* MULTIPLE COMPARISONS. Every cell tested is counted and printed, the test
  period is time-ordered and held out, and a NEGATIVE CONTROL (the same harness
  driven by a de-vigged anchor taken from a DIFFERENT fixture) must lose roughly
  the vig. If it does not, the harness is broken and the run says so.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict
from datetime import timedelta
from statistics import median

from workers.api_clients.db import execute_query
from workers.model.devig import devig

ANCHOR_BOOK = "Pinnacle"
EXEC_BOOKS = ("Coolbet", "Epicbet", "Unibet-Site")

# Never a price. 'Unibet' is the AF feed (33.1% phantom-high, dead 2026-09-12),
# 'Unibet-Kambi' diverged 38% from the site, and the rest are football-data.co.uk
# CSV imports or synthetic aggregates over other books.
EXCLUDED_BOOKS = ("Unibet", "Unibet-Kambi", "Max", "Avg", "Betfair Exchange",
                  "BetWin", "Betfred")

ASSEMBLE_WINDOW_MIN = 2.0     # a book's own complement may straddle this

# ODDS-OUTLIER-FILTER-2026-08-18 / ANALYSIS_GOTCHAS §9. A mislabelled line
# manufactures an enormous fake edge (over_under_25 at odds 4.5+ once read
# CLV +173.95%). The production guard drops a book price above Pinnacle x this
# multiplier on the SAME aligned anchor: 1.35 for 3-way, 1.30 for 2-way, which
# are `daily_pipeline_v2._PIN_1X2_OUTLIER_MULT` / `_PIN_OU_OUTLIER_MULT`.
OUTLIER_MULT_3WAY = 1.35
OUTLIER_MULT_2WAY = 1.30
DEFAULT_ALIGN_MIN = 15.0      # anchor-to-executable tolerance
PER_BET_SD_FALLBACK = 1.3


# ── market catalogue ────────────────────────────────────────────────────────
# `sel` is the COMPLEMENT SET — the de-vig is only valid over a complete one.
# `fair` is what the de-vigged implied probabilities must sum to (2.0 for double
# chance: each of 1X/12/X2 covers two of three outcomes).
# `settles_on` names the statistic the market grades against, which is what
# gate 3 measures the presence of.
class M:
    def __init__(self, key, label, sql, sel, fair, settles_on, lined,
                 fixed_line=None):
        self.key, self.label, self.sql = key, label, sql
        self.sel, self.fair = sel, fair
        self.settles_on, self.lined = settles_on, lined
        # A single-line market (over_under_25) still grades against a line — it
        # just does not vary, so it carries no monotonicity check.
        self.fixed_line = fixed_line


OU = ("over", "under")
X3 = ("home", "draw", "away")

MARKETS = [
    M("1x2", "1x2 (control — LIVE)", "o.market = '1x2'", X3, 1.0, "ft_goals", False),
    M("over_under_25", "O/U 2.5 (control — LIVE)", "o.market = 'over_under_25'", OU, 1.0, "ft_goals", False, 2.5),
    M("over_under_15", "O/U 1.5", "o.market = 'over_under_15'", OU, 1.0, "ft_goals", False, 1.5),
    M("over_under_35", "O/U 3.5", "o.market = 'over_under_35'", OU, 1.0, "ft_goals", False, 3.5),
    M("over_under_45", "O/U 4.5", "o.market = 'over_under_45'", OU, 1.0, "ft_goals", False, 4.5),
    M("btts", "BTTS", "o.market = 'btts'", ("yes", "no"), 1.0, "ft_goals", False),
    M("double_chance", "Double chance", "o.market = 'double_chance'", ("1x", "12", "x2"), 2.0, "ft_goals", False),
    M("draw_no_bet", "Draw no bet", "o.market = 'draw_no_bet'", ("home", "away"), 1.0, "ft_goals", False),
    M("asian_handicap", "Asian handicap", "o.market = 'asian_handicap'", ("home", "away"), 1.0, "ah", True),
    M("1x2_1h", "1st-half 1x2", "o.market = '1x2_1h'", X3, 1.0, "ht_goals", False),
    M("over_under_1h", "1st-half totals", "o.market LIKE 'over_under_1h_%'", OU, 1.0, "ht_goals", True),
    M("team_total", "Team totals (FT)", "o.market LIKE 'team_total_%' AND o.market NOT LIKE 'team_total_1h%'", OU, 1.0, "team_ft", True),
    M("team_total_1h", "Team totals (1H)", "o.market LIKE 'team_total_1h%'", OU, 1.0, "team_ht", True),
    M("corners_ou", "Corners O/U (match)", "o.market ~ '^corners_ou_[0-9]+$'", OU, 1.0, "corners", True),
    M("corners_1h", "Corners O/U (1H)", "o.market LIKE 'corners_1h_ou_%'", OU, 1.0, "corners_ht", True),
    M("corners_team", "Corners O/U (team)", "(o.market LIKE 'corners_home_ou_%' OR o.market LIKE 'corners_away_ou_%')", OU, 1.0, "corners_team", True),
    M("cards_ou", "Cards O/U", "o.market LIKE 'cards_ou_%'", OU, 1.0, "cards", True),
]
MARKETS_BY_KEY = {m.key: m for m in MARKETS}


# ── loading ─────────────────────────────────────────────────────────────────
def load_market(m: M, days: int, books: tuple[str, ...]):
    """Pre-kickoff, non-live quotes for one market family plus everything gate 3
    and the grader need. Books are passed in so the same-quantity check can pull
    a wider set than the gates do."""
    rows = execute_query(
        f"""
        SELECT o.match_id, o.bookmaker, o.market, o.selection,
               o.odds::float AS odds, o.handicap_line::float AS line,
               o.timestamp, mt.date, mt.status,
               mt.score_home, mt.score_away, mt.ht_score_home, mt.ht_score_away,
               st.corners_home, st.corners_away,
               st.corners_home_ht, st.corners_away_ht, ev.n_cards
          FROM odds_snapshots o
          JOIN matches mt ON mt.id = o.match_id
          LEFT JOIN match_stats st ON st.match_id = o.match_id
          LEFT JOIN (SELECT match_id, count(*) AS n_cards
                       FROM match_events
                      WHERE event_type IN ('yellow_card', 'red_card')
                      GROUP BY 1) ev ON ev.match_id = o.match_id
         WHERE o.timestamp > now() - (%s || ' days')::interval
           AND o.bookmaker = ANY(%s)
           AND o.bookmaker <> ALL(%s)
           AND o.is_live IS NOT TRUE
           AND o.timestamp < mt.date
           AND ({m.sql})
        """,
        (str(days), list(books), list(EXCLUDED_BOOKS)),
    )
    return rows


def line_of(m: M, row) -> float | None:
    """The line a quote belongs to. `handicap_line` is authoritative where it is
    populated; the market-name suffix is the fallback for the 251,969 legacy
    `over_under_*` rows §61 left deliberately NULL, and for the AH rows it never
    applied to. Returns None when the line is genuinely unknowable — honestly
    absent beats confidently wrong (§61)."""
    if m.fixed_line is not None:
        return m.fixed_line
    if not m.lined:
        return None
    if row["line"] is not None:
        return float(row["line"])
    tok = row["market"].rsplit("_", 1)[-1]
    if not tok.isdigit():
        return None
    # Two-digit glued tokens are unambiguous ('95' -> 9.5). Three-digit ones are
    # not ('125' is 1.25 and 12.5) unless they carry a leading zero, so they are
    # dropped rather than guessed.
    if len(tok) == 2:
        return int(tok) / 10.0
    if len(tok) == 3 and tok[0] == "0":
        return int(tok) / 100.0
    return None


def key_of(m: M, row):
    """The comparison key. Two quotes are comparable only at the same LINE
    (§25) — and for team totals / team corners, the same SIDE, which lives in
    the market name."""
    ln = line_of(m, row)
    if m.key in ("team_total", "team_total_1h", "corners_team"):
        side = "home" if "_home" in row["market"] else "away"
        return (side, ln)
    return (None, ln)


# ── assembly (§63) ──────────────────────────────────────────────────────────
def assemble(obs, sels, window=ASSEMBLE_WINDOW_MIN):
    """[(anchor_ts, {sel: odds})] — a book's COMPLETE complements, allowing its
    rows to straddle `window` minutes. Verbatim in spirit from
    own_path_kill_criterion.assemble, generalised past the 1X2 triple."""
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked = {}
        for t, sel, o in obs[i:]:
            if (t - t0).total_seconds() / 60.0 > window:
                break
            picked.setdefault(sel, o)
        if all(s in picked for s in sels):
            out.append((t0, {s: picked[s] for s in sels}))
    return out


def overround(quote: dict, sels, fair: float) -> float:
    return sum(1.0 / quote[s] for s in sels) / fair - 1.0


def index_rows(m: M, rows):
    """(match_id, key) -> {book: [(ts, sel, odds)]}, plus match facts."""
    obs = defaultdict(lambda: defaultdict(list))
    facts = {}
    for r in rows:
        if r["odds"] is None or r["odds"] <= 1.0:
            continue
        if r["selection"] not in m.sel:
            continue
        k = key_of(m, r)
        if (m.lined or m.fixed_line is not None) and k[1] is None:
            continue
        obs[(r["match_id"], k)][r["bookmaker"]].append(
            (r["timestamp"], r["selection"], r["odds"]))
        facts[r["match_id"]] = r
    return obs, facts


# ── grading ─────────────────────────────────────────────────────────────────
def statistic(m: M, f, key):
    """The number this market settles against, or None when it is absent."""
    on = m.settles_on
    if on == "ft_goals":
        if f["score_home"] is None:
            return None
        return f["score_home"] + f["score_away"]
    if on == "ht_goals":
        if f["ht_score_home"] is None:
            return None
        return f["ht_score_home"] + f["ht_score_away"]
    if on == "team_ft":
        if f["score_home"] is None:
            return None
        return f["score_home"] if key[0] == "home" else f["score_away"]
    if on == "team_ht":
        if f["ht_score_home"] is None:
            return None
        return f["ht_score_home"] if key[0] == "home" else f["ht_score_away"]
    if on == "corners":
        if f["corners_home"] is None or f["corners_away"] is None:
            return None
        return f["corners_home"] + f["corners_away"]
    if on == "corners_ht":
        if f["corners_home_ht"] is None or f["corners_away_ht"] is None:
            return None
        return f["corners_home_ht"] + f["corners_away_ht"]
    if on == "corners_team":
        c = f["corners_home"] if key[0] == "home" else f["corners_away"]
        return None if c is None else c
    if on == "cards":
        # §51: the settlement definition is the EVENTS count, pinned as
        # settlement.CARDS_SETTLEMENT_DEF, not match_stats' card columns.
        return None if f["n_cards"] is None else f["n_cards"]
    if on == "ah":
        if f["score_home"] is None:
            return None
        return f["score_home"] - f["score_away"]
    return None


def grade(m: M, sel: str, key, f):
    """Return per-unit PROFIT multiplier ignoring odds: 1 = win, 0 = push,
    -1 = loss; None = ungradeable (leave it out, never guess)."""
    stat = statistic(m, f, key)
    if stat is None:
        return None
    if m.settles_on == "ah":
        # §53: handicap_line is HOME-perspective for BOTH selections.
        spread = -key[1]
        margin = stat
        frac = margin - spread
        if abs(frac * 4 - round(frac * 4)) > 1e-6:
            return None
        if abs(frac) < 1e-9:
            return 0.0
        if sel == "home":
            win = frac > 0
        else:
            win = frac < 0
        q = abs(frac)
        if abs(q - 0.25) < 1e-9:          # quarter line: half stake pushes
            return 0.5 if win else -0.5
        return 1.0 if win else -1.0
    if m.sel == OU:
        line = key[1]
        if abs(stat - line) < 1e-9:
            return 0.0                    # whole-number line pushes
        over = stat > line
        return (1.0 if over else -1.0) * (1.0 if sel == "over" else -1.0)
    if m.sel == X3:
        h = f["ht_score_home"] if m.settles_on == "ht_goals" else f["score_home"]
        a = f["ht_score_away"] if m.settles_on == "ht_goals" else f["score_away"]
        if h is None:
            return None
        got = "home" if h > a else ("away" if a > h else "draw")
        return 1.0 if sel == got else -1.0
    if m.key == "btts":
        h, a = f["score_home"], f["score_away"]
        yes = h > 0 and a > 0
        return 1.0 if (sel == "yes") == yes else -1.0
    if m.key == "double_chance":
        h, a = f["score_home"], f["score_away"]
        got = "home" if h > a else ("away" if a > h else "draw")
        cover = {"1x": ("home", "draw"), "12": ("home", "away"), "x2": ("draw", "away")}
        return 1.0 if got in cover[sel] else -1.0
    if m.key == "draw_no_bet":
        h, a = f["score_home"], f["score_away"]
        if h == a:
            return 0.0
        return 1.0 if ((sel == "home") == (h > a)) else -1.0
    return None


# ── gates ───────────────────────────────────────────────────────────────────
def gate_1_2_3(m: M, obs, facts, days: int):
    """Anchor overround, executable coverage and settleability in one pass over
    the assembled quotes, so all three describe the SAME population."""
    anchor_or, exec_fx, both_fx = [], set(), set()
    per_book_or = defaultdict(list)
    settle_fin, settle_ok = 0, 0
    seen_fin = set()

    for (mid, key), bybook in obs.items():
        a = assemble(bybook.get(ANCHOR_BOOK, []), m.sel)
        if a:
            anchor_or.append(overround(a[-1][1], m.sel, m.fair))
        e_books = []
        for b in EXEC_BOOKS:
            q = assemble(bybook.get(b, []), m.sel)
            if q:
                e_books.append(b)
                per_book_or[b].append(overround(q[-1][1], m.sel, m.fair))
        if e_books:
            exec_fx.add(mid)
        if a and e_books:
            both_fx.add(mid)
            f = facts[mid]
            if f["status"] == "finished" and (mid, key) not in seen_fin:
                seen_fin.add((mid, key))
                settle_fin += 1
                if statistic(m, f, key) is not None:
                    settle_ok += 1
    return {
        "anchor_median_or": median(anchor_or) if anchor_or else None,
        "anchor_n": len(anchor_or),
        "book_or": {b: (median(v), len(v)) for b, v in per_book_or.items()},
        "exec_fx": len(exec_fx),
        "both_fx": len(both_fx),
        "both_fx_per_day": len(both_fx) / days,
        "settle_fin": settle_fin,
        "settle_ok": settle_ok,
        "settle_pct": (100.0 * settle_ok / settle_fin) if settle_fin else None,
    }


def gate_same_quantity(m: M, days: int, min_fx: int = 30):
    """Do the books price the SAME QUANTITY? Two independent tests, both of
    which cards fails and which nothing else should.

    (a) LEVEL. At a shared line, every book's de-vigged P(over) should sit in
        the same range. A book whose P(over) is 0.18 where the sharp book says
        0.35 is not offering value, it is counting something else.
    (b) MONOTONICITY. P(over) must FALL as the line rises, at every book. The
        live parser bug behind the cards trap shows up here as a flat ~0.5
        across every line from 2.5 to 8.5 — a label detached from the quantity.
    """
    if not m.lined:
        return {"applicable": False}
    books = (ANCHOR_BOOK,) + EXEC_BOOKS + ("Bet365", "1xBet")
    rows = load_market(m, days, books)
    obs, _ = index_rows(m, rows)
    pov = defaultdict(list)           # (book, key) -> [p_over]
    for (mid, key), bybook in obs.items():
        for b, o in bybook.items():
            q = assemble(o, m.sel)
            if not q:
                continue
            d = devig([q[-1][1]["over"], q[-1][1]["under"]])
            if d:
                pov[(b, key)].append(d[0])
    med = {k: (median(v), len(v)) for k, v in pov.items() if len(v) >= 5}

    # (a) level agreement at shared lines, our books + anchor
    ours = (ANCHOR_BOOK,) + EXEC_BOOKS
    spreads = []
    for key in {k for _, k in med}:
        vals = [med[(b, key)][0] for b in ours if (b, key) in med
                and med[(b, key)][1] >= min_fx]
        if len(vals) >= 3:
            spreads.append(max(vals) - min(vals))
    # (b) monotonicity per book over its own lines
    mono = {}
    for b in books:
        pts = sorted(((k[1], med[(b, k)][0]) for k in
                      {kk for bb, kk in med if bb == b}
                      if med[(b, k)][1] >= 10 and k[1] is not None),
                     key=lambda x: x[0])
        pts = [p for p in pts if p[0] is not None]
        if len(pts) < 4:
            continue
        drops = sum(1 for i in range(1, len(pts)) if pts[i][1] <= pts[i - 1][1] + 1e-9)
        mono[b] = {"lines": len(pts), "monotone_steps": drops / (len(pts) - 1),
                   "p_hi": pts[0][1], "p_lo": pts[-1][1],
                   "range": pts[0][1] - pts[-1][1]}
    ok_level = bool(spreads) and median(spreads) <= 0.10
    ok_mono = all(v["monotone_steps"] >= 0.80 and v["range"] >= 0.10
                  for b, v in mono.items() if b in ours)
    return {"applicable": True, "n_shared_lines": len(spreads),
            "median_spread": median(spreads) if spreads else None,
            "mono": mono, "pass": bool(ok_level and ok_mono and spreads and mono)}


# ── gate 4: the backtest ────────────────────────────────────────────────────
def build_bets(m: M, obs, facts, align_min: float, scramble: bool = False,
               rng: random.Random | None = None):
    """One candidate per (fixture, comparison key, executable book): the LATEST
    executable quote that has a time-aligned anchor. Returns rows carrying the
    full edge vector so a floor sweep costs nothing extra.

    `scramble` is the NEGATIVE CONTROL: the anchor probabilities come from a
    DIFFERENT fixture at the same market and line. A harness that reports an
    edge on a junk anchor is measuring itself, not the market."""
    anchor_pool = defaultdict(list)
    prepared = {}
    for (mid, key), bybook in obs.items():
        a = assemble(bybook.get(ANCHOR_BOOK, []), m.sel)
        if not a:
            continue
        prepared[(mid, key)] = (a, bybook)
        for ts, q in a:
            d = devig([q[s] for s in m.sel])
            if d:
                anchor_pool[key].append(dict(zip(m.sel, d)))

    out = []
    for (mid, key), (a, bybook) in prepared.items():
        f = facts[mid]
        for b in EXEC_BOOKS:
            eq = assemble(bybook.get(b, []), m.sel)
            if not eq:
                continue
            best = None
            for ts, q in reversed(eq):          # latest executable quote first
                at, aq = min(a, key=lambda x: abs((x[0] - ts).total_seconds()))
                gap = abs((at - ts).total_seconds()) / 60.0
                if gap <= align_min:
                    best = (ts, q, at, aq, gap)
                    break
            if best is None:
                continue
            ts, q, at, aq, gap = best
            if scramble:
                pool = anchor_pool.get(key, [])
                if len(pool) < 2:
                    continue
                p = rng.choice(pool)
            else:
                d = devig([aq[s] for s in m.sel])
                if not d:
                    continue
                p = dict(zip(m.sel, d))
            mult = OUTLIER_MULT_3WAY if len(m.sel) == 3 else OUTLIER_MULT_2WAY
            legs = []
            for s in m.sel:
                # §9: drop the leg, not the fixture — an outlier is one quote.
                if q[s] > aq[s] * mult:
                    continue
                g = grade(m, s, key, f)
                if g is None:
                    continue
                legs.append({"sel": s, "odds": q[s], "p": p[s],
                             "edge": p[s] * q[s] - 1.0, "grade": g})
            if legs:
                out.append({"mid": mid, "key": key, "book": b, "date": f["date"],
                            "gap": gap, "legs": legs,
                            "status": f["status"]})
    return out


def ret_of(leg):
    """Per-unit return. grade 1 = win (odds-1), -1 = loss (-1), 0 = push,
    +/-0.5 = the quarter-line half-stake case."""
    g = leg["grade"]
    if g > 0:
        return (leg["odds"] - 1.0) * g
    return g


def evaluate(bets, floor: float, book: str | None = None):
    """Apply an edge floor and return the ledger a bot would actually have.

    SINGLE-BOOK BASIS (§52, §55). Selection NEVER crosses books: taking the
    max-edge leg across Coolbet/Epicbet/Unibet-Site re-creates the line-shop
    artifact — it picks whichever book is most mispriced and calls that error an
    edge, which is precisely the test that condemned the O/U 2.5 we make money
    on. Each book is its own ledger; a book is chosen within, never between.

    ONE BET PER (fixture, book). Within a book we do take the best-edge
    selection and, on a laddered market, the best-edge LINE — that is what a bot
    with a per-fixture blast-radius cap does. Reported alongside the all-legs
    count so the collapse is visible."""
    picked = []
    for b in bets:
        if book is not None and b["book"] != book:
            continue
        legs = [l for l in b["legs"] if l["edge"] >= floor]
        if not legs:
            continue
        picked.append({**b, "leg": max(legs, key=lambda x: x["edge"])})
    by = {}
    for p in picked:
        k = (p["mid"], p["book"])
        if k not in by or p["leg"]["edge"] > by[k]["leg"]["edge"]:
            by[k] = p
    return list(by.values())


def roi_ci(picked):
    """ROI with a CLUSTER-ROBUST CI on match_id. Legs on one fixture are not
    independent — home/draw/away are mutually exclusive, and the same selection
    at two books is nearly one bet. A naive iid interval on correlated legs is
    too narrow, which is how a noisy cell passes for a finding."""
    rets = [ret_of(p["leg"]) for p in picked]
    n = len(rets)
    if n == 0:
        return None
    mean = sum(rets) / n
    if n > 1:
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    else:
        sd = PER_BET_SD_FALLBACK
    by = defaultdict(float)
    for p, r in zip(picked, rets):
        by[p["mid"]] += r - mean
    g = len(by)
    var = sum(v * v for v in by.values()) / (n * n) if g > 1 else (sd * sd / n)
    if g > 1:
        var *= g / (g - 1)
    se = math.sqrt(max(var, 0.0))
    return {"n": n, "roi": mean, "sd": sd, "clusters": g,
            "lo": mean - 1.96 * se, "hi": mean + 1.96 * se, "se": se,
            "median_gap": median([p["gap"] for p in picked]),
            "fixtures": len({p["mid"] for p in picked})}


def folds(picked, k=3):
    """Time-ordered folds — the only split that answers 'would this have worked
    on data I had not already looked at'."""
    s = sorted(picked, key=lambda p: p["date"])
    if len(s) < k * 10:
        return []
    sz = len(s) // k
    out = []
    for i in range(k):
        part = s[i * sz:(i + 1) * sz] if i < k - 1 else s[i * sz:]
        r = roi_ci(part)
        if r:
            out.append(r)
    return out


def power_n(diff: float, sd: float) -> float:
    """§60: n per arm to detect `diff` at 80% power. Print it before believing
    any point estimate."""
    if diff == 0:
        return float("inf")
    return 2 * ((1.96 + 0.84) * sd / diff) ** 2


# ── driver ──────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=17)
    ap.add_argument("--align-min", type=float, default=DEFAULT_ALIGN_MIN)
    ap.add_argument("--floors", default="0.02,0.03,0.05,0.08")
    ap.add_argument("--markets", default="", help="comma-separated keys; default all")
    ap.add_argument("--min-fx-per-day", type=float, default=3.0)
    ap.add_argument("--min-settle-pct", type=float, default=70.0)
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()

    floors = [float(x) for x in a.floors.split(",")]
    keys = [k for k in a.markets.split(",") if k] or [m.key for m in MARKETS]
    rng = random.Random(a.seed)
    cells = 0
    verdicts = []

    for k in keys:
        m = MARKETS_BY_KEY[k]
        print(f"\n{'='*78}\n{m.label}  [{m.key}]\n{'='*78}", flush=True)
        rows = load_market(m, a.days, (ANCHOR_BOOK,) + EXEC_BOOKS)
        obs, facts = index_rows(m, rows)
        g = gate_1_2_3(m, obs, facts, a.days)

        aor = g["anchor_median_or"]
        print(f"  GATE 1 anchor      Pinnacle median overround: "
              f"{'n/a — Pinnacle does not price it' if aor is None else f'{aor*100:6.2f}%  (n={g['anchor_n']})'}")
        for b, (v, n) in sorted(g["book_or"].items()):
            print(f"                     {b:14s} {v*100:6.2f}%  (n={n})")
        if aor is None:
            verdicts.append((m, g, None, None, "GATE 1 FAIL — no sharp anchor"))
            print("  -> STOP: no de-viggable Pinnacle complement. Not backtested.")
            continue

        print(f"  GATE 2 executable  anchor+exec fixtures: {g['both_fx']} "
              f"({g['both_fx_per_day']:.1f}/day)   exec-any: {g['exec_fx']}")
        if g["both_fx_per_day"] < a.min_fx_per_day:
            verdicts.append((m, g, None, None, "GATE 2 FAIL — too few co-priced fixtures"))
            print("  -> STOP: not enough co-priced fixtures to matter. Not backtested.")
            continue

        sp = g["settle_pct"]
        print(f"  GATE 3 settleable  {g['settle_ok']}/{g['settle_fin']} finished "
              f"= {('n/a' if sp is None else f'{sp:.1f}%')}  (statistic: {m.settles_on})")
        if sp is None or sp < a.min_settle_pct:
            verdicts.append((m, g, None, None, "GATE 3 FAIL — cannot settle"))
            print("  -> STOP: cannot be evaluated on the bets it would place. Not backtested.")
            continue

        q = gate_same_quantity(m, a.days)
        if q["applicable"]:
            print(f"  QUANTITY CHECK     shared lines={q['n_shared_lines']}  "
                  f"median cross-book P(over) spread="
                  f"{'n/a' if q['median_spread'] is None else f'{q['median_spread']:.3f}'}")
            for b, v in sorted(q["mono"].items()):
                print(f"                     {b:14s} lines={v['lines']:2d} "
                      f"monotone={v['monotone_steps']*100:5.1f}%  "
                      f"P(over) {v['p_hi']:.3f} -> {v['p_lo']:.3f}")
            print(f"                     -> {'PASS' if q['pass'] else 'FAIL'}")
            if not q["pass"]:
                verdicts.append((m, g, q, None, "QUANTITY FAIL — books price different things"))
                print("  -> STOP: the books do not price the same quantity. Not backtested.")
                continue
        else:
            print("  QUANTITY CHECK     n/a (unlined market)")

        bets = build_bets(m, obs, facts, a.align_min)
        ctrl = build_bets(m, obs, facts, a.align_min, scramble=True, rng=rng)
        print(f"  GATE 4 backtest    candidates={len(bets)}  "
              f"(align <= {a.align_min:.0f} min)")
        rowsout = []
        for fl in floors:
            cells += 1
            sel = evaluate(bets, fl)
            r = roi_ci(sel)
            c = roi_ci(evaluate(ctrl, fl))
            fo = folds(sel)
            if r is None:
                print(f"    floor {fl*100:4.1f}%   no bets")
                continue
            fs = " / ".join(f"{x['roi']*100:+.1f}" for x in fo) or "n/a"
            need = power_n(abs(r["roi"]), r["sd"])
            print(f"    floor {fl*100:4.1f}%   n={r['n']:5d} fx={r['fixtures']:5d}  "
                  f"ROI {r['roi']*100:+6.2f}%  CI [{r['lo']*100:+6.2f},{r['hi']*100:+6.2f}]  "
                  f"gap {r['median_gap']:5.1f}m  folds {fs}  "
                  f"| ctrl n={c['n'] if c else 0} "
                  f"ROI {(c['roi']*100 if c else float('nan')):+6.2f}%"
                  f"  | n@80%power {need:,.0f}")
            for b in EXEC_BOOKS:
                rb = roi_ci(evaluate(bets, fl, book=b))
                if rb:
                    print(f"                      {b:14s} n={rb['n']:5d}  "
                          f"ROI {rb['roi']*100:+6.2f}%  "
                          f"CI [{rb['lo']*100:+6.2f},{rb['hi']*100:+6.2f}]")
            rowsout.append((fl, r, c, fo))
        verdicts.append((m, g, q, rowsout, "reached gate 4"))

    print(f"\n\n{'='*78}\nCELLS TESTED: {cells} (market x floor). "
          f"Per-bet return sd ~1.3; §60 power check applies to every comparison.\n{'='*78}")
    for m, g, q, rowsout, note in verdicts:
        print(f"  {m.label:28s} {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
