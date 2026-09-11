#!/usr/bin/env python3
"""FLOOR-GRID-SWEEP — a general dimensional cube over settled PRE-MATCH picks.

Sweeps the (edge floor x odds floor) grid inside ANY grouping of ANY dimensions,
over EVERY market we have picks for — including the ones we neither bet nor
publish (asian_handicap, corners, team totals, BTTS, double chance, 1x2_1h) so
the same tool can validate a configuration for a market before we ever offer it.

Built 2026-09-11 on the owner's request: "think further than i suggested, make it
so detailed and thorough, maybe even more than we need for this task, but so
that we can check any kind of dimension combinations from it later... you can
even include other markets and bet types... even the ones we dont offer picks
[for] or bet on, e.g. AH, corners, cards... and a single market has multiple bet
types, e.g. corners has ou85 ou95 etc."

WHY A CUBE AND NOT ANOTHER REPORT
---------------------------------
The 1x2 edge floor changed four times (0.03 -> 0.10 -> 0.15 -> 0.10 -> 0.13,
three of them in ONE day) because each sweep answered a narrower question than
the last, and none was re-runnable along a new axis:

  * `edge_floor_backtest.py`    edge floors only, market pooled, no selection split
  * `favlong_floor_backtest.py` fav-vs-long, later per selection, ONE odds floor
  * `bot_2d_audit.py`           the 2D matrix but PER BOT (held-out), not per market

So every new question needed new code, and new code meant new silent choices of
basis/metric/sample — the three axes ANALYSIS_GOTCHAS blames for the churn. This
tool fixes the METHOD and opens the DIMENSIONS.

THE METHOD IS FIXED — do not add options that soften these
-----------------------------------------------------------
* EXECUTABLE price: `COALESCE(odds_at_pick_live, odds_at_pick)`. Bare
  `odds_at_pick` is a snapshot high-water mark overstating by ~+0.25 decimal
  points (STALE-BEST-ODDS): not a price we could have taken.
* ONE FOLD PARTITION per (dataset, market), shared by every group and cell. The
  first version built folds per group, and the robust flag then depended on the
  grouping: `HOME (all odds)` and `HOME-DOG >=2.80` at edge>=10%/odds>=3.20 are
  the SAME bets, yet one printed robust and the other did not, purely because
  the boundaries came from 407 rows vs 236. A verdict that moves with the
  grouping is not a property of the bets.
* ROBUST = every fold has bets AND every fold is positive. An empty fold FAILS:
  "it never lost in a window it never traded in" is not evidence.
* MIN-N gate: a cell is ADOPTABLE only at n >= --min-n. Without it the grid
  recommends 4-bet cells at +300%, which is how the 15% floor was adopted and
  reverted inside a single day.
* PRE-MATCH ONLY, always. `inplay%` bots are excluded in SQL with no override.
  In-play betting was retired 2026-08-21, it is a different bet type, and on
  2026-09-11 it single-handedly faked an away-selection result: +15.4% "robust"
  on n=364 that was really retired in-play bots, one of them n=14 at +452%.
  See ANALYSIS_GOTCHAS §47 ("an odds-band effect is a BOT effect until you
  split by bot").

MARKETS, FAMILIES, LINES AND BET TYPES
---------------------------------------
A market family spans many bet types and the line lives in a DIFFERENT place
depending on the family, which is why a naive `GROUP BY market` misleads:

  * `over_under_25` / `over_under_35`   line in the MARKET name      -> o/u 2.5, 3.5
  * `corners_ou_85` / `corners_ou_95`   line in the MARKET name      -> corners 8.5, 9.5
  * `team_total_home_15`                side AND line in the MARKET  -> team_total home 1.5
  * `asian_handicap` sel `home -0.5`    line in the SELECTION        -> ah home -0.5
  * `1x2`, `btts`, `double_chance`      no line

So the cube exposes `market` (raw), `family` (folded), `line`, `side` and
`bet_type` (family+line, the thing you would actually configure) as separate
dimensions. `family` delegates to `workers.canonical_market.market_family`
first — the shared vocabulary the placer's floors key on — and only folds what
that leaves unfolded (corners, team totals), so there is no second copy of the
o/u mapping.

DATASETS — separate, never unioned (gotcha 18: one ledger per bot family)
-------------------------------------------------------------------------
  sim/calibrated       simulated_bets, maturity=calibrated      (real-money slice)
  sim/cohort           simulated_bets, calibrated+beta+active   (working cohort)
  sim/all-prematch     simulated_bets, every pre-match bot      (incl. retired)
  shadow/all-prematch  shadow_bets_unique, every pre-match bot  (largest; always
                       the deduped VIEW, never the base table — gotcha 5)

Agreement ACROSS datasets is the signal. A cell robust in the calibrated slice
AND the largest one is a floor; robust in only one is a regime.

USAGE
-----
  python3 scripts/floor_grid_sweep.py --list-dims          # what can I ask?
  python3 scripts/floor_grid_sweep.py                      # default report
  python3 scripts/floor_grid_sweep.py --group-by bet_type --summary-only
  python3 scripts/floor_grid_sweep.py --market 1x2 --group-by sel_band,bookmaker
  python3 scripts/floor_grid_sweep.py --group-by league_tier --filter sel_band=home-dog
  python3 scripts/floor_grid_sweep.py --family corners --group-by line
  python3 scripts/floor_grid_sweep.py --family asian_handicap --group-by side,line
  python3 scripts/floor_grid_sweep.py --metric clv_pinnacle --group-by sel_band
  python3 scripts/floor_grid_sweep.py --edge-floors 0,5,10,11,12,13,14 \\
                                      --odds-floors 2.8,3.0,3.2,3.4,3.6
  python3 scripts/floor_grid_sweep.py --dump /tmp/picks.csv   # ask anything later
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

DEFAULT_EDGE_FLOORS = [0.00, 0.03, 0.05, 0.08, 0.10, 0.12, 0.13, 0.15, 0.18, 0.20]
DEFAULT_ODDS_FLOORS = [1.00, 1.50, 1.80, 2.00, 2.20, 2.50, 2.80, 3.20, 4.00]

DATASETS = [
    ("sim/calibrated",      "simulated_bets",     "calibrated"),
    ("sim/cohort",          "simulated_bets",     "cohort"),
    ("sim/all-prematch",    "simulated_bets",     "all"),
    ("shadow/all-prematch", "shadow_bets_unique", "all"),
]

# ── the SCALE dataset, and its one hard limitation ───────────────────────────
# Added 2026-09-11 after the owner asked the right question of an earlier
# answer: "on what dataset? 1k fixtures? 25k? 50k? 100k?"
#
# The honest size of the four datasets above is ~18,700 settled pre-match picks
# across ALL markets and both ledgers — for 1x2 alone it is ~4,500, and the
# individual grid CELLS that produced the headline numbers were n=100-260. The
# repo's own precision note says ~9,300 settled bets are needed for +/-2% on
# ROI (~334 on CLV). So a cell of 130 bets is suggestive, never decisive, and
# calling three overlapping windows "every dataset agreeing" overstated it.
#
# This dataset is fixture-level instead of pick-level: for every FINISHED
# fixture (167,957 in the DB) where both Pinnacle and an accessible book priced
# the market, take the best accessible price and score it against the
# Shin-de-vigged Pinnacle probability. That reaches ~100k+ synthetic bets.
#
# ⚠️ IT IS BLIND TO THE ODDS AXIS, AND THAT IS NOT A NUANCE.
# Taking the best of many books systematically RESCUES low-odds picks we could
# never actually have taken at that price (ANALYSIS_GOTCHAS §52/§55, the
# line-shop mirage). So its absolute ROI is fantasy, and — critically for this
# tool — the distortion lands ON the odds dimension. The repo already recorded
# this: "Idealized 104k/182k confirms the edge floors are robust at scale but is
# blind to the odds effect ... which is why the odds floor is validated on
# executable + CLV, not idealized."
#
# Therefore: use `idealized/fixture-level` for the EDGE axis at scale, and NEVER
# to choose an odds floor. `--metric clv_pinnacle` on the executable datasets is
# the sanctioned way to probe the odds question at our real sample size.
IDEALIZED_LABEL = "idealized/fixture-level"

# `roi` is the default and the only basis a floor decision should rest on. The
# CLV metrics are for DIAGNOSIS: CLV converges far faster than ROI (~334 settled
# bets for +/-2% vs ~9,300), so on a thin market CLV can say "there is an edge
# here" long before ROI can, and that is exactly the situation for the markets
# we do not yet bet. Never adopt a floor on CLV alone.
METRICS = {
    "roi":          ("ret",          "ROI%"),
    "clv":          ("clv",          "CLV%"),
    "clv_pinnacle": ("clv_pinnacle", "CLVpin%"),
    "clv_live":     ("clv_live",     "CLVlive%"),
}

DIMENSIONS = {
    "dataset":        "ledger + maturity slice (see DATASETS)",
    "market":         "raw market string as stored (e.g. corners_ou_95)",
    "family":         "folded family: 1x2, o/u, corners_ou, team_total, asian_handicap, btts, ...",
    "line":           "the line, wherever it lives (2.5, 9.5, -0.5, '-' if none)",
    "side":           "over/under/home/away/draw where meaningful",
    "bet_type":       "family + line — the thing you would actually configure",
    "selection":      "raw selection string",
    "sel_band":       "1x2 only: home-fav <2.00 / home-mid 2.00-2.80 / home-dog >=2.80 / draw / away; else = side",
    "odds_band":      "executable-odds bucket (<1.8, 1.8-2.2, 2.2-2.8, 2.8-3.2, 3.2-4.0, 4.0+)",
    "edge_band":      "edge bucket (<5%, 5-8%, 8-10%, 10-13%, 13-18%, 18%+)",
    "edge_scale":     "fraction (the convention, 0..1) | SUSPECT (>1 — different unit, see --include-suspect-edge)",
    "edge_kind":      "WHAT the edge is measured against: model | sharp-anchor | line-shop. NEVER pool these on one floor axis",
    "bookmaker":      "recommended_bookmaker — the book the pick was priced at",
    "closing_book":   "closing_bookmaker — where the closing line came from",
    "bot":            "bot name",
    "maturity":       "bot maturity_label",
    "retired":        "y/n — was the bot retired",
    "model_version":  "model bundle that produced the prediction",
    "timing_cohort":  "when in the pick lifecycle it was taken",
    "strategy":       "strategy_profile",
    "league":         "league name",
    "league_country": "league country",
    "league_tier":    "league tier (t1 = top flight)",
    "month":          "pick_time year-month",
    "dow":            "kickoff day of week",
    "ko_hour":        "kickoff hour UTC bucket (00-05/06-11/12-17/18-23)",
}


# ── family / line / side parsing ─────────────────────────────────────────────
_LINE_IN_MARKET = re.compile(r"^(?P<base>.*?)_(?P<num>\d{2,3})$")
_LINE_IN_SEL = re.compile(r"(?P<sign>[+-]?)(?P<num>\d+(?:\.\d+)?)\s*$")


def _decode_line_token(tok: str) -> float | None:
    """`25` -> 2.5, `95` -> 9.5, `105` -> 10.5, `05` -> 0.5.

    These suffixes are the stored convention (over_under_25, corners_ou_105,
    team_total_home_05): the trailing digit is the decimal. Two-digit tokens are
    d.d and three-digit are dd.d.
    """
    if not tok.isdigit():
        return None
    return int(tok[:-1]) + (int(tok[-1]) / 10.0)


def parse_market(market: str, selection: str) -> tuple[str, str, str]:
    """-> (family, line, side). Never raises; unknown shapes pass through.

    Delegates the folded family to `workers.canonical_market.market_family`
    (the vocabulary the placer's floors key on) and only folds what that leaves
    alone — corners and team totals — so the o/u mapping has no second copy.
    """
    m = (market or "").lower().strip()
    s = (selection or "").lower().strip()
    try:
        from workers.canonical_market import market_family
        fam = market_family(m) or m
    except Exception:  # noqa: BLE001
        fam = m
    line, side = "-", ""

    # o/u families: canonical_market already folds over_under_* -> 'o/u', which
    # loses the line, so recover it from the raw market name.
    mm = _LINE_IN_MARKET.match(m)
    if m.startswith("over_under") and mm:
        v = _decode_line_token(mm.group("num"))
        line = f"{v:g}" if v is not None else "-"
        side = "over" if s.startswith("over") else "under" if s.startswith("under") else s
    elif m.startswith("corners_ou") and mm:
        fam = "corners_ou"
        v = _decode_line_token(mm.group("num"))
        line = f"{v:g}" if v is not None else "-"
        side = "over" if s.startswith("over") else "under" if s.startswith("under") else s
    elif m.startswith("team_total") and mm:
        base = mm.group("base")            # team_total_home / team_total_away
        fam = "team_total"
        side = base.rsplit("_", 1)[-1]     # home / away
        v = _decode_line_token(mm.group("num"))
        line = f"{v:g}" if v is not None else "-"
    elif fam == "asian_handicap":
        # line lives in the SELECTION: "home -0.5", "away +0.5", "home +0"
        sm = _LINE_IN_SEL.search(s)
        if sm:
            line = f"{float(sm.group('sign') + sm.group('num')):g}"
        side = "home" if s.startswith("home") else "away" if s.startswith("away") else s
    else:
        side = s
    return fam, line, side


# Sharp-anchored picks measure a DIFFERENT quantity, and this is the single
# most dangerous thing to pool on an edge-floor axis (owner asked about it
# directly: "model edge %, odds, sharp edge").
#
#   model edge  = calibrated_prob - 1/book_odds        (vs OUR probability)
#   sharp edge  = P_sharp - 1/book_odds                (vs de-vigged Pinnacle,
#                                                       a near-TRUE line)
#   line-shop   = best-of-books vs a reference price
#
# A 3% sharp edge is a real 3% overlay on a near-true line; a 3% model edge is
# noise. That is exactly why `pick_triggers._SHARP_MIN_EDGE_BY_MARKET` is 3%
# while the model floors are 13%/8% — see BETTING_GATE_DECISIONS "Sharp-anchor
# trigger floors". Sweeping one floor axis across both kinds silently asks
# "what edge floor suits two incompatible definitions at once", which has no
# answer. So the kind is a first-class dimension, and `--group-by edge_kind`
# (or a filter) is the correct way to look at either.
#
# Discriminators: the sentinel model_version `pinnacle_shin_devig` marks
# sharp-anchored rows (0 settled as of 2026-09-11 — the sharp trigger bots are
# young), and the `bot_pin_*` / `bot_sweep_*` families are line-shop.
_SHARP_MV = "pinnacle_shin_devig"


def _edge_kind(bot: str, model_version: str | None) -> str:
    if (model_version or "").lower() == _SHARP_MV:
        return "sharp-anchor"
    b = (bot or "").lower()
    if "trigger_sharp" in b:
        return "sharp-anchor"
    if b.startswith(("bot_pin_", "bot_sweep_")) or "lineshop" in b:
        return "line-shop"
    return "model"


def _sel_band(family: str, selection: str, odds: float, side: str) -> str:
    """The bands 1x2 floor decisions are made on; every other family falls back
    to its side so the dimension is always meaningful.

    The home bands are ODDS-defined, which is why they interact with the
    odds-floor axis by construction: an odds floor above a band empties it
    (n=0), and that emptiness is the ANSWER to "are home favs excluded
    automatically by the 2.80 floor?" rather than missing data.
    """
    if family != "1x2":
        return side or "?"
    s = (selection or "").lower()
    if s == "home":
        return "home-fav" if odds < 2.00 else "home-mid" if odds < 2.80 else "home-dog"
    return s or "?"


def _bucket(v, edges, labels):
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


# ── loading ──────────────────────────────────────────────────────────────────
def _sql(table: str, maturity: str) -> str:
    if maturity == "calibrated":
        mat = "AND b.maturity_label = 'calibrated'"
    elif maturity == "cohort":
        mat = "AND b.maturity_label IN ('calibrated','beta','active')"
    else:
        mat = ""
    return f"""
        SELECT lower(x.market) AS market, lower(x.selection) AS selection,
               x.edge_percent::float AS ep,
               COALESCE(x.odds_at_pick_live, x.odds_at_pick)::float AS odds,
               x.pick_time, m.date AS kickoff, x.result,
               x.clv::float AS clv, x.clv_pinnacle::float AS clv_pinnacle,
               x.clv_live::float AS clv_live,
               x.recommended_bookmaker AS bookmaker,
               x.closing_bookmaker AS closing_book,
               x.model_version, x.timing_cohort,
               x.strategy_profile AS strategy,
               b.name AS bot, b.maturity_label AS maturity,
               (b.retired_at IS NOT NULL) AS retired,
               l.name AS league, l.country AS league_country, l.tier AS league_tier,
               (CASE WHEN x.result = 'won'
                     THEN (COALESCE(x.odds_at_pick_live, x.odds_at_pick) - 1)
                     ELSE -1 END)::float AS ret
          FROM {table} x
          JOIN bots b    ON b.id = x.bot_id
          JOIN matches m ON m.id = x.match_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE x.result IN ('won','lost')
           AND x.edge_percent IS NOT NULL
           AND COALESCE(x.odds_at_pick_live, x.odds_at_pick) IS NOT NULL
           AND b.name NOT LIKE 'inplay%%'      -- PRE-MATCH ONLY. No override.
           {mat}
    """


def _load_idealized(market_pred: str, sels: tuple, fam: str) -> list[dict]:
    """Fixture-level synthetic bets: best accessible price vs de-vig Pinnacle.

    Reuses `edge_floor_backtest`'s own SQL shape and `_devig` so there is no
    second implementation of the idealized basis. Unlike that version this one
    KEEPS selection and odds, which is what lets the cube group it by selection
    band — but see IDEALIZED_LABEL above: the odds axis is not trustworthy here.
    """
    from scripts.edge_floor_backtest import _devig, _ACCESSIBLE
    books = ",".join("'%s'" % b for b in _ACCESSIBLE)
    rows = execute_query(f"""
        WITH fin AS (
          SELECT id, date, score_home, score_away,
                 CASE WHEN score_home>score_away THEN 'home'
                      WHEN score_home<score_away THEN 'away' ELSE 'draw' END winner,
                 (score_home+score_away) total
            FROM matches WHERE status='finished'
              AND score_home IS NOT NULL AND score_away IS NOT NULL),
        lat AS (
          SELECT DISTINCT ON (o.match_id,o.market,o.selection,o.bookmaker)
                 o.match_id, o.market, o.selection, o.bookmaker, o.odds
            FROM odds_snapshots o
           WHERE {market_pred} AND o.selection IN ({','.join("'%s'" % s for s in sels)})
             AND o.bookmaker IN ('Pinnacle',{books})
           ORDER BY o.match_id,o.market,o.selection,o.bookmaker,o."timestamp" DESC),
        agg AS (
          SELECT match_id, market, selection,
                 max(odds) FILTER (WHERE bookmaker='Pinnacle') pin,
                 max(odds) FILTER (WHERE bookmaker!='Pinnacle') best
            FROM lat GROUP BY 1,2,3)
        SELECT a.match_id::text mid, a.market, a.selection,
               a.pin::float pin, a.best::float best,
               f.date, f.winner, f.total
          FROM agg a JOIN fin f ON f.id=a.match_id
         WHERE a.pin IS NOT NULL AND a.best IS NOT NULL AND a.pin>1 AND a.best>1
    """)
    from collections import defaultdict
    grp, meta = defaultdict(dict), {}
    for r in rows:
        grp[(r["mid"], r["market"])][r["selection"]] = {"pin": r["pin"], "best": r["best"]}
        meta[(r["mid"], r["market"])] = r
    out = []
    for key, od in grp.items():
        if not all(s in od for s in sels):
            continue
        truep = _devig({s: od[s]["pin"] for s in sels})
        r = meta[key]
        for s in sels:
            best = od[s]["best"]
            edge = best * truep.get(s, 0) - 1
            if fam == "1x2":
                won = (r["winner"] == s)
                line = None
            else:
                line = int(r["market"].split("_")[-1]) / 10.0
                won = (r["total"] > line) if s == "over" else (r["total"] < line)
            out.append({
                "market": r["market"], "selection": s, "ep": edge, "odds": best,
                "pick_time": r["date"], "kickoff": r["date"],
                "result": "won" if won else "lost",
                "ret": (best - 1) if won else -1,
                "clv": None, "clv_pinnacle": None, "clv_live": None,
                "bookmaker": "best-of-accessible", "closing_book": "?",
                "model_version": "devig_pinnacle", "timing_cohort": "?",
                "strategy": "?", "bot": "(fixture-level)", "maturity": "n/a",
                "retired": False, "league": "?", "league_country": "?",
                "league_tier": None,
            })
    return out


def load(datasets=None, include_idealized: bool = True) -> list[dict]:
    out: list[dict] = []
    raw: list[tuple[str, dict]] = []
    for label, table, maturity in DATASETS:
        if datasets and label not in datasets:
            continue
        for r in execute_query(_sql(table, maturity)):
            raw.append((label, dict(r)))
    # ON BY DEFAULT. The pick-level ledgers total ~18,700 settled bets across
    # ALL markets, so the cells that decide a floor are 100-260 bets — noise on
    # a slate that runs ~1,000 fixtures a day. The fixture-level basis is the
    # only one at real scale, so it loads unless explicitly skipped.
    if include_idealized and (not datasets or IDEALIZED_LABEL in datasets):
        for r in _load_idealized("o.market='1x2'", ("home", "draw", "away"), "1x2"):
            raw.append((IDEALIZED_LABEL, r))
        for r in _load_idealized("o.market ~ '^over_under_[0-9]+$'",
                                 ("over", "under"), "o/u"):
            raw.append((IDEALIZED_LABEL, r))
    for label, d in raw:
        fam, line, side = parse_market(d["market"], d["selection"])
        d["family"], d["line"], d["side"] = fam, line, side
        d["bet_type"] = fam if line == "-" else f"{fam} {line}"
        d["sel_band"] = _sel_band(fam, d["selection"], d["odds"], side)
        d["odds_band"] = _bucket(d["odds"], [1.8, 2.2, 2.8, 3.2, 4.0],
                                 ["<1.8", "1.8-2.2", "2.2-2.8", "2.8-3.2",
                                  "3.2-4.0", "4.0+"])
        d["edge_band"] = _bucket(d["ep"], [0.05, 0.08, 0.10, 0.13, 0.18],
                                 ["<5%", "5-8%", "8-10%", "10-13%",
                                  "13-18%", "18%+"])
        # EDGE-UNIT-GUARD (2026-09-11). `edge_percent` is a decimal FRACTION
        # by convention (0.10 = 10%, gotcha 48) — but six bots violate it,
        # storing values up to 30.7 (corners) and 67.9 (team totals):
        #   bot_corners_paper_shadow_v1, bot_team_total_paper_shadow_v1,
        #   bot_1h_1x2_paper_shadow_v1, bot_no_pin_shadow_v1,
        #   bot_no_pin_home_v1, bot_sweep_1x2_home_v1
        # Three of those are 1x2 bots, so pooling them silently CORRUPTS the
        # edge axis: every one of their picks clears even a 20% floor, which
        # inflates exactly the high-floor cells a sweep is most tempted to
        # adopt. Flagged as a dimension AND excluded by default.
        d["edge_scale"] = "fraction" if -1.0 <= d["ep"] <= 1.0 else "SUSPECT"
        d["edge_kind"] = _edge_kind(d["bot"], d.get("model_version"))

        d["month"] = d["pick_time"].strftime("%Y-%m") if d.get("pick_time") else "?"
        ko = d.get("kickoff")
        d["dow"] = ko.strftime("%a") if ko else "?"
        d["ko_hour"] = (_bucket(ko.hour, [6, 12, 18],
                                ["00-05", "06-11", "12-17", "18-23"])
                        if ko else "?")
        d["league_tier"] = f"t{d['league_tier']}" if d.get("league_tier") else "t?"
        d["retired"] = "y" if d.get("retired") else "n"
        for k in ("bookmaker", "closing_book", "model_version",
                  "timing_cohort", "strategy", "league", "league_country"):
            d[k] = d.get(k) or "?"
        out.append(d)
    return out


# ── grid maths ───────────────────────────────────────────────────────────────
def fold_bounds(rows: list[dict], n: int) -> list:
    xs = sorted(r["pick_time"] for r in rows)
    if not xs:
        return []
    step = len(xs) / n
    return [xs[min(len(xs) - 1, int(step * (i + 1)))] for i in range(n - 1)]


def _fold_of(when, bounds: list) -> int:
    for i, b in enumerate(bounds):
        if when < b:
            return i
    return len(bounds)


def cell(rows, bounds, ef, of, n_folds, field) -> dict:
    kept = [r for r in rows if r["ep"] >= ef and r["odds"] >= of]
    vals = [r[field] for r in kept if r.get(field) is not None]
    if not vals:
        return {"n": 0, "val": None, "robust": False, "folds": []}
    buckets: list[list[float]] = [[] for _ in range(n_folds)]
    for r in kept:
        if r.get(field) is not None:
            buckets[_fold_of(r["pick_time"], bounds)].append(r[field])
    frois = [100 * st.mean(b) if b else None for b in buckets]
    return {"n": len(vals), "val": 100 * st.mean(vals),
            "robust": all(f is not None and f > 0 for f in frois),
            "folds": frois}


def _fmt(c, min_n) -> str:
    if c["n"] == 0 or c["val"] is None:
        return f"{'—':>13}"
    return (f"{c['val']:>+7.1f}{'✓' if c['robust'] else ' '}"
            f"{'' if c['n'] >= min_n else '?':<1}{c['n']:>4}")


# ── filtering ────────────────────────────────────────────────────────────────
def parse_filters(specs):
    fs = []
    for s in specs:
        for op in ("!=", ">=", "<=", "="):
            if op in s:
                k, v = s.split(op, 1)
                fs.append((k.strip(), op, v.strip()))
                break
        else:
            raise SystemExit(f"bad --filter {s!r}: use dim=val / dim!=val / dim>=n")
    return fs


def apply_filters(rows, fs):
    for k, op, v in fs:
        if k not in DIMENSIONS and k not in ("odds", "ep"):
            raise SystemExit(f"unknown filter dimension {k!r} — try --list-dims")
        if op == "=":
            rows = [r for r in rows if str(r.get(k)) == v]
        elif op == "!=":
            rows = [r for r in rows if str(r.get(k)) != v]
        elif op == ">=":
            rows = [r for r in rows if float(r.get(k) or 0) >= float(v)]
        else:
            rows = [r for r in rows if float(r.get(k) or 0) <= float(v)]
    return rows


# ── reporting ────────────────────────────────────────────────────────────────
def report(rows, group_by, edge_floors, odds_floors, n_folds, min_n, metric,
           summary_only, top, scope_by) -> int:
    field, mlabel = METRICS[metric]
    summary = []
    keys = sorted({tuple(str(r.get(s)) for s in scope_by) for r in rows})
    for sk in keys:
        scope = [r for r in rows
                 if tuple(str(r.get(s)) for s in scope_by) == sk]
        bounds = fold_bounds(scope, n_folds)
        groups: dict = {}
        for r in scope:
            groups.setdefault(tuple(str(r.get(g)) for g in group_by), []).append(r)
        if not summary_only:
            print(f"\n{'='*118}")
            print(f"SCOPE {' · '.join(sk)}   ·   n={len(scope)} settled pre-match"
                  f"   ·   {n_folds} walk-forward folds   ·   metric={mlabel}")
            print(f"group-by: {','.join(group_by)}   ·   cell = {mlabel} "
                  f"[✓=positive in EVERY fold] [?=n<{min_n}, not adoptable] n")
            print('='*118)
        for gk, grp in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            cells = {(ef, of): cell(grp, bounds, ef, of, n_folds, field)
                     for ef in edge_floors for of in odds_floors}
            adopt = [((ef, of), c) for (ef, of), c in cells.items()
                     if c["robust"] and c["n"] >= min_n]
            if adopt:
                (bef, bof), bc = max(adopt, key=lambda kv: kv[1]["val"])
                summary.append((" · ".join(sk), " / ".join(gk), bef, bof,
                                bc["val"], bc["n"], len(adopt)))
            else:
                summary.append((" · ".join(sk), " / ".join(gk), None, None,
                                None, len(grp), 0))
            if summary_only:
                continue
            print(f"\n  {' / '.join(gk)}   (n={len(grp)})")
            print("    edge\\odds " + "".join(f"{o:>13.2f}" for o in odds_floors))
            for ef in edge_floors:
                print(f"    >={ef*100:>4.0f}%   "
                      + "".join(_fmt(cells[(ef, of)], min_n) for of in odds_floors))

    print(f"\n\n{'='*118}")
    print(f"SUMMARY — best ADOPTABLE cell  [positive in all {n_folds} folds AND "
          f"n >= {min_n}]   metric={mlabel}")
    print('='*118)
    print(f"{'scope':38}{'group':26}{'edge':>7}{'odds':>7}{mlabel:>10}"
          f"{'n':>6}{'#robust':>9}")
    shown = sorted(summary, key=lambda s: (s[4] is None, -(s[4] or 0)))
    for sk, g, ef, of, val, n, cnt in (shown[:top] if top else shown):
        if ef is None:
            print(f"{sk[:37]:38}{g[:25]:26}{'—':>7}{'—':>7}{'none':>10}{n:>6}{0:>9}")
        else:
            print(f"{sk[:37]:38}{g[:25]:26}{ef*100:>6.0f}%{of:>7.2f}"
                  f"{val:>+10.1f}{n:>6}{cnt:>9}")
    print(f"\n#robust = how many of the {len(edge_floors)*len(odds_floors)} "
          f"(edge x odds) cells were robust AND above the min-n gate. Many "
          f"robust cells = a broad, stable frame. One or two = fragile, and "
          f"probably selection noise rather than an edge.")
    return 0


def dump(rows, path) -> int:
    cols = ["dataset", "market", "family", "line", "side", "bet_type",
            "selection", "sel_band", "ep", "odds", "odds_band", "edge_band",
            "edge_scale", "edge_kind",
            "result", "ret", "clv", "clv_pinnacle", "clv_live", "bookmaker",
            "closing_book", "bot", "maturity", "retired", "model_version",
            "timing_cohort", "strategy", "league", "league_country",
            "league_tier", "month", "dow", "ko_hour", "pick_time", "kickoff"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} settled pre-match picks x {len(cols)} columns -> {path}")
    print("Every dimension in --list-dims is a column, so any combination can be "
          "asked offline without re-deriving the method.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list-dims", action="store_true")
    ap.add_argument("--group-by", default="sel_band")
    ap.add_argument("--scope-by", default="dataset,market",
                    help="dimensions that define a fold timeline and a report "
                         "block (default dataset,market). Fold bounds are "
                         "computed per scope, so keep a time-homogeneous unit "
                         "here.")
    ap.add_argument("--filter", action="append", default=[])
    ap.add_argument("--market", action="append", default=[])
    ap.add_argument("--family", action="append", default=[])
    ap.add_argument("--dataset", action="append", default=[])
    ap.add_argument("--edge-floors", help="comma list in PERCENT, e.g. 0,10,13")
    ap.add_argument("--odds-floors", help="comma list, e.g. 2.8,3.2,3.6")
    ap.add_argument("--metric", default="roi", choices=sorted(METRICS))
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=50)
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--include-suspect-edge", action="store_true",
                    help="keep rows whose edge_percent breaks the 0..1 fraction "
                         "convention. OFF by default: those bots store a "
                         "different unit (up to 67.9), so every pick of theirs "
                         "clears any floor and the edge axis becomes "
                         "meaningless. Only use this to STUDY the offenders.")
    ap.add_argument("--no-idealized", action="store_true",
                    help="skip the fixture-level basis (it is the only dataset "
                         "at real scale, so only skip it when you specifically "
                         "want the pick-level ledgers)")
    ap.add_argument("--dump")
    a = ap.parse_args()

    if a.list_dims:
        print("Dimensions (group-by / scope-by / filter):\n")
        for k, v in DIMENSIONS.items():
            print(f"  {k:16} {v}")
        print("\nMetrics:\n")
        for k, (_, lab) in sorted(METRICS.items()):
            print(f"  {k:16} {lab}"
                  + ("   <- the only basis for adopting a floor" if k == "roi"
                     else "   (diagnosis only; converges faster than ROI)"))
        print("\nDatasets:\n")
        for lab, tbl, mat in DATASETS:
            print(f"  {lab:21} {tbl} · maturity={mat}")
        return 0

    rows = load(a.dataset or None, include_idealized=not a.no_idealized)
    if a.market:
        rows = [r for r in rows if r["market"] in a.market]
    if a.family:
        rows = [r for r in rows if r["family"] in a.family]
    rows = apply_filters(rows, parse_filters(a.filter))
    if not a.include_suspect_edge:
        bad = [r for r in rows if r["edge_scale"] == "SUSPECT"]
        if bad:
            from collections import Counter
            who = Counter(r["bot"] for r in bad).most_common()
            print(f"EDGE-UNIT-GUARD: dropped {len(bad)} of {len(rows)} rows whose "
                  f"edge_percent breaks the 0..1 fraction convention "
                  f"(gotcha 48). They would clear EVERY floor and inflate the "
                  f"high-floor cells. Offenders: "
                  + ", ".join(f"{b}({n})" for b, n in who))
            print("  Pass --include-suspect-edge to study them deliberately.\n")
        rows = [r for r in rows if r["edge_scale"] == "fraction"]
    if not rows:
        print("no rows after filters")
        return 1
    if a.dump:
        return dump(rows, a.dump)

    ef = ([float(x) / 100 for x in a.edge_floors.split(",")]
          if a.edge_floors else DEFAULT_EDGE_FLOORS)
    of = ([float(x) for x in a.odds_floors.split(",")]
          if a.odds_floors else DEFAULT_ODDS_FLOORS)
    gb = [g.strip() for g in a.group_by.split(",") if g.strip()]
    sb = [s.strip() for s in a.scope_by.split(",") if s.strip()]
    for d in gb + sb:
        if d not in DIMENSIONS:
            raise SystemExit(f"unknown dimension {d!r} — try --list-dims")
    return report(rows, gb, ef, of, a.folds, a.min_n, a.metric,
                  a.summary_only, a.top, sb)


if __name__ == "__main__":
    raise SystemExit(main())
