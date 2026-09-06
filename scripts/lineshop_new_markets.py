#!/usr/bin/env python3
"""NEW-MARKETS-LINESHOP-2026-09-05 — line-shop the newly collected markets.

The premise, and why there is no model in here
----------------------------------------------
Our binding constraint is repeatedly measured as PRICE, not model quality
(+1.26pp realised edge at closing against a ~2.50% vig). Pinnacle prices
corners, cards and first-half markets, and books we can actually place at
(Coolbet / Unibet-Kambi / Betano / Unibet / Epicbet) quote them too. So

    edge = best_accessible_price x devig(Pinnacle) - 1

is computable on these markets with **no model at all** — exactly the mechanic
already used for 1x2 / OU. Collection shipped 2026-09-05; this script is the
strategy half.

Three modes
-----------
    --mode coverage      what is actually in the table, per namespace and book,
                         and how many Pinnacle/accessible pairs exist
    --mode settle-audit  proves the settlement columns mean what we think, by
                         checking realised over-rate against de-vigged Pinnacle
    --mode backtest      the strategy: pick-time edge, CLV against Pinnacle's
                         close, ROI with its standard error, and a placebo

Correctness rules this script obeys (each one has burned this repo)
------------------------------------------------------------------
* **`odds_snapshots` is append-only.** `MAX(odds)` is a high-water mark, not a
  price (gotcha 30). Every price here comes from `DISTINCT ON (...) ORDER BY
  timestamp DESC`.
* **Pre-kickoff is `o.timestamp <= m.date`, not `is_live = false`.** `is_live`
  only excludes the `api-football-live` pseudo-book; real books post after
  kickoff under their own name, 30-40pct of AF-fed rows (gotcha 37).
* **Market shapes are matched EXACTLY, by anchored regex.** `split_part(market,
  '_', 1) = 'corners'` also matches `corners_home_ou`, `corners_away_ou` and
  `corners_1h_ou`. An earlier agent settled per-team and first-half markets
  against full-match corner counts that way and had to bin the result. Here
  `^corners_ou_[0-9]+$` cannot match `corners_ou_home_95`.
* **De-vig by market SHAPE.** Shin for the 3-way `1x2_1h`; proportional
  "manufactures apparent edge on draws and away dogs" (workers/model/devig.py).
  Two-way totals get Shin too, where it is near-identical to proportional —
  `--devig proportional` re-runs either so the difference is visible rather
  than assumed.
* **Outlier guard** (gotcha 9): an accessible price above
  `pinnacle x --outlier-mult` is a mislabelled line, not an edge.
* **Peer-lag guard** (gotcha 34): "latest quote per book" is not "live quote".
  A book whose newest quote is far behind its peers ON THE SAME FIXTURE has a
  dead feed. Absolute age caps do not work here — the morning cohort
  legitimately runs on 10h-old quotes — so the lag is measured against the
  other books on that fixture.
* **Judged on CLV, not ROI** (gotcha 8). ROI is printed with its standard
  error beside it, always, because at sd ~1.4 a week of data cannot decide it.
* **Placebo before believing anything.** Shuffle the Pinnacle probabilities
  across selections, re-apply the identical rule, report where the real number
  sits. CORNERS-EDGE-TAIL cleared this at z=+11.47; a rejected idea sat at 1.84.

THE BACKFILL TIMESTAMP — read this before interpreting any CLV number here
--------------------------------------------------------------------------
`scripts/backfill_af_new_markets.py` stamps every backfilled row at
**kickoff minus one minute** (`timestamp = v.ko - interval '1 minute'`,
`minutes_to_kickoff = 1`). That is a deliberate, correct choice for a
`DISTINCT ON ... ORDER BY timestamp DESC` reader, but it has a consequence for
this analysis that has to be stated rather than discovered:

    **The 7-day backfill has NO time series. It is one synthetic closing
    snapshot per fixture.**

Measured 2026-09-06 over the last 20 days of finished fixtures: **2,196 of
2,200** carry exactly one distinct pre-kickoff timestamp on these markets, and
the 10th, 50th and 90th percentiles of (kickoff - timestamp) are all 1 minute.
**Zero** finished fixtures have a quote 2h or more before kickoff. Only 4
fixtures — all from 2026-09-05, the day live collection started — have real
history.

So on today's data the pick price and the closing anchor are literally the same
row, and `clv = odds x devig(Pinnacle_close) - 1` is identically the entry edge.
A CLV figure computed on it is not a closing-line value, it is the edge
restated. This script therefore reports CLV only over rows where the pick
snapshot is genuinely EARLIER than the close, and prints that coverage. When
the coverage is near zero, the correct reading is "CLV is not yet measurable on
these markets", not "CLV is zero".

That is the single biggest thing standing between this machinery and a verdict,
and it fixes itself with time: live collection since 2026-09-05 writes real
30-minute history, so `--lead-hours 6` starts returning rows as the window
grows.

WHAT THIS FOUND ON ITS FIRST RUN (2026-09-06, 20-day window)
------------------------------------------------------------
* **CLV is not measurable yet** — 0 of 28,236 candidate selections have a pick
  snapshot earlier than the close, for the backfill-timestamp reason above.
* **Settlement validated for corners.** Realised over-rate against de-vigged
  Pinnacle P(over): corners_ou -0.3pp (n=1,326), corners_1h_ou +1.8pp (715),
  corners_home_ou -0.7pp (469), corners_away_ou -2.6pp (485).
* **Cards are NOT settleable with what we store.** All four candidate
  definitions come out 7.6-12.9pp short of the sharp forecast: yellow only
  -12.9, yellow+red -11.1, card points -9.1, `match_events` card rows -7.6.
  Our mean card count on fixtures Pinnacle prices is 3.52 (stats) / 3.73
  (events) against a modal Pinnacle line of 4.5. Whatever the books count, we
  do not count it. **Do not build a cards strategy on this until it is fixed.**
* **First-half goals settle ~+3 to +5pp hot**, and it looks like an
  availability bias rather than an edge: the fixtures whose events reproduce
  the stored full-time score average 3.20 goals, well above the slate. Treat
  those families as provisional.
* **The strategy is not decidable.** 733 picks / 593 settled at a 2pct edge
  floor, ROI +7.69pct with SE 4.04pct (95pct CI -0.2pct to +15.6pct), and the
  outcome-permutation placebo puts it at **z = +0.90** — indistinguishable from
  chance. ~9,300 settled bets are needed for a +/-2pct ROI interval; ~334 would
  do it on CLV, which is exactly why CLV is the gate we want and exactly what we
  cannot yet compute.

Usage
-----
    python3 scripts/lineshop_new_markets.py --mode coverage
    python3 scripts/lineshop_new_markets.py --mode settle-audit
    python3 scripts/lineshop_new_markets.py --mode backtest --edge 0.02
    python3 scripts/lineshop_new_markets.py --mode backtest --family corners_ou
"""
from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console          # noqa: E402
from rich.table import Table              # noqa: E402

from workers.api_clients.db import execute_query        # noqa: E402
from workers.model.devig import shin_devig, proportional_devig   # noqa: E402

console = Console(width=190)

# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------
# Mirrors daily_pipeline_v2.ACCESSIBLE_BOOKMAKERS (ACCESSIBLE-SET-VERIFY
# 2026-09-05: Marathonbet / 10Bet / 888Sport are EMTA-BLOCKED and are NOT here,
# and neither is Pinnacle — PINNACLE-NOT-ACCESSIBLE-2026-09-04). Imported
# rather than re-typed so the two can never drift.
try:
    from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS
except Exception:                                            # pragma: no cover
    ACCESSIBLE_BOOKMAKERS = frozenset(
        {"Coolbet", "Betano", "Unibet", "Unibet-Kambi", "Epicbet"})

SHARP = "Pinnacle"

# ---------------------------------------------------------------------------
# Market shape vocabulary — read off the WRITERS, then verified against the DB
# ---------------------------------------------------------------------------
# api_football.parse_fixture_odds      -> corners_ou / corners_home_ou /
#                                         corners_away_ou / corners_1h_ou /
#                                         cards_ou / over_under_1h /
#                                         team_total_1h_{home,away} /
#                                         team_total_{home,away} / 1x2_1h
# coolbet_explorer._parse_market       -> corners_ou[_home|_away][_1h] /
#                                         cards_ou[...]   <- NOTE the ordering
#                                         differs from AF's; see COVERAGE notes.
# unibet_kambi.parse_betoffers         -> corners_ou / cards_ou (match line only)
#
# Every entry is an ANCHORED regex. Two-way unless `outcomes` says otherwise.
#
# `settle` names the rule; `two_way` selections are always over/under.
FAMILIES: dict[str, dict] = {
    # --- corners -----------------------------------------------------------
    "corners_ou":        {"re": r"^corners_ou_(\d+)$",        "settle": "corners_ft"},
    "corners_1h_ou":     {"re": r"^corners_1h_ou_(\d+)$",     "settle": "corners_1h"},
    "corners_ou_1h":     {"re": r"^corners_ou_1h_(\d+)$",     "settle": "corners_1h"},
    "corners_home_ou":   {"re": r"^corners_home_ou_(\d+)$",   "settle": "corners_home"},
    "corners_ou_home":   {"re": r"^corners_ou_home_(\d+)$",   "settle": "corners_home"},
    "corners_away_ou":   {"re": r"^corners_away_ou_(\d+)$",   "settle": "corners_away"},
    "corners_ou_away":   {"re": r"^corners_ou_away_(\d+)$",   "settle": "corners_away"},
    # --- cards -------------------------------------------------------------
    "cards_ou":          {"re": r"^cards_ou_(\d+)$",          "settle": "cards_ft"},
    "cards_ou_1h":       {"re": r"^cards_ou_1h_(\d+)$",       "settle": "cards_1h"},
    # --- first half goals --------------------------------------------------
    "over_under_1h":     {"re": r"^over_under_1h_(\d+)$",     "settle": "goals_1h"},
    "team_total_1h_home": {"re": r"^team_total_1h_home_(\d+)$", "settle": "goals_1h_home"},
    "team_total_1h_away": {"re": r"^team_total_1h_away_(\d+)$", "settle": "goals_1h_away"},
    # --- full-match team totals (Pinnacle-only namespace today) ------------
    "team_total_home":   {"re": r"^team_total_home_(\d+)$",   "settle": "goals_ft_home"},
    "team_total_away":   {"re": r"^team_total_away_(\d+)$",   "settle": "goals_ft_away"},
    # --- first half 1x2 (THREE-way — de-vig shape differs) -----------------
    "1x2_1h":            {"re": r"^1x2_1h$", "settle": "result_1h",
                          "outcomes": ("home", "draw", "away")},
}

_FAMILY_RE = {k: re.compile(v["re"]) for k, v in FAMILIES.items()}

# One SQL-side prefilter that is deliberately WIDER than the anchored regexes.
# Narrowing happens in Python, where the anchors are, so a namespace we have not
# catalogued shows up in --mode coverage as unclassified rather than silently
# vanishing.
SQL_MARKET_FILTER = (
    "(o.market ~ '^(corners|cards|team_total|over_under_1h)_' "
    " OR o.market = '1x2_1h')"
)


def classify(market: str) -> tuple[str, float] | None:
    """Exact-shape classifier. Returns (family, line) or None.

    The whole point is that `corners_ou_95` and `corners_ou_home_95` land in
    different families and can never be settled against each other.
    """
    for fam, rx in _FAMILY_RE.items():
        m = rx.match(market)
        if not m:
            continue
        if not m.groups():
            return fam, 0.0
        line = decode_line(m.group(1), fam)
        if line is None:
            return None
        return fam, line
    return None


# Per family: (min plausible line, max plausible line, quarter lines occur?).
# These exist to resolve a LOSSY ENCODING, not to filter — see decode_line.
# Quarter lines (x.25 / x.75) are an Asian-totals thing and genuinely occur on
# goals markets; corner and card totals are quoted on .0/.5 lines, so allowing a
# quarter reading there would only create false ambiguity.
# Quarter lines are real on EVERY one of these families — `corners_ou_1225`
# (12.25) and `corners_1h_ou_575` (5.75) are in the table today, so the earlier
# assumption that corners are quoted only on .0/.5 was wrong.
PLAUSIBLE: dict[str, tuple[float, float]] = {
    "corners_ou":         (2.0, 20.0),
    "corners_1h_ou":      (1.0, 15.0),
    "corners_ou_1h":      (1.0, 15.0),
    "corners_home_ou":    (1.0, 12.0),
    "corners_ou_home":    (1.0, 12.0),
    "corners_away_ou":    (1.0, 12.0),
    "corners_ou_away":    (1.0, 12.0),
    "cards_ou":           (1.0, 12.0),
    "cards_ou_1h":        (1.0,  8.0),
    "over_under_1h":      (0.25, 6.5),
    "team_total_1h_home": (0.25, 6.0),
    "team_total_1h_away": (0.25, 6.0),
    "team_total_home":    (0.25, 7.0),
    "team_total_away":    (0.25, 7.0),
}

_AMBIGUOUS = 0     # counted, reported, never silently dropped


def decode_line(digits: str, family: str) -> float | None:
    """Decode the line out of a market name — and refuse when it is ambiguous.

    THE ENCODING IS LOSSY. Every writer builds the suffix as
    `str(float(line)).replace('.', '')`, so

        10.5 -> "105"   and   1.25 -> "125"
        22.5 -> "225"   and   2.25 -> "225"

    A quarter line and an x.5 line ten times larger produce the SAME string, and
    nothing else in the row distinguishes them. This is not hypothetical: the
    first run of this script decoded `over_under_1h_125` as a 12.5-GOAL
    first-half line, "settled" 634 bets against it, and every one lost — a clean
    0.000 realised over-rate against a de-vigged P(over) of 0.447. Four such
    pseudo-lines (7.5 / 12.5 / 17.5 / 22.5) were 1,377 of 2,352 first-half goals
    rows and dragged that family's calibration gap to -19.9pp. With them decoded
    properly the same family sits at +2.8pp.

    Resolution, in order:
      * fewer than 3 digits, or last two digits not `25`/`75` -> unambiguous,
        decimal point before the last digit ("25" is 2.5; 0.25 would have been
        written "025"). Returned as-is — the plausible range is a
        DISAMBIGUATOR, never a filter, so an unusual but unambiguous line is
        kept rather than quietly binned.
      * otherwise two readings exist. Take whichever falls inside the family's
        plausible range; if both or neither do, return None, count it, and drop
        the row. Guessing here is exactly the "settled against the wrong thing"
        failure this analysis was warned about.
    """
    global _AMBIGUOUS
    lo, hi = PLAUSIBLE.get(family, (0.0, 1e9))
    if len(digits) == 1:                       # defensive: an int-typed line
        return float(digits)
    a = float(f"{digits[:-1]}.{digits[-1]}")   # 105 -> 10.5
    if len(digits) < 3 or digits[-2:] not in ("25", "75"):
        return a
    b = float(f"{digits[:-2] or '0'}.{digits[-2:]}")   # 125 -> 1.25
    a_ok, b_ok = lo <= a <= hi, lo <= b <= hi
    if a_ok and not b_ok:
        return a
    if b_ok and not a_ok:
        return b
    _AMBIGUOUS += 1
    return None


def ambiguous_count() -> int:
    return _AMBIGUOUS


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------
# `yellows_home` / `reds_home` are NOT the live columns — use `yellow_cards_*` /
# `red_cards_*`. Settling on `yellows_home` produces almost nothing and looks
# like "cards are not collected".
#
# CORRECTION 2026-09-06: this comment previously said they were "LEGACY AND
# ENTIRELY NULL (0 of 1,537 …)". That is wrong — **13,081 of 54,166 rows are
# populated**. They are written by `scripts/ingest_football_data_csvs.py` (the
# football-data.co.uk backfill, which stopped in 2026-05), always in lockstep
# with `yellow_cards_*`. A backfill that stopped is not a dead column, and
# calling it one is gotcha 38 in miniature — the same mistake that has produced
# three wrong conclusions on this project.
CARDS_DEFS = {
    "yellow":     "match_stats yellow only",
    "yellow_red": "match_stats yellow + red (a red is one card)",
    "points":     "bookmaker convention: yellow=1, red=2, second yellow=3",
    "events":     "count of yellow_card/red_card rows in match_events",
}


def _cards_total(st: dict, how: str, half: bool = False,
                 ev: tuple | None = None) -> float | None:
    if how == "events":
        if ev is None:
            return None
        if half:
            return float(ev[1])
        # Bookmaker convention is yellow=1, red=2, and a player sent off for a
        # SECOND yellow totals 3 — and the raw row count already gets that case
        # right for free: his two yellows and his red are three separate rows.
        # A STRAIGHT red is the one that is short, appearing as a single row
        # where the convention wants 2. So the correction is +1 per straight
        # red, not a blanket doubling.
        straight_reds = ev[3] if len(ev) > 3 else 0
        return float(ev[0] + straight_reds)
    if half:
        y = _sum2(st.get("yellow_cards_home_ht"), st.get("yellow_cards_away_ht"))
        return y                       # no per-half red column exists
    y = _sum2(st.get("yellow_cards_home"), st.get("yellow_cards_away"))
    if y is None:
        return None
    if how == "yellow":
        return y
    # A NULL red count means "AF reported no reds", not "unknown" — AF omits the
    # row at zero. The COALESCE is right, but its stated justification was not:
    # this claimed red_cards_* is "never populated with a 0", and in fact
    # `red_cards_home = 0` appears in 8,474 AF rows (measured 2026-09-06). The
    # real support for treating NULL as zero is that where red is NULL, the
    # events table shows zero reds in 2,446 of 2,476 cases (98.8%).
    r = (st.get("red_cards_home") or 0) + (st.get("red_cards_away") or 0)
    if how == "yellow_red":
        return y + r
    # CARDS-SECOND-YELLOW-2026-09-06. `points` is the bookmaker convention
    # (yellow=1, red=2), but a naive y + 2r DOUBLE-COUNTS a second yellow: that
    # player already contributed his first yellow to `y`, and the second yellow
    # AND the red are both recorded again.
    #
    # How AF actually encodes it, measured over the whole 1.79M-row events
    # table: `event_type` has exactly two card values, `yellow_card` (443,467)
    # and `red_card` (29,589). There are **ZERO `yellow_red_card` rows** — the
    # mapping in api_football.py for "Yellow Red Card" / "Second Yellow card" is
    # dead code, because AF never sends those strings. A second yellow arrives
    # as THREE events: a yellow at minute a, then a yellow AND a red at minute b
    # for the same player. 8,731 players hold exactly 2Y+1R in one match, ~30%
    # of all reds — the correct real-world share.
    #
    # So the convention is `y + 2r - second_yellows`, and `ev` carries the
    # second-yellow count when the events path supplied it. Without events we
    # cannot identify them from match_stats alone, so fall back to y + 2r and
    # accept a small overcount (~0.07 cards/match) rather than guess.
    second_yellows = ev[2] if (ev is not None and len(ev) > 2 and ev[2] is not None) else 0
    return y + 2 * r - second_yellows


def _sum2(a, b):
    if a is None or b is None:
        return None
    return a + b


def settle_total(total: float | None, line: float, selection: str) -> float | None:
    """Return +1 (win), 0 (push), -1 (loss) as a UNIT result, or None."""
    if total is None:
        return None
    if abs(total - line) < 1e-9:
        return 0.0
    over = total > line
    won = over if selection == "over" else not over
    return 1.0 if won else -1.0


def settled_unit(row: dict, ctx: dict, cards_def: str) -> float | None:
    """+1/0/-1 for a bet, or None when the market cannot be settled."""
    fam, line, sel = row["family"], row["line"], row["selection"]
    rule = FAMILIES[fam]["settle"]
    st, m = ctx["stats"], ctx["match"]
    h1 = ctx["h1"]          # (home_goals_1h, away_goals_1h) or None

    if rule == "corners_ft":
        return settle_total(_sum2(st.get("corners_home"), st.get("corners_away")), line, sel)
    if rule == "corners_1h":
        return settle_total(_sum2(st.get("corners_home_ht"), st.get("corners_away_ht")), line, sel)
    if rule == "corners_home":
        return settle_total(st.get("corners_home"), line, sel)
    if rule == "corners_away":
        return settle_total(st.get("corners_away"), line, sel)
    if rule == "cards_ft":
        return settle_total(_cards_total(st, cards_def,
                                         ev=ctx.get("cards_from_events")), line, sel)
    if rule == "cards_1h":
        return settle_total(_cards_total(st, cards_def, half=True,
                                         ev=ctx.get("cards_from_events")), line, sel)
    if rule in ("goals_1h", "goals_1h_home", "goals_1h_away", "result_1h"):
        if h1 is None:
            return None
        gh, ga = h1
        if rule == "goals_1h":
            return settle_total(gh + ga, line, sel)
        if rule == "goals_1h_home":
            return settle_total(gh, line, sel)
        if rule == "goals_1h_away":
            return settle_total(ga, line, sel)
        res = "home" if gh > ga else "away" if ga > gh else "draw"
        return 1.0 if sel == res else -1.0
    if rule == "goals_ft_home":
        return settle_total(m.get("score_home"), line, sel)
    if rule == "goals_ft_away":
        return settle_total(m.get("score_away"), line, sel)
    return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_matches(days: int) -> dict:
    """Finished fixtures with their stats and a VALIDATED first-half score.

    First-half goals are derived from `match_events`, which is the only source
    we hold for them — `matches` has no halftime column.

    **A goal is not only `event_type = 'goal'`.** The vocabulary is
    `goal` / `penalty_scored` / `own_goal` as separate types, and `detail` is
    always the useless string `'Normal Goal'`, so it cannot be used to spot own
    goals. Counting only `'goal'` reproduced the stored full-time score on just
    2,079 of 2,839 fixtures (73pct); counting all three, with an own goal
    credited to the OTHER side, reproduces it on 4,874 of 5,504 (88.6pct) and
    nearly doubles the usable sample.

    Even so the derivation is only accepted on fixtures where the derived
    FULL-time score reproduces the stored one exactly. Everything else returns
    `h1 = None` and every first-half bet on it is dropped as unsettleable
    rather than settled against a number we cannot vouch for. `cards_from_events`
    is populated the same way, for the settle-audit's cards diagnosis.
    """
    rows = execute_query(
        """
        SELECT m.id, m.date, m.score_home, m.score_away,
               ms.corners_home, ms.corners_away,
               ms.corners_home_ht, ms.corners_away_ht,
               ms.yellow_cards_home, ms.yellow_cards_away,
               ms.yellow_cards_home_ht, ms.yellow_cards_away_ht,
               ms.red_cards_home, ms.red_cards_away
          FROM matches m
          LEFT JOIN match_stats ms ON ms.match_id = m.id
         WHERE m.status = 'finished'
           AND m.date >= now() - (%s || ' days')::interval
           AND m.date <  now()
        """,
        [days],
    )
    out = {}
    for r in rows:
        out[r["id"]] = {"match": r, "stats": r, "h1": None,
                        "cards_from_events": None}

    ev = execute_query(
        """
        SELECT me.match_id, me.minute, me.team, me.event_type, me.player_name
          FROM match_events me
          JOIN matches m ON m.id = me.match_id
         WHERE me.event_type IN ('goal', 'penalty_scored', 'own_goal',
                                 'yellow_card', 'red_card')
           AND m.status = 'finished'
           AND m.date >= now() - (%s || ' days')::interval
        """,
        [days],
    )
    tally = defaultdict(lambda: [0, 0, 0, 0])   # ft_h, ft_a, h1_h, h1_a
    # ft_cards, h1_cards, second_yellows, straight_reds
    cards = defaultdict(lambda: [0, 0, 0, 0])

    # CARDS-SECOND-YELLOW-2026-09-06. A second yellow arrives from AF as a
    # yellow AND a red at the same minute for the same player (there are ZERO
    # `yellow_red_card` rows in the entire 1.79M-row table — AF never sends that
    # string). Identify those pairs so both settlement paths can apply the
    # bookmaker convention correctly.
    yellow_keys = {
        (e["match_id"], e.get("player_name"), e["minute"])
        for e in ev if e["event_type"] == "yellow_card"
    }
    for e in ev:
        if e["event_type"] in ("yellow_card", "red_card"):
            c = cards[e["match_id"]]
            c[0] += 1
            if (e["minute"] or 0) <= 45:
                c[1] += 1
            if e["event_type"] == "red_card":
                if (e["match_id"], e.get("player_name"), e["minute"]) in yellow_keys:
                    c[2] += 1        # second yellow
                else:
                    c[3] += 1        # straight red
            continue
        t = tally[e["match_id"]]
        # An own goal is credited to the team it was scored AGAINST.
        home = (e["team"] == "home") != (e["event_type"] == "own_goal")
        t[0 if home else 1] += 1
        if (e["minute"] or 0) <= 45:
            t[2 if home else 3] += 1
    for mid, c in cards.items():
        if mid in out:
            out[mid]["cards_from_events"] = tuple(c)
    for mid, t in tally.items():
        m = out.get(mid)
        if not m:
            continue
        if t[0] == m["match"]["score_home"] and t[1] == m["match"]["score_away"]:
            m["h1"] = (t[2], t[3])
    return out


def load_quotes(days: int, lead_hours: float) -> tuple[list, list]:
    """(pick-time quotes across all books, Pinnacle closing quotes).

    Both use `DISTINCT ON ... ORDER BY timestamp DESC` — a MAX() here would be a
    high-water mark over an append-only history, not a price anyone could take
    (gotcha 30). Both are bounded by `o.timestamp <= m.date`, the authoritative
    pre-kickoff predicate that survives reschedules (gotcha 37).
    """
    pick = execute_query(
        f"""
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id, o.market, o.selection, o.bookmaker, o.odds, o.timestamp
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE m.status = 'finished'
           AND m.date >= now() - (%s || ' days')::interval
           AND m.date <  now()
           AND o.is_closing = false
           AND o.timestamp <= m.date - (%s || ' hours')::interval
           AND {SQL_MARKET_FILTER}
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [days, lead_hours],
    )
    close = execute_query(
        f"""
        SELECT DISTINCT ON (o.match_id, o.market, o.selection)
               o.match_id, o.market, o.selection, o.odds, o.timestamp
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE m.status = 'finished'
           AND m.date >= now() - (%s || ' days')::interval
           AND m.date <  now()
           AND o.bookmaker = %s
           AND o.is_closing = false
           AND o.timestamp <= m.date
           AND {SQL_MARKET_FILTER}
         ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
        """,
        [days, SHARP],
    )
    return pick, close


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------
def build_candidates(pick_rows, close_rows, args) -> tuple[list, dict]:
    """One candidate per (match, market, selection) with a de-vigged sharp
    probability at pick time, a de-vigged sharp probability at the close, and
    the best accessible price at pick time."""
    devig_fn = shin_devig if args.devig == "shin" else proportional_devig
    diag = defaultdict(int)

    # index: (match, market) -> {book: {selection: (odds, ts)}}
    idx: dict[tuple, dict] = defaultdict(lambda: defaultdict(dict))
    for r in pick_rows:
        cls = classify(r["market"])
        if cls is None:
            diag["unclassified_market_rows"] += 1
            continue
        idx[(r["match_id"], r["market"])][r["bookmaker"]][r["selection"]] = (
            float(r["odds"]), r["timestamp"])

    closing: dict[tuple, dict] = defaultdict(dict)
    closing_ts: dict[tuple, dict] = defaultdict(dict)
    for r in close_rows:
        closing[(r["match_id"], r["market"])][r["selection"]] = float(r["odds"])
        closing_ts[(r["match_id"], r["market"])][r["selection"]] = r["timestamp"]

    # Peer-lag guard (gotcha 34): per FIXTURE, the median freshness across books.
    # A dead feed falls behind its peers; overnight everything ages together, so
    # an absolute cap would delete the morning cohort instead.
    fixture_ts: dict = defaultdict(list)
    for (mid, _mkt), books in idx.items():
        for _bk, sels in books.items():
            for _s, (_o, ts) in sels.items():
                fixture_ts[mid].append(ts)
    med_ts = {mid: sorted(v)[len(v) // 2] for mid, v in fixture_ts.items()}

    out = []
    for (mid, mkt), books in idx.items():
        fam, line = classify(mkt)
        outcomes = FAMILIES[fam].get("outcomes", ("over", "under"))

        pin = books.get(SHARP)
        if not pin or any(s not in pin for s in outcomes):
            diag["no_complete_pinnacle_market"] += 1
            continue
        p_pick = devig_fn([pin[s][0] for s in outcomes])
        cl = closing.get((mid, mkt))
        if not cl or any(s not in cl for s in outcomes):
            diag["no_complete_pinnacle_close"] += 1
            continue
        p_close = devig_fn([cl[s] for s in outcomes])
        if p_pick is None or p_close is None:
            diag["devig_failed"] += 1
            continue

        for i, sel in enumerate(outcomes):
            best_o, best_bk = 0.0, None
            for bk, sels in books.items():
                if bk not in ACCESSIBLE_BOOKMAKERS or sel not in sels:
                    continue
                o, ts = sels[sel]
                lag_h = (med_ts[mid] - ts).total_seconds() / 3600.0
                if lag_h > args.max_lag_hours:
                    diag["dropped_stale_feed"] += 1
                    continue
                if o > pin[sel][0] * args.outlier_mult:
                    diag["dropped_outlier_price"] += 1
                    continue
                if o > best_o:
                    best_o, best_bk = o, bk
            if best_bk is None:
                diag["no_accessible_price"] += 1
                continue
            out.append({
                "match_id": mid, "market": mkt, "family": fam, "line": line,
                "selection": sel, "book": best_bk, "odds": best_o,
                "p_pick": p_pick[i], "p_close": p_close[i],
                "pin_odds": pin[sel][0],
                "pick_ts": pin[sel][1],
                "close_ts": closing_ts.get((mid, mkt), {}).get(sel),
                "edge": best_o * p_pick[i] - 1.0,
                "clv": best_o * p_close[i] - 1.0,
            })
    return out, diag


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def mean_sd(xs):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mu = sum(xs) / n
    if n < 2:
        return mu, 0.0
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return mu, math.sqrt(var)


def n_for_precision(sd, half_width=0.02):
    """Bets needed for a +/-`half_width` 95pct interval at this dispersion."""
    if sd <= 0:
        return 0
    return int(math.ceil((1.96 * sd / half_width) ** 2))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def mode_coverage(args):
    rows = execute_query(
        f"""
        SELECT o.market, o.bookmaker, count(*) AS n,
               count(DISTINCT o.match_id) AS matches,
               min(o.timestamp)::date AS first_seen,
               max(o.timestamp)::date AS last_seen
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE m.date >= now() - (%s || ' days')::interval
           AND o.timestamp <= m.date
           AND {SQL_MARKET_FILTER}
         GROUP BY 1, 2
        """,
        [args.days],
    )
    fam_books: dict = defaultdict(lambda: defaultdict(lambda: [0, set()]))
    unclassified: dict = defaultdict(int)
    for r in rows:
        cls = classify(r["market"])
        if cls is None:
            unclassified[r["market"]] += r["n"]
            continue
        cell = fam_books[cls[0]][r["bookmaker"]]
        cell[0] += r["n"]
        cell[1].add(r["market"])

    t = Table(title=f"New-market namespaces, pre-kickoff rows, last {args.days}d")
    for c in ("family", "Pinnacle rows", "accessible books (rows)", "other books"):
        t.add_column(c, overflow="fold")
    for fam in FAMILIES:
        books = fam_books.get(fam)
        if not books:
            t.add_row(fam, "-", "[red]NONE[/red]", "-")
            continue
        acc = ", ".join(f"{b} {v[0]}" for b, v in sorted(books.items())
                        if b in ACCESSIBLE_BOOKMAKERS) or "[red]NONE[/red]"
        oth = ", ".join(f"{b}" for b in sorted(books) if b not in ACCESSIBLE_BOOKMAKERS
                        and b != SHARP) or "-"
        t.add_row(fam, str(books.get(SHARP, [0, set()])[0]) or "0", acc, oth)
    console.print(t)

    if unclassified:
        console.print("[yellow]Markets matched by the SQL prefilter but by no "
                      "anchored family regex (a new namespace, or a writer "
                      "changed spelling):[/yellow]")
        for m, n in sorted(unclassified.items(), key=lambda x: -x[1])[:20]:
            console.print(f"  {m}: {n}")

    # Pairing is what the strategy actually needs: a Pinnacle market AND an
    # accessible price on the same fixture and the same exact shape.
    pick, close = load_quotes(args.days, args.lead_hours)
    cands, diag = build_candidates(pick, close, args)
    by_fam = defaultdict(int)
    for c in cands:
        by_fam[c["family"]] += 1
    t2 = Table(title="Pinnacle-vs-accessible PAIRED selections "
                     f"(pick = latest quote {args.lead_hours}h+ before KO)")
    t2.add_column("family")
    t2.add_column("paired selections", justify="right")
    for fam in FAMILIES:
        t2.add_row(fam, str(by_fam.get(fam, 0)))
    console.print(t2)
    console.print(f"[dim]diagnostics: {dict(diag)}[/dim]")


def mode_settle_audit(args):
    """Prove the settlement columns mean what we think.

    Method: de-vigged Pinnacle P(over) is a very good forecast. If our
    settlement rule is right, the realised over-rate on a bucket of markets
    should track the mean de-vigged P(over) closely. If we settle cards on the
    wrong definition, or corners on the wrong column, the two diverge.
    """
    matches = load_matches(args.days)
    pick, close = load_quotes(args.days, 0.0)
    cands, _ = build_candidates(pick, close, args)

    console.print("[bold]Column reality check[/bold]")
    r = execute_query(
        """
        SELECT count(*) AS n,
               count(ms.corners_home) AS corners_ft,
               count(ms.corners_home_ht) AS corners_1h,
               count(ms.yellows_home) AS legacy_yellows,
               count(ms.yellow_cards_home) AS yellow_cards,
               count(ms.red_cards_home) AS red_cards
          FROM match_stats ms JOIN matches m ON m.id = ms.match_id
         WHERE m.status = 'finished'
           AND m.date >= now() - (%s || ' days')::interval
        """,
        [args.days],
    )[0]
    console.print(f"  finished fixtures with a stats row : {r['n']}")
    console.print(f"  corners_home (FT)                  : {r['corners_ft']}")
    console.print(f"  corners_home_ht (1H)               : {r['corners_1h']}")
    console.print(f"  yellows_home (LEGACY)              : {r['legacy_yellows']}"
                  "   <- 0 means the column is dead; use yellow_cards_*")
    console.print(f"  yellow_cards_home                  : {r['yellow_cards']}")
    console.print(f"  red_cards_home                     : {r['red_cards']}")
    h1_ok = sum(1 for v in matches.values() if v["h1"] is not None)
    console.print(f"  first-half score derivable+validated: {h1_ok}/{len(matches)}")

    # over-rate vs de-vigged P(over), per family and per cards definition
    console.print("\n[bold]Realised over-rate vs de-vigged Pinnacle P(over)[/bold]")
    t = Table()
    t.add_column("family / cards def", overflow="fold", min_width=26)
    for c in ("n", "mean P(over)", "realised over-rate", "gap (pp)"):
        t.add_column(c, justify="right")

    def bucket(rows, cards_def):
        ps, ys = [], []
        for c in rows:
            if c["selection"] != "over":
                continue
            ctx = matches.get(c["match_id"])
            if not ctx:
                continue
            u = settled_unit(c, ctx, cards_def)
            if u is None or u == 0.0:
                continue
            ps.append(c["p_pick"])
            ys.append(1.0 if u > 0 else 0.0)
        return ps, ys

    for fam in FAMILIES:
        if FAMILIES[fam].get("outcomes"):
            continue
        rows = [c for c in cands if c["family"] == fam]
        if not rows:
            continue
        defs = CARDS_DEFS if fam.startswith("cards") else {"yellow_red": ""}
        for d in defs:
            ps, ys = bucket(rows, d)
            if not ps:
                continue
            label = fam if not fam.startswith("cards") else f"{fam} ({d})"
            gap = (sum(ys) / len(ys) - sum(ps) / len(ps)) * 100
            t.add_row(label, str(len(ps)), f"{sum(ps)/len(ps):.3f}",
                      f"{sum(ys)/len(ys):.3f}", f"{gap:+.1f}")
    console.print(t)
    console.print("[dim]A settlement rule that is right should sit within a "
                  "couple of pp of the sharp forecast. A double-digit gap means "
                  "the column or the definition is wrong, not that the market "
                  "is mispriced.[/dim]")


def evaluate(cands, matches, args, p_key="p_pick"):
    """Apply the edge rule and settle. Returns (clv list, pnl list, rows)."""
    clv, pnl, kept = [], [], []
    for c in cands:
        edge = c["odds"] * c[p_key] - 1.0
        if edge < args.edge:
            continue
        if c["odds"] < args.min_odds or c["odds"] > args.max_odds:
            continue
        kept.append(c)
        clv.append(c["clv"])
        ctx = matches.get(c["match_id"])
        u = settled_unit(c, ctx, args.cards_def) if ctx else None
        if u is None:
            continue
        pnl.append(0.0 if u == 0.0 else (c["odds"] - 1.0) if u > 0 else -1.0)
    return clv, pnl, kept


def mode_backtest(args):
    matches = load_matches(args.days)
    pick, close = load_quotes(args.days, args.lead_hours)
    cands, diag = build_candidates(pick, close, args)
    if args.family:
        cands = [c for c in cands if c["family"] == args.family]

    console.print(f"[bold]NEW-MARKETS-LINESHOP backtest[/bold] — last {args.days}d, "
                  f"pick at latest quote >= {args.lead_hours}h before KO, "
                  f"de-vig={args.devig}, edge>={args.edge:.1%}, "
                  f"cards settled as '{args.cards_def}'")
    console.print(f"[dim]candidate selections: {len(cands)}   diagnostics: "
                  f"{dict(diag)}   ambiguous line codes dropped: "
                  f"{ambiguous_count()}[/dim]")

    # --- is CLV even measurable on this data? ------------------------------
    real_hist = [c for c in cands
                 if c["close_ts"] is not None and c["pick_ts"] < c["close_ts"]]
    frac = len(real_hist) / len(cands) if cands else 0.0
    if frac < 0.05:
        console.print(
            "\n[bold red]CLV IS NOT MEASURABLE ON THIS SAMPLE.[/bold red] "
            f"Only {len(real_hist)} of {len(cands)} candidate selections "
            f"({frac:.1%}) have a pick snapshot strictly EARLIER than the "
            "Pinnacle close. The rest are the 7-day AF backfill, which stamps "
            "every row at kickoff minus one minute, so pick price and closing "
            "anchor are the same row and CLV collapses to the entry edge. "
            "Read the CLV column below as arithmetic, not as evidence.\n")
    else:
        console.print(f"[green]CLV measurable on {len(real_hist)} of "
                      f"{len(cands)} selections ({frac:.1%}).[/green]\n")

    t = Table(title="Per family")
    for c in ("family", "picks", "settled", "ROI", "ROI SE", "ROI 95pct CI",
              "n for ROI +/-2pct", "CLV-able", "mean CLV", "t(CLV)"):
        t.add_column(c, justify="right")

    fams = sorted({c["family"] for c in cands})
    rows_for_placebo = []
    for fam in fams + ["ALL"]:
        pool = cands if fam == "ALL" else [c for c in cands if c["family"] == fam]
        clv, pnl, kept = evaluate(pool, matches, args)
        if not kept:
            continue
        if fam == "ALL":
            rows_for_placebo = pool
        pmu, psd = mean_sd(pnl)
        se = (psd / math.sqrt(len(pnl))) if pnl else 0.0
        # CLV only over rows with genuine history — see the docstring.
        hist = [c for c in kept
                if c["close_ts"] is not None and c["pick_ts"] < c["close_ts"]]
        cmu, csd = mean_sd([c["clv"] for c in hist])
        tstat = (cmu / (csd / math.sqrt(len(hist)))) if csd > 0 and len(hist) > 1 else 0.0
        t.add_row(
            fam, str(len(kept)), str(len(pnl)),
            f"{pmu:+.2%}" if pnl else "-",
            f"{se:.2%}" if pnl else "-",
            f"[{pmu - 1.96 * se:+.1%}, {pmu + 1.96 * se:+.1%}]" if pnl else "-",
            str(n_for_precision(psd)) if pnl else "-",
            str(len(hist)),
            f"{cmu:+.2%}" if hist else "n/a",
            f"{tstat:+.2f}" if len(hist) > 1 else "n/a",
        )
    console.print(t)
    console.print("[dim]ROI carries its standard error and interval because at "
                  "a per-bet sd near 1.4 it takes ~19,400 bets to resolve "
                  "+/-2pct. CLV would need ~334 and is the gate we WANT to use "
                  "(gotcha 8) — it is simply not available yet here.[/dim]")

    # ---- placebo ---------------------------------------------------------
    if not rows_for_placebo:
        console.print("[yellow]No picks — nothing to placebo.[/yellow]")
        return
    clv, pnl, kept = evaluate(rows_for_placebo, matches, args)
    real_roi, _ = mean_sd(pnl)

    # ---- placebo ---------------------------------------------------------
    #
    # A probability shuffle is NOT usable as the null for this rule, and it is
    # worth writing down why rather than reporting the flattering z it gives.
    # The rule is `odds x devig(Pinnacle) - 1 >= threshold`, and in reality
    # `odds` is approximately `1 / p` — the price and the probability are two
    # views of the same number. Break that coupling and the rule fires
    # constantly: shuffling p globally selected 11,016 selections against the
    # real 732, and even shuffling within (family, line, selection) selected
    # 8,464. Against a pool whose baseline is the vig (-6.5pct to -8.4pct) any
    # real rule "wins" at z > 16. That measures "does the probability belong to
    # the same market as the price", which was never in question.
    #
    # The null that actually tests the claim holds n FIXED and permutes the
    # OUTCOMES: within each (family, line, selection) stratum, reassign the
    # realised results among the candidates. Everything about the selection —
    # which bets, at what odds, how many — is untouched, so the only question
    # left is whether the selected bets won more than exchangeable outcomes in
    # the same strata would have.
    rng = random.Random(args.seed)
    settleable = []
    for c in rows_for_placebo:
        ctx = matches.get(c["match_id"])
        u = settled_unit(c, ctx, args.cards_def) if ctx else None
        if u is not None:
            settleable.append((c, u))
    strata: dict[tuple, list[int]] = defaultdict(list)
    for i, (c, _u) in enumerate(settleable):
        strata[(c["family"], c["line"], c["selection"])].append(i)

    chosen = {id(c) for c in kept}
    sim_roi = []
    for _ in range(args.placebo):
        units = [u for _c, u in settleable]
        for idxs in strata.values():
            vals = [units[i] for i in idxs]
            rng.shuffle(vals)
            for i, v in zip(idxs, vals):
                units[i] = v
        pl = []
        for (c, _u), u in zip(settleable, units):
            if id(c) not in chosen:
                continue
            pl.append(0.0 if u == 0.0 else (c["odds"] - 1.0) if u > 0 else -1.0)
        if pl:
            sim_roi.append(mean_sd(pl)[0])

    def z_of(real, sims):
        mu, sd = mean_sd(sims)
        return (real - mu) / sd if sd > 0 else float("nan")

    console.print(f"\n[bold]Placebo[/bold] ({args.placebo} permutations of the "
                  "realised OUTCOMES within (family, line, selection); the "
                  "selected bets, their odds and n are held fixed)")
    if sim_roi and pnl:
        console.print(f"  ROI   real {real_roi:+.2%}   placebo "
                      f"{mean_sd(sim_roi)[0]:+.2%} +/- {mean_sd(sim_roi)[1]:.2%}"
                      f"   z = {z_of(real_roi, sim_roi):+.2f}")
        console.print("[dim]A real effect should sit far outside the placebo "
                      "cloud. CORNERS-EDGE-TAIL cleared its placebo at z=+11.47; "
                      "a rejected idea sat at z=1.84. Note this null cannot "
                      "vindicate a family whose SETTLEMENT is wrong — see "
                      "--mode settle-audit first.[/dim]")

    # ---- honesty about power --------------------------------------------
    _, psd = mean_sd(pnl)
    need_roi = n_for_precision(psd)
    console.print(f"\n[bold]Power[/bold]: {len(pnl)} settled picks in hand. "
                  f"ROI needs {need_roi} for a +/-2pct interval "
                  f"({need_roi / max(len(pnl), 1):.0f}x more). CLV would need "
                  "~334 but is not yet measurable at all (see above). "
                  "Collection on these markets started 2026-09-05 plus a 7-day "
                  "AF backfill, so no verdict is available yet — re-run this "
                  "script as the live window grows.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("coverage", "settle-audit", "backtest"),
                    default="backtest")
    ap.add_argument("--days", type=int, default=20,
                    help="lookback over FINISHED fixtures (default 20)")
    ap.add_argument("--lead-hours", type=float, default=0.0,
                    help="pick uses the latest quote at least this long before "
                         "kickoff; CLV is measured against Pinnacle's close, so "
                         "0 makes the two the same snapshot. Default is 0 "
                         "because ALMOST NO REAL PRE-KICKOFF HISTORY EXISTS ON "
                         "THESE MARKETS YET - see the BACKFILL TIMESTAMP note "
                         "in the module docstring.")
    ap.add_argument("--edge", type=float, default=0.02,
                    help="multiplicative edge floor: odds x devig(Pinnacle) - 1")
    ap.add_argument("--devig", choices=("shin", "proportional"), default="shin")
    ap.add_argument("--family", default=None, help="restrict to one family")
    # CARDS-SECOND-YELLOW-2026-09-06: was "yellow_red", which scores a red as
    # ONE card and is the least correct of the four. "points" is the bookmaker
    # convention (yellow=1, red=2, second yellow=3).
    ap.add_argument("--cards-def", choices=tuple(CARDS_DEFS), default="points")
    ap.add_argument("--outlier-mult", type=float, default=1.30,
                    help="reject an accessible price above Pinnacle x this "
                         "(gotcha 9 — a 4.5+ quote on a 2.5 line is a "
                         "mislabelled market, not an edge)")
    ap.add_argument("--max-lag-hours", type=float, default=6.0,
                    help="drop a book whose newest quote on a fixture is this "
                         "far behind the fixture's median book (gotcha 34)")
    ap.add_argument("--min-odds", type=float, default=1.20)
    ap.add_argument("--max-odds", type=float, default=6.0)
    ap.add_argument("--placebo", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260906)
    args = ap.parse_args()

    if args.mode == "coverage":
        mode_coverage(args)
    elif args.mode == "settle-audit":
        mode_settle_audit(args)
    else:
        mode_backtest(args)


if __name__ == "__main__":
    main()
