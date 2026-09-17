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

from workers.utils.odds_assembly import assemble as _shared_assemble  # noqa: E402

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
                 fixed_line=None, ladder_sign=-1):
        self.key, self.label, self.sql = key, label, sql
        self.sel, self.fair = sel, fair
        self.settles_on, self.lined = settles_on, lined
        # A single-line market (over_under_25) still grades against a line — it
        # just does not vary, so it carries no monotonicity check.
        self.fixed_line = fixed_line
        # Direction the FIRST selection's probability must move as the line
        # rises. -1 for a total (P(over) falls as the line rises); +1 for Asian
        # handicap, where `handicap_line` is stored HOME-perspective (§53) so a
        # HIGHER line means the home side receives more goals and P(home covers)
        # RISES. Getting this backwards turns an honest ladder into a "flat
        # ladder, disqualified".
        self.ladder_sign = ladder_sign


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
    M("asian_handicap", "Asian handicap", "o.market = 'asian_handicap'", ("home", "away"), 1.0, "ah", True, None, +1),
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
    and the grader need.

    The `%` doubling is ANALYSIS_GOTCHAS #6: psycopg2 reads a bare `%` in the
    SQL string as a parameter placeholder, so a `LIKE 'cards_ou_%'` predicate
    interpolated into a parameterised query raises `IndexError: tuple index out
    of range` — which reads like a bug in the parameter list, not in the LIKE. Books are passed in so the same-quantity check can pull
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
           AND ({m.sql.replace("%", "%%")})
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
    """[(anchor_ts, {sel: odds})] — a book's COMPLETE complements.

    Delegates to `workers.utils.odds_assembly` — the ONE assembler. This was a
    private copy, and when the burst rule (COOLBET-DOUBLE-WRITE) landed in the
    shared module the copy silently kept the old first-in-window answer: same
    rows, draw 5.76 here against 6.65 there. Do not re-inline it. `window` stays
    in MINUTES for this script's callers; the shared module works in seconds.
    """
    return _shared_assemble(obs, tuple(sels), window_s=float(window) * 60.0)


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


def gate_same_quantity(m: M, days: int, min_fx: int = 30,
                       max_spread: float = 0.10, diag_books=("Bet365", "1xBet")):
    """Do the books price the SAME QUANTITY, LINE BY LINE?

    This is the check that would have stopped the cards bot. Cards generated 156
    "opportunities"/week at a 3% floor — more than every other market combined —
    purely because the books disagree about what they are counting.

    BOTH TESTS ARE PAIRED ON FIXTURES, which is not a detail. ANALYSIS_GOTCHAS
    §10: each book prices a different slate, so comparing two books' median
    P(over) compares their slates, not their prices. Run UNPAIRED, this check
    rejects O/U 1.5 / 3.5 / 4.5 — three markets where the books demonstrably do
    agree — with "spreads" of 0.13 to 0.25 that are entirely fixture mix.

    (a) LEVEL, paired. For every fixture a book and the anchor both quote at the
        same line, take the de-vigged P(over) difference; the line's bias is the
        MEDIAN of those differences. A line passes when no executable book is
        more than `max_spread` from the anchor.

    (b) MONOTONICITY, within ONE FIXTURE'S OWN LADDER and one side. P(over) must
        fall as the line rises. Measured inside a fixture, so a book's line mix
        cannot fake it either. A book whose ladder is flat is not pricing the
        ladder: Bet365 and 1xBet sit at ~0.46-0.58 at every cards line from 2.5
        to 7.5 — a label detached from the quantity, i.e. a live parser bug, and
        the reason cards produced more "opportunities" than every other market
        combined.

    A MARKET-LEVEL PASS/FAIL is the wrong output, because it throws away the
    half of a ladder that is sound. This returns the SET OF LINES that pass, and
    gate 4 runs on those lines only.
    """
    if not (m.lined or m.fixed_line is not None):
        return {"applicable": False, "pass_keys": None, "pass": True}
    # Bet365 and 1xBet are DIAGNOSTIC ONLY — they are not bettable and never
    # enter a gate. They are pulled because they are where the flat-ladder
    # pathology is visible, which is the evidence that the check works at all.
    books = (ANCHOR_BOOK,) + EXEC_BOOKS + tuple(diag_books)
    rows = load_market(m, days, books)
    obs, _ = index_rows(m, rows)
    # key -> book -> match_id -> de-vigged P(over)
    pov: dict = defaultdict(lambda: defaultdict(dict))
    for (mid, key), bybook in obs.items():
        for bk, o in bybook.items():
            q = assemble(o, m.sel)
            if not q:
                continue
            d = devig([q[-1][1][x] for x in m.sel])
            if d:
                pov[key][bk][mid] = d[0]
    ours = (ANCHOR_BOOK,) + EXEC_BOOKS

    # (a) PAIRED level agreement against the anchor, line by line
    per_line = {}
    for key, bybook in pov.items():
        anchor = bybook.get(ANCHOR_BOOK, {})
        biases, paired_n = {}, {}
        for bk in EXEC_BOOKS:
            common = set(anchor) & set(bybook.get(bk, {}))
            if len(common) < min_fx:
                continue
            biases[bk] = median([bybook[bk][x] - anchor[x] for x in common])
            paired_n[bk] = len(common)
        if not biases:
            per_line[key] = {"n_books": 0, "spread": None, "pass": False,
                             "why": "too thin to pair", "bias": {}, "paired_n": {}}
            continue
        worst = max(abs(v) for v in biases.values())
        per_line[key] = {
            "n_books": len(biases) + 1, "spread": worst,
            "pass": worst <= max_spread,
            "why": "" if worst <= max_spread else "paired level mismatch vs anchor",
            "bias": biases, "paired_n": paired_n,
            "books": {bk: median(list(v.values()))
                      for bk, v in bybook.items() if bk in ours and len(v) >= 5},
        }

    # (b) monotonicity measured WITHIN a fixture's own ladder
    mono = {}
    for bk in books:
        steps = drops = ladders = 0
        hi, lo = [], []
        fx = defaultdict(list)          # (match_id, side) -> [(line, p_over)]
        for key, bybook in pov.items():
            if key[1] is None:
                continue
            for mid, pv in bybook.get(bk, {}).items():
                fx[(mid, key[0])].append((key[1], pv))
        for pts in fx.values():
            if len(pts) < 4:
                continue
            pts.sort()
            ladders += 1
            steps += len(pts) - 1
            # `ladder_sign` is the direction P(sel0) must MOVE as the line
            # rises: -1 for a total, +1 for Asian handicap. A step is good when
            # it moves that way (or is flat), i.e. sign * delta >= 0.
            drops += sum(1 for i in range(1, len(pts))
                         if m.ladder_sign * (pts[i][1] - pts[i - 1][1]) >= -1e-9)
            hi.append(pts[0][1] if m.ladder_sign < 0 else pts[-1][1])
            lo.append(pts[-1][1] if m.ladder_sign < 0 else pts[0][1])
        if steps < 20:
            continue
        mono[bk] = {"lines": ladders, "monotone_steps": drops / steps,
                    "p_hi": median(hi), "p_lo": median(lo),
                    "range": median(hi) - median(lo)}

    # A book whose own ladder is flat is disqualified outright — no line of it is
    # trustworthy however well it happens to agree at one point.
    flat = {bk for bk, v in mono.items()
            if bk in ours and (v["monotone_steps"] < 0.85 or v["range"] < 0.10)}
    pass_keys = {k for k, v in per_line.items() if v["pass"]}
    table = sorted(((bk, k[0], k[1], median(list(v.values())), len(v))
                    for k, bb in pov.items() for bk, v in bb.items()
                    if k[1] is not None and len(v) >= 5),
                   key=lambda r: (str(r[1]), r[2], r[0]))
    return {"applicable": True, "per_line": per_line, "pass_keys": pass_keys,
            "mono": mono, "table": table, "flat_books": flat,
            "n_lines": len(per_line), "n_pass": len(pass_keys),
            "pass": bool(pass_keys) and not flat}


def gate_settlement_calibration(m: M, obs, facts, allowed_keys=None):
    """GATE 3b — does the statistic we grade on REPRODUCE the sharp book?

    Gate 3 asks whether the number exists. This asks whether it is the number
    the market is pricing, which is a different question and the one that cost
    real money on cards: `match_stats` card columns undercount the books' line
    by ~0.8/match (§51), so a bot grading on them manufactures phantom under-edge
    on every fixture while still settling 100% of its bets.

    The test needs no model. Pinnacle's de-vigged P(over), averaged over a few
    hundred fixtures, IS the realised over-rate to within sampling error. If our
    grading says otherwise, our definition of the quantity is wrong — not
    Pinnacle's.

    Returns mean anchor P(over) vs realised over-rate and the gap in pp.
    """
    exp, got = [], []
    for (mid, key), bybook in obs.items():
        if allowed_keys is not None and key not in allowed_keys:
            continue
        f = facts[mid]
        if f["status"] != "finished":
            continue
        a = assemble(bybook.get(ANCHOR_BOOK, []), m.sel)
        if not a:
            continue
        d = devig([a[-1][1][s2] for s2 in m.sel])
        if not d:
            continue
        g = grade(m, m.sel[0], key, f)
        # Only clean win/loss legs: a push carries no information and an Asian
        # quarter line's half-win is not a Bernoulli draw from P(sel).
        if g is None or abs(abs(g) - 1.0) > 1e-9:
            continue
        exp.append(d[0])
        got.append(1.0 if g > 0 else 0.0)
    if len(exp) < 100:
        return None
    e, o = sum(exp) / len(exp), sum(got) / len(got)
    se = math.sqrt(max(o * (1 - o), 1e-9) / len(got))
    return {"n": len(exp), "anchor": e, "realised": o, "gap": o - e,
            "z": (o - e) / se if se else 0.0}


# ── gate 4: the backtest ────────────────────────────────────────────────────
def build_bets(m: M, obs, facts, align_min: float, scramble: bool = False,
               rng: random.Random | None = None, allowed_keys=None):
    """One candidate per (fixture, comparison key, executable book): the LATEST
    executable quote that has a time-aligned anchor. Returns rows carrying the
    full edge vector so a floor sweep costs nothing extra.

    `scramble` is the NEGATIVE CONTROL: the anchor probabilities come from a
    DIFFERENT fixture at the same market and line. A harness that reports an
    edge on a junk anchor is measuring itself, not the market."""
    anchor_pool = defaultdict(list)
    prepared = {}
    for (mid, key), bybook in obs.items():
        if allowed_keys is not None and key not in allowed_keys:
            continue          # the same-quantity check rejected this line
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


def flat_control(ctrl_bets, book_or: float | None):
    """HARNESS VALIDITY — the number with a closed form.

    Flat-bet EVERY leg of the junk-anchor ledger, with no floor and no
    selection. If a book's prices are proportional to the true probabilities and
    our grading is right, that loses exactly

        -v / (1 + v)     where v is the book's overround

    and nothing else. It is the one control whose expected value is known in
    advance, so it separates "this market has no edge" from "we are grading this
    market wrong" — a distinction the FLOORED control cannot make, because
    taking the max-edge leg across a laddered market's many lines systematically
    picks the longest price on offer and so loses the vig PLUS the
    favourite-longshot bias.

    Measured 2026-09-14: corners -6.62% against an expected -6.9%/-6.5% (clean);
    cards -8.72% against the same -7.4%, and -11.4% at its central line 4.5
    against -7.4% — a ~4pp residual that is the card-count definition itself,
    the same gap gate 3b reports independently.
    """
    legs = [{**b, "leg": l} for b in ctrl_bets for l in b["legs"]]
    r = roi_ci(legs)
    if r is None:
        return None
    r["expected"] = (-book_or / (1 + book_or)) if book_or else None
    return r


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
    ap.add_argument("--quantity-table", action="store_true",
                    help="dump the per-book, per-line median P(over) grid — the "
                         "evidence behind the same-quantity verdict")
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

        # ANALYSIS_GOTCHAS §12: a heavy replay query gets OOM-killed silently.
        # Asian handicap alone is 2.7M rows across the five books over 17 days,
        # so the two diagnostic books are dropped on any market whose bettable
        # load is already large. They inform no gate.
        diag = () if len(rows) > 800_000 else ("Bet365", "1xBet")
        if not diag:
            print("  (diagnostic books Bet365/1xBet skipped — market too large "
                  "to reload; they inform no gate)")
        q = gate_same_quantity(m, a.days, diag_books=diag)
        allowed = None
        if q["applicable"]:
            print(f"  QUANTITY CHECK     lines checked={q['n_lines']}  "
                  f"lines PASSING={q['n_pass']}")
            for b, v in sorted(q["mono"].items()):
                tag = "  <-- FLAT LADDER, disqualified" if b in q["flat_books"] else ""
                print(f"                     {b:14s} ladders={v['lines']:5d} "
                      f"within-fixture monotone={v['monotone_steps']*100:5.1f}%  "
                      f"P(over) {v['p_hi']:.3f} -> {v['p_lo']:.3f}{tag}")
            for k in sorted(q["per_line"], key=lambda x: (str(x[0]), x[1] or 0)):
                v = q["per_line"][k]
                if v["spread"] is None:
                    continue
                print(f"                     line {k[1]:5.2f} "
                      f"{str(k[0] or ''):5s} worst paired bias vs anchor="
                      f"{v['spread']:+.3f}  "
                      f"{'PASS' if v['pass'] else 'FAIL — ' + v['why']}"
                      + ("   [" + ", ".join(
                          f"{b} {bi:+.3f} (n={v['paired_n'][b]})"
                          for b, bi in sorted(v.get('bias', {}).items())) + "]"
                         if a.quantity_table else ""))
            print(f"                     -> {'PASS' if q['pass'] else 'FAIL'}")
            if not q["pass"]:
                verdicts.append((m, g, q, None, "QUANTITY FAIL — books price different things"))
                print("  -> STOP: the books do not price the same quantity. Not backtested.")
                continue
            allowed = q["pass_keys"]
        else:
            print("  QUANTITY CHECK     n/a (unlined market)")

        cal = gate_settlement_calibration(m, obs, facts, allowed)
        if cal:
            print(f"  GATE 3b calibration n={cal['n']}  anchor P({m.sel[0]})="
                  f"{cal['anchor']:.3f}  realised={cal['realised']:.3f}  "
                  f"gap={cal['gap']*100:+.1f}pp  z={cal['z']:+.1f}")
            if abs(cal["z"]) > 4.0:
                verdicts.append((m, g, q, None,
                                 f"GATE 3b FAIL — grading disagrees with the sharp book "
                                 f"by {cal['gap']*100:+.1f}pp"))
                print("  -> STOP: the statistic we settle on is not the quantity the "
                      "market prices. Not backtested.")
                continue

        bets = build_bets(m, obs, facts, a.align_min, allowed_keys=allowed)
        ctrl = build_bets(m, obs, facts, a.align_min, scramble=True, rng=rng,
                          allowed_keys=allowed)
        mean_or = (median([v for v, _ in g["book_or"].values()])
                   if g["book_or"] else None)
        fc = flat_control(ctrl, mean_or)
        if fc:
            dev = (fc["roi"] - fc["expected"]) * 100 if fc["expected"] else None
            print(f"  NEG CONTROL (flat)  junk anchor, every leg, no floor: "
                  f"n={fc['n']}  ROI {fc['roi']*100:+.2f}%  "
                  f"expected {-mean_or/(1+mean_or)*100:+.2f}% "
                  f"(= -v/(1+v), v={mean_or*100:.2f}%)  "
                  f"deviation {dev:+.2f}pp"
                  # HOW TO READ THE DEVIATION. -v/(1+v) is the exact loss only
                  # if a book's prices are proportional to the true
                  # probabilities. They are not: the favourite-longshot bias
                  # makes flat-betting every leg lose MORE than the vig wherever
                  # the market is asymmetric, with no grading error involved —
                  # 1x2 (our known-good control) deviates -2.45pp and O/U 4.5,
                  # whose grading is `goals > 4.5`, deviates -4.50pp. So this
                  # line answers "is the harness paying the vig it should" and
                  # NOT "is the grading right". GATE 3b answers the grading
                  # question, because comparing the anchor's de-vigged P(over)
                  # to the realised rate is free of both biases.
                  )
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
