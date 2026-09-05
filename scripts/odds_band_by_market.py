"""ODDS-BAND-BY-MARKET-AUDIT-2026-09-05 — is the profitable odds range the same
for every market, or does each market have its own?

Background. REALMONEY-ODDS-BAND-MISMATCH found that our Coolbet real-money bets
were profitable only above ~2.80 and shipped `COOLBET_MIN_ODDS=2.80` as a single
fleet-wide floor. The owner's question is whether that floor is the right SHAPE:
"probably every market has their own odds range(s) where they are most
profitable", and — the part that matters more — a market with no positive band
today might have one in a price range we are not currently betting, so this
should be able to OPEN markets, not only close them.

What this script does differently from the first cut recorded in
PRIORITY_QUEUE.md:

  * Both ledgers. The first cut used `simulated_bets` from non-retired bots only
    (n=608). `shadow_bets_unique` carries an order of magnitude more settled
    prematch picks and is where the line-shop bots live, which is exactly the
    per-bot contrast the ticket asks for.
  * CLV is RECOMPUTED here rather than read from `clv_pinnacle_live`, for one
    reason: the stored column is NULL for every asian_handicap row, because
    `settlement._market_complement_selections()` deliberately refuses AH (the
    handicap line is not threaded through it). AH is the third-largest market in
    the fleet by settled volume and has never had a validator. Pairing Pinnacle's
    home/away quotes at the SAME `handicap_line` is a clean 2-way complement, so
    it can be de-vigged like any other 2-way market. See `_ah_note()` for the one
    caveat that comes with it.
  * The recomputation is checked against the stored column on the rows where both
    exist (`--verify`), so the new AH numbers sit on a basis that has been shown
    to reproduce the audited one.

Correctness rules this script obeys, each of which has cost this repo real time:

  * `odds_snapshots` is APPEND-ONLY, so MAX(odds) is a high-water mark, not an
    offer (gotcha 30). Every price here comes from `DISTINCT ON (...) ORDER BY
    timestamp DESC`.
  * `is_live = false` is NOT a pre-kickoff filter — it only excludes the
    `api-football-live` pseudo-book, and 26% of `is_live = false` rows are past
    kickoff (gotcha 37). Every odds query is bounded by `o.timestamp <= m.date`.
  * Market prefixes are treacherous: `split_part(market,'_',1)='corners'` also
    matches `corners_home_ou`, `corners_away_ou` and `corners_1h_ou`. Market
    shapes are matched exactly, never by prefix.
  * Odds outliers dominate any unguarded slice search (gotcha 9), so the
    production guard (taken price <= Pinnacle close x 1.30 OU / x 1.35 1X2) is
    applied and the drops are reported.
  * Judged on CLV, never ROI (gotcha 8). ROI is printed only with its standard
    error attached and a note that it is underpowered.
  * ENSEMBLE-RECALIBRATION shipped 2026-09-03, inside the sample window
    (gotcha 39), so every headline is also split on that date.

Nothing here writes to the database and nothing here changes a production gate.

WHAT THE FIRST RUN FOUND (2026-09-06, n=12,342 usable of 17,559 settled
prematch singles, 4,823 matches). Re-run before quoting these — they move.

1. THE ODDS BAND IS NOT A MARKET PROPERTY. IT IS A BOT PROPERTY.
   Pooled over model-driven bots, 1x2 CLV is negative in every band and gets
   monotonically WORSE with price: <2.0 -0.32pct, 2.0-2.8 -2.71pct, 2.8-3.5
   -3.01pct, 3.5-5.0 -3.44pct, 5.0+ -5.47pct. Over/under is negative in every
   band too. Split by bot, the same 1x2 3.5-5.0 cell reads +6.42pct for
   `bot_v10_all` (n=89, t_clu +4.69) and -6.50pct for `bot_aggressive`
   (n=379, t_clu -9.53). A price floor cannot express that; only a per-bot rule
   can.

2. THE FIRST CUT'S HEADLINE DOES NOT GENERALISE, BUT IT IS NOT NOISE EITHER.
   `bot_v10_all` 1x2 at 3.5-5.0 clears its own-grid placebo at p=0.0020 against
   a null p95 of 2.43, is positive in all five months (May +0.2pct rising to
   Aug +9.2pct), appears in both ledgers independently (sim +6.00pct n=50,
   shadow +6.95pct n=39), and is corroborated by a second, independently
   configured bot in the identical cell (`bot_high_alignment` +5.51pct, n=34,
   p=0.0040). Every one of the 89 rows is a HOME selection, so the finding is
   "v10 backing home underdogs at 3.5-5.0", not "long prices are good".

3. THE POSITIVE O/U BANDS BELONG TO BOTS WHOSE ENTRY RULE IS THE METRIC.
   Over/under 3.5-5.0 shows +7.08pct across all bots — and the cell is 85pct
   line-shop rows. Restricted to model-driven bots, over/under has NO positive
   band at any price, which is the first cut's O/U conclusion surviving intact.

4. NOTHING NEW OPENS ON THE EXISTING SURFACE. Scanning for positive cells we do
   not currently bet: there are none among model-driven bots in any market at
   any band. Asian handicap (priced on the high-water basis, see below) is
   negative in all four bands. Corners is the only genuinely new surface and it
   cannot be tested yet — no bet has ever been placed on it.

5. TWO DEFECTS FOUND IN PASSING, both reported by `--verify`:
   * `simulated_bets.clv_pinnacle_live` is a DIFFERENT QUANTITY from
     `shadow_bets.clv_pinnacle_live` — vigged rather than de-vigged, and
     +8.40pp higher than an honest recompute on the same 1,642 rows.
   * `odds_at_pick_live` has never been backfilled for asian_handicap: 0 of
     2,013 settled AH rows carry an executable price, so AH cannot be judged on
     the basis everything else is judged on.

6. Good news for the corners work: Pinnacle DOES quote corners (58,676 rows
   across 50 corner markets), so corner bets will have a de-vigged CLV anchor
   the day they exist. Gotcha 4's "Pinnacle quotes only 8 bet types" is about
   the standard set and should not be read as ruling corners out.

Usage:
    python3 scripts/odds_band_by_market.py
    python3 scripts/odds_band_by_market.py --min-n 25 --perms 1000
    python3 scripts/odds_band_by_market.py --verify
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

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig, proportional_devig  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _normalize_bet_market,
    _normalize_bet_selection,
)

# ─── configuration ───────────────────────────────────────────────────────────

# Band edges. 3.5+ is split at 5.0 deliberately: the first cut stopped at "3.5+"
# and that is the cell carrying the whole positive result, so it has to be shown
# it is not one tail of very long prices doing the work.
BANDS = [(0.0, 2.0), (2.0, 2.8), (2.8, 3.5), (3.5, 5.0), (5.0, 1e9)]
BAND_LABELS = ["<2.0", "2.0-2.8", "2.8-3.5", "3.5-5.0", "5.0+"]

# ENSEMBLE-RECALIBRATION (gotcha 39). Everything before this date is a
# pre-recalibration measurement wearing a current-date label.
RECAL_DATE = "2026-09-03"

# Production outlier guard (gotcha 9), keyed by market family.
OUTLIER_CAP = {"1x2": 1.35, "ou": 1.30, "ah": 1.30, "dc": 1.35}

MARKETS_3WAY = {"1x2"}

# The bots whose ENTRY CRITERION is de-vigged-Pinnacle edge. This distinction is
# the single most important thing in this file.
#
# `bot_pin_1x2_home_v1`, `bot_pin_1x2_draw_tier4_v1`, `bot_sweep_ou25_v1`,
# `bot_sweep_ou35_v1` and `bot_coolbet_value_v1` all fire when a soft book's
# price beats the de-vigged Pinnacle probability by `_LINESHOP_TRUE_EDGE_MIN`
# (3%). Their CLV against de-vigged Pinnacle is therefore their own admission
# test, re-measured. It is positive by construction in EVERY band, and reading
# it as "this band is profitable" is circular. What is informative for them is
# the DRIFT — how much of the entry edge survives to the close — which is why
# the mean entry edge is printed alongside.
#
# `bot_sweep_1x2_home_v1` / `_draw_v1` are NOT in this set: they gate on MODEL
# edge and merely require a Pinnacle quote to exist.
LINESHOP_BOTS = {
    "bot_pin_1x2_home_v1", "bot_pin_1x2_draw_tier4_v1",
    "bot_sweep_ou25_v1", "bot_sweep_ou35_v1", "bot_coolbet_value_v1",
}


def band_of(price: float) -> str | None:
    for (lo, hi), lab in zip(BANDS, BAND_LABELS):
        if lo <= price < hi:
            return lab
    return None


# ─── statistics ──────────────────────────────────────────────────────────────

def summarise(values: list[float]) -> tuple[int, float, float, float]:
    """n, mean, iid standard error, t."""
    n = len(values)
    if n < 2:
        return n, (values[0] if values else 0.0), 0.0, 0.0
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return n, m, se, (m / se if se else 0.0)


def cluster_se(values: list[float], clusters: list[str]) -> float:
    """Cluster-robust standard error of the mean, clustered on match_id.

    Several bots bet the same fixture, so rows are not independent. With G
    clusters the usual sandwich for a sample mean is

        Var(xbar) = (1/n^2) * sum_g ( sum_{i in g} (x_i - xbar) )^2

    scaled by the small-G correction G/(G-1). Where each match contributes one
    row this collapses to the iid formula, which is why both are printed: the gap
    between them IS the clustering.
    """
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    per_cluster: dict[str, float] = defaultdict(float)
    for v, c in zip(values, clusters):
        per_cluster[c] += v - m
    g = len(per_cluster)
    if g < 2:
        return 0.0
    acc = sum(s * s for s in per_cluster.values())
    return math.sqrt(acc * (g / (g - 1.0))) / n


# ─── loading ─────────────────────────────────────────────────────────────────

SIM_SQL = """
SELECT 'sim' AS src, b.name AS bot, (b.retired_at IS NULL) AS bot_live,
       s.market AS market_raw, s.selection AS selection_raw,
       s.odds_at_pick, s.odds_at_pick_live, s.clv_pinnacle_live,
       s.edge_percent, s.pick_time, s.match_id::text AS match_id,
       s.result::text AS result,
       s.pnl, s.stake, l.tier AS tier, l.name AS league, m.date AS kickoff
  FROM simulated_bets s
  JOIN bots b     ON b.id = s.bot_id
  JOIN matches m  ON m.id = s.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
 WHERE s.result IN ('won','lost')
   AND s.match_minute_at_pick IS NULL   -- prematch only; CLV is meaningless
   AND b.name NOT LIKE 'inplay%%'       -- in-play (gotcha 14)
   AND s.combo_legs IS NULL             -- singles only
   AND s.market <> 'combo'
"""

SHADOW_SQL = """
SELECT 'shadow' AS src, b.name AS bot, (b.retired_at IS NULL) AS bot_live,
       s.market AS market_raw, s.selection AS selection_raw,
       s.odds_at_pick, s.odds_at_pick_live, s.clv_pinnacle_live,
       s.edge_percent, s.pick_time, s.match_id::text AS match_id,
       s.result::text AS result,
       s.pnl, s.stake, l.tier AS tier, l.name AS league, m.date AS kickoff
  FROM shadow_bets_unique s
  JOIN bots b     ON b.id = s.bot_id
  JOIN matches m  ON m.id = s.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
 WHERE s.result IN ('won','lost')
   AND b.name NOT LIKE 'inplay%%'
   AND s.market <> 'combo'
"""

AH_SEL_RE = re.compile(r"^\s*(home|away)\s*([+-]?\d+(?:\.\d+)?)\s*$")


def classify(row: dict) -> tuple[str, str, str, float | None] | None:
    """(family, snapshot_market, snapshot_selection, home_handicap_line).

    `family` is the pooled market label the cross-tab is built on. Pooling every
    spelling matters — comparing one spelling against a pooled figure silently
    compares different bot mixes (gotcha 33).
    """
    raw = (row["market_raw"] or "").strip().lower()
    sel_raw = (row["selection_raw"] or "").strip()

    if raw == "asian_handicap":
        mo = AH_SEL_RE.match(sel_raw.lower())
        if not mo:
            return None
        side, line = mo.group(1), float(mo.group(2))
        # `odds_snapshots.handicap_line` is written from the HOME perspective on
        # both rows of the pair, so an away selection has to be flipped to find
        # its counterpart.
        home_line = line if side == "home" else -line
        return "asian_handicap", "asian_handicap", side, home_line

    mkt = _normalize_bet_market(raw, sel_raw)
    sel = _normalize_bet_selection(sel_raw)
    if mkt == "1x2" and sel in ("home", "draw", "away"):
        return "1x2", "1x2", sel, None
    if mkt.startswith("over_under") and sel in ("over", "under"):
        # Pool every OU line into one family for the headline, but keep the
        # snapshot market exact so the Pinnacle anchor is the RIGHT line
        # (CLV-OU-LINE-FIX): an OU 3.5 bet priced against the 2.5 close produced
        # bogus +59-76% CLV once already.
        return "over_under", mkt, sel, None
    if mkt == "double_chance" and sel in ("1x", "12", "x2"):
        return "double_chance", "double_chance", sel, None
    if mkt == "btts" and sel in ("yes", "no"):
        return "btts", "btts", sel, None
    return None


def load_rows() -> list[dict]:
    rows = execute_query(SIM_SQL, []) + execute_query(SHADOW_SQL, [])
    out = []
    for r in rows:
        c = classify(r)
        if not c:
            continue
        fam, mkt, sel, line = c
        r["family"], r["snap_market"], r["snap_selection"] = fam, mkt, sel
        r["hline"] = line
        out.append(r)
    return out


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def load_pinnacle_close(match_ids: list[str], markets: list[str]) -> dict:
    """Pinnacle's closing quote per (match, market, selection).

    Reproduces production `settlement.get_pinnacle_closing_odds()` exactly:
    prefer the row flagged `is_closing`, fall back to the latest pre-kickoff
    quote. Matching the production definition is not cosmetic — an earlier draft
    of this script used the fallback unconditionally and disagreed with the
    audited column on 76pct of over/under rows, by a median 0.2pp but by up to
    13pp on individual rows.

    DISTINCT ON ... ORDER BY timestamp DESC, never MAX(odds) (gotcha 30), and
    bounded by `o.timestamp <= m.date` because `is_live = false` does not mean
    pre-kickoff (gotcha 37).
    """
    sql = """
    SELECT DISTINCT ON (o.match_id, o.market, o.selection)
           o.match_id::text AS match_id, o.market, o.selection, o.odds
      FROM odds_snapshots o
      JOIN matches m ON m.id = o.match_id
     WHERE o.match_id = ANY(%s::uuid[])
       AND o.bookmaker = 'Pinnacle'
       AND o.market = ANY(%s)
       AND o.timestamp <= m.date
       {closing}
     ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
    """
    close: dict[tuple, float] = {}
    for clause in ("", "AND o.is_closing = TRUE"):   # fallback first, then override
        for batch in _chunks(match_ids, 400):
            for r in execute_query(sql.format(closing=clause), [batch, markets]):
                close[(r["match_id"], r["market"], r["selection"])] = float(r["odds"])
    return close


def load_pinnacle_ah_close(match_ids: list[str]) -> dict:
    """Same, keyed additionally by `handicap_line` so the pair is the same line."""
    sql = """
    SELECT DISTINCT ON (o.match_id, o.handicap_line, o.selection)
           o.match_id::text AS match_id, o.handicap_line, o.selection, o.odds
      FROM odds_snapshots o
      JOIN matches m ON m.id = o.match_id
     WHERE o.match_id = ANY(%s::uuid[])
       AND o.bookmaker = 'Pinnacle'
       AND o.market = 'asian_handicap'
       AND o.handicap_line IS NOT NULL
       AND o.timestamp <= m.date
       {closing}
     ORDER BY o.match_id, o.handicap_line, o.selection, o.timestamp DESC
    """
    close: dict[tuple, float] = {}
    for clause in ("", "AND o.is_closing = TRUE"):
        for batch in _chunks(match_ids, 400):
            for r in execute_query(sql.format(closing=clause), [batch]):
                close[(r["match_id"], float(r["handicap_line"]), r["selection"])] = \
                    float(r["odds"])
    return close


def _ah_note() -> str:
    return (
        "AH caveat: on a full line (-1.0) part of the stake pushes, and on a\n"
        "  quarter line (-1.25) half of it does. Pinnacle's two quotes are priced\n"
        "  CONDITIONAL on no push, so the 2-way de-vig returns P(cover | no push)\n"
        "  and `odds * p - 1` is EV per NON-PUSH bet. Per-bet EV is that number\n"
        "  times (1 - push probability), so the AH magnitudes below are an upper\n"
        "  bound in absolute size. The SIGN and the ordering across bands are\n"
        "  unaffected, which is what the band question needs."
    )


def attach_true_prob(rows: list[dict]) -> dict[str, int]:
    """Attach Pinnacle-fair probability and the recomputed executable CLV."""
    match_ids = sorted({r["match_id"] for r in rows})
    snap_markets = sorted({r["snap_market"] for r in rows if r["family"] != "asian_handicap"})
    # double_chance is derived from the 1x2 close, so 1x2 must be fetched too.
    want = set(snap_markets) | {"1x2"}
    want.discard("double_chance")
    close = load_pinnacle_close(match_ids, sorted(want))
    ah_close = load_pinnacle_ah_close(match_ids)

    devig_cache: dict[tuple, dict[str, float] | None] = {}
    stats = defaultdict(int)

    def probs_for(mid: str, mkt: str) -> dict[str, float] | None:
        key = (mid, mkt)
        if key in devig_cache:
            return devig_cache[key]
        if mkt == "1x2":
            sides = ["home", "draw", "away"]
        elif mkt.startswith("over_under"):
            sides = ["over", "under"]
        elif mkt == "btts":
            sides = ["yes", "no"]
        else:
            devig_cache[key] = None
            return None
        odds = [close.get((mid, mkt, s)) for s in sides]
        if any(o is None or o <= 1.0 for o in odds):
            devig_cache[key] = None
            return None
        # Shin for 3-way (margin loads onto longshots), proportional for 2-way.
        p = devig(odds) if mkt in MARKETS_3WAY else proportional_devig(odds)
        devig_cache[key] = dict(zip(sides, p)) if p else None
        return devig_cache[key]

    for r in rows:
        fam = r["family"]
        p = None
        pin_price = None
        if fam == "asian_handicap":
            line = r["hline"]
            oh = ah_close.get((r["match_id"], line, "home"))
            oa = ah_close.get((r["match_id"], line, "away"))
            if oh and oa and oh > 1.0 and oa > 1.0:
                pp = proportional_devig([oh, oa])
                if pp:
                    p = pp[0] if r["snap_selection"] == "home" else pp[1]
                    pin_price = oh if r["snap_selection"] == "home" else oa
        elif fam == "double_chance":
            base = probs_for(r["match_id"], "1x2")
            if base:
                legs = {"1x": ("home", "draw"), "12": ("home", "away"),
                        "x2": ("draw", "away")}[r["snap_selection"]]
                p = sum(base[l] for l in legs)
                pin_price = 1.0 / p if p > 0 else None
        else:
            table = probs_for(r["match_id"], r["snap_market"])
            if table:
                p = table.get(r["snap_selection"])
                pin_price = close.get((r["match_id"], r["snap_market"], r["snap_selection"]))

        r["true_p"] = p if (p and 0.0 < p < 1.0) else None
        r["pin_price"] = pin_price
        if r["true_p"] is None:
            stats["no_pinnacle_anchor"] += 1
            continue

        cap = OUTLIER_CAP[{"1x2": "1x2", "over_under": "ou", "asian_handicap": "ah",
                           "double_chance": "dc", "btts": "ou"}[fam]]

        # Two price bases, kept strictly apart.
        #
        # `odds_at_pick_live` is the quote that was actually on offer at pick
        # time and is the ONLY basis anything actionable is read off.
        # `odds_at_pick` is a MAX() high-water mark over the fixture's whole
        # snapshot history (gotcha 30) and overstates 1x2 ROI by +5.80pp. It is
        # computed here for one reason: the executable column has never been
        # backfilled for asian_handicap (0 of 2,013 settled AH rows carry it),
        # so AH would otherwise be invisible in an audit whose ticket names it.
        # Everything derived from it is labelled INFLATED and is not a basis
        # for changing a gate.
        for basis, col, clv_key, band_key_ in (
            ("live", "odds_at_pick_live", "clv", "band"),
            ("highwater", "odds_at_pick", "clv_hw", "band_hw"),
        ):
            raw = r.get(col)
            if not raw:
                if basis == "live":
                    stats["no_executable_price"] += 1
                continue
            px = float(raw)
            if pin_price and px > pin_price * cap:
                stats[f"outlier_dropped_{basis}"] += 1
                continue
            b = band_of(px)
            if b is None:
                stats["no_band"] += 1
                continue
            r[clv_key] = px * r["true_p"] - 1.0
            r[band_key_] = b
            if basis == "live":
                r["px"] = px
                r["unit_return"] = (px - 1.0) if r["result"] == "won" else -1.0
                stats["kept"] += 1
            else:
                r["unit_return_hw"] = (px - 1.0) if r["result"] == "won" else -1.0
    return stats


# ─── reporting ───────────────────────────────────────────────────────────────

def cells(rows, key_fn, min_n, clv_key="clv", ret_key="unit_return"):
    grouped = defaultdict(list)
    for r in rows:
        if r.get(clv_key) is None:
            continue
        grouped[key_fn(r)].append(r)
    out = {}
    for k, rs in grouped.items():
        if len(rs) < min_n:
            continue
        clv = [r[clv_key] for r in rs]
        mids = [r["match_id"] for r in rs]
        n, m, se, t = summarise(clv)
        cse = cluster_se(clv, mids)
        ret = [r[ret_key] for r in rs if r.get(ret_key) is not None]
        _, rm, rse, _ = summarise(ret) if ret else (0, 0.0, 0.0, 0.0)
        edges = [float(r["edge_percent"]) for r in rs if r.get("edge_percent") is not None]
        out[k] = {
            "n": n, "clv": m, "se": se, "t": t,
            "cse": cse, "ct": (m / cse if cse else 0.0),
            "roi": rm, "roi_se": rse, "matches": len(set(mids)),
            "edge": (sum(edges) / len(edges) if edges else None),
            "lineshop": sum(1 for r in rs if r["bot"] in LINESHOP_BOTS) / len(rs),
        }
    return out


def print_table(title, table, keyfmt=lambda k: " / ".join(str(x) for x in k)):
    print(f"\n{title}")
    if not table:
        print("  (no cell reached the minimum n)")
        return
    print(f"  {'cell':<44} {'n':>5} {'mch':>5} {'CLV':>8} {'t':>7} "
          f"{'t(clu)':>7} {'entry':>7} {'ROI':>8} {'+/-':>7} {'LS':>5}")
    for k in sorted(table, key=lambda k: keyfmt(k)):
        c = table[k]
        edge = f"{c['edge']*100:>6.1f}%" if c["edge"] is not None else "     -"
        print(f"  {keyfmt(k):<44} {c['n']:>5} {c['matches']:>5} "
              f"{c['clv']*100:>7.2f}% {c['t']:>7.2f} {c['ct']:>7.2f} {edge} "
              f"{c['roi']*100:>7.2f}% {c['roi_se']*100:>6.1f}pp "
              f"{c['lineshop']*100:>4.0f}%")


def band_key(k):
    fam, band = k
    return f"{fam:<18} {band}"


# ─── placebo ─────────────────────────────────────────────────────────────────

def _demean_by_family(rows, clv_key="clv"):
    """Centre CLV within each market family.

    The band question is "does the PRICE carry information inside a market",
    which is not the same as "is this market negative overall". Without
    centring, a market with a large uniform level (double_chance sits at
    -5.8pct across every band) hands every one of its cells a huge |t| that has
    nothing to do with price, and the placebo null inherits the same level, so
    both real and shuffled statistics land in the 70s and the test says
    nothing. Centring makes both sides measure the band deviation only.
    """
    by_fam = defaultdict(list)
    for r in rows:
        if r.get(clv_key) is not None:
            by_fam[r["family"]].append(r)
    out = {}
    for fam, rs in by_fam.items():
        vals = [r[clv_key] for r in rs]
        mu = sum(vals) / len(vals)
        out[fam] = (rs, [v - mu for v in vals], mu)
    return out


def placebo_max_t(rows, min_n, perms, clv_key="clv", seed=20260905):
    """Family-wise placebo for the whole market x band search.

    The headline is not one pre-registered test — it is the best cell found in a
    grid, so the honest null is the distribution of the BEST cell under no
    association. Within each market family the (centred) CLV values are shuffled
    across rows, which destroys any link between price and CLV while leaving
    every sample size, every band boundary and the n>=min_n rule exactly as they
    are. The statistic recorded per permutation is max |t| over the surviving
    cells; the real grid's best cell is read against that distribution.

    A finding that does not clear this is not a finding.
    """
    rng = random.Random(seed)
    fam_data = _demean_by_family(rows, clv_key)

    def grid_max(assign):
        best, best_cell = 0.0, None
        for fam, (rs, _, mu) in fam_data.items():
            buckets = defaultdict(list)
            for r, v in zip(rs, assign[fam]):
                buckets[r["band" if clv_key == "clv" else "band_hw"]].append(v)
            for band, vals in buckets.items():
                if len(vals) < min_n:
                    continue
                _, m, se, t = summarise(vals)
                if abs(t) > best:
                    best, best_cell = abs(t), (fam, band, m + mu, m, t, len(vals))
        return best, best_cell

    real, real_cell = grid_max({f: list(v[1]) for f, v in fam_data.items()})
    null = []
    for _ in range(perms):
        shuffled = {}
        for f, (_, vals, _mu) in fam_data.items():
            v = list(vals)
            rng.shuffle(v)
            shuffled[f] = v
        null.append(grid_max(shuffled)[0])
    null.sort()
    above = sum(1 for x in null if x >= real)
    return real, real_cell, null, (above + 1) / (perms + 1)


def placebo_cell(rows, fam, band, perms, clv_key="clv", seed=20260906):
    """Single-cell placebo, on the same centred statistic: how often does THIS
    band produce a deviation this extreme when the labels are shuffled inside
    the market?"""
    rng = random.Random(seed)
    band_field = "band" if clv_key == "clv" else "band_hw"
    rs = [r for r in rows if r["family"] == fam and r.get(clv_key) is not None]
    if not rs:
        return None
    vals = [r[clv_key] for r in rs]
    mu = sum(vals) / len(vals)
    dev = [v - mu for v in vals]
    idx = [i for i, r in enumerate(rs) if r.get(band_field) == band]
    if len(idx) < 2:
        return None
    real = sum(dev[i] for i in idx) / len(idx)
    hits = 0
    pool = list(dev)
    for _ in range(perms):
        rng.shuffle(pool)
        if abs(sum(pool[i] for i in idx) / len(idx)) >= abs(real):
            hits += 1
    return mu + real, real, (hits + 1) / (perms + 1), len(idx)


# ─── verification ────────────────────────────────────────────────────────────

def verify(rows):
    """Recomputed CLV vs the stored `clv_pinnacle_live`, SPLIT BY LEDGER.

    Splitting matters. On `shadow_bets` the recompute reproduces the stored
    column almost exactly, which is what makes the new asian_handicap numbers
    trustworthy. On `simulated_bets` it does not, and the reason is a real
    defect rather than a rounding difference — see the printed note.
    """
    for src in ("shadow", "sim"):
        pairs = [(float(r["clv_pinnacle_live"]), r["clv"], r) for r in rows
                 if r["src"] == src and r.get("clv") is not None
                 and r.get("clv_pinnacle_live") is not None]
        if len(pairs) < 10:
            print(f"\nVERIFY [{src}]: too few overlapping rows to check.")
            continue
        d = [b - a for a, b, _ in pairs]
        n, m, _, _ = summarise(d)
        exact = sum(1 for x in d if abs(x) <= 0.002) / n
        ma = sum(a for a, _, _ in pairs) / n
        mb = sum(b for _, b, _ in pairs) / n
        print(f"\nVERIFY [{src:<6}] n={n:<6} stored mean {ma*100:+.2f}%  "
              f"recomputed {mb*100:+.2f}%  mean diff {m*100:+.2f}pp  "
              f"agree within 0.2pp: {exact*100:.0f}%")
    print("""
  simulated_bets.clv_pinnacle_live IS NOT THE SAME QUANTITY as
  shadow_bets.clv_pinnacle_live, and nothing in the codebase writes it.
  settlement.py writes the shadow column only, from the DE-VIGGED Pinnacle
  probability. The simulated column was populated once (migration 300 era) by
  rescaling the RAW `clv_pinnacle` to the live price, so it still carries
  Pinnacle's overround: e.g. one o/u row stores +129.41pct, which implies a
  Pinnacle 'over 2.5' close of 1.19. Anything that pools the two columns —
  `weekly_bot_review.py` does, via
  COALESCE(clv_pinnacle_live, clv_live, clv_pinnacle, clv) — is averaging a
  de-vigged number with a vigged one. This script recomputes instead of reading
  it, so nothing below inherits the defect.""")


def coverage_report(rows_all, rows_kept):
    """Per-market coverage, never a single aggregate. 54.7pct overall once looked
    like ordinary snapshot gaps and was actually one market at 92pct and another
    at 0pct (gotcha 32)."""
    tot, kept, anch = defaultdict(int), defaultdict(int), defaultdict(int)
    for r in rows_all:
        tot[r["family"]] += 1
        if r.get("true_p") is not None:
            anch[r["family"]] += 1
    for r in rows_kept:
        kept[r["family"]] += 1
    print("\nCOVERAGE — settled prematch singles, per market")
    print(f"  {'market':<18} {'settled':>8} {'pin anchor':>11} "
          f"{'executable':>11} {'usable pct':>11}")
    for f in sorted(tot):
        pct = 100.0 * kept[f] / tot[f] if tot[f] else 0.0
        print(f"  {f:<18} {tot[f]:>8} {anch[f]:>11} {kept[f]:>11} {pct:>10.1f}%")


def band_coverage_bias(rows_all):
    """Does executable-price coverage itself depend on the odds band? If the top
    band is systematically less covered, the headline cell is a different
    population rather than a different price."""
    print("\nCOVERAGE BY BAND (banded on `odds_at_pick`, so uncovered rows still appear)")
    print(f"  {'market / band':<32} {'settled':>8} {'usable':>8} {'pct':>7}")
    g = defaultdict(lambda: [0, 0])
    for r in rows_all:
        px = r.get("odds_at_pick")
        if not px:
            continue
        b = band_of(float(px))
        if b is None:
            continue
        cell = g[(r["family"], b)]
        cell[0] += 1
        if r.get("clv") is not None:
            cell[1] += 1
    for k in sorted(g):
        t, u = g[k]
        print(f"  {k[0] + ' / ' + k[1]:<32} {t:>8} {u:>8} "
              f"{(100.0*u/t if t else 0):>6.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=15,
                    help="minimum rows for a cell to be reported (default 15)")
    ap.add_argument("--perms", type=int, default=500)
    ap.add_argument("--verify", action="store_true",
                    help="check recomputed CLV against the stored column")
    args = ap.parse_args()

    print("ODDS-BAND-BY-MARKET-AUDIT — CLV at executable prices, by market x band")
    print("=" * 78)
    rows = load_rows()
    stats = attach_true_prob(rows)
    kept = [r for r in rows if r.get("clv") is not None]
    print(f"\nloaded {len(rows)} settled prematch singles; "
          f"usable at executable prices {len(kept)}")
    for k in sorted(stats):
        if k != "kept":
            print(f"  dropped {k:<26} {stats[k]}")
    print(f"  distinct matches in the usable set: "
          f"{len({r['match_id'] for r in kept})} "
          f"({len(kept)/max(1, len({r['match_id'] for r in kept})):.2f} rows per match "
          f"— why a cluster-robust t is printed next to the iid one)")

    if args.verify:
        verify(kept)
    coverage_report(rows, kept)
    band_coverage_bias(rows)

    print("\n" + "=" * 78)
    print("HOW TO READ THE TABLES")
    print("  CLV    de-vigged-Pinnacle closing-line value at the EXECUTABLE price")
    print("         (`odds_at_pick_live`). This is the column to judge on.")
    print("  t      iid t-stat.  t(clu)  same, clustered on match_id. Several bots")
    print("         bet the same fixture; where the two diverge, believe t(clu).")
    print("  entry  mean stored edge_percent at pick time — for line-shop bots this")
    print("         IS the selection rule, so their CLV is not independent evidence.")
    print("  LS     share of the cell's rows from a line-shop bot (LINESHOP_BOTS).")
    print("  ROI    unit return, with its standard error. UNDERPOWERED by")
    print("         construction: unit-return sd ~1.42 needs ~19,400 bets for")
    print("         +/-2pp where clv sd ~0.19 needs ~334 (gotcha 8). It is printed")
    print("         so nobody recomputes it, not so it can be acted on.")

    print_table("MARKET x BAND — ALL BOTS, BOTH LEDGERS",
                cells(kept, lambda r: (r["family"], r["band"]), args.min_n),
                band_key)

    model = [r for r in kept if r["bot"] not in LINESHOP_BOTS]
    lineshop = [r for r in kept if r["bot"] in LINESHOP_BOTS]
    print_table("MARKET x BAND — MODEL-DRIVEN BOTS ONLY (the honest test)",
                cells(model, lambda r: (r["family"], r["band"]), args.min_n),
                band_key)
    print_table("MARKET x BAND — LINE-SHOP BOTS ONLY (CLV here is CIRCULAR: it is "
                "their entry rule)",
                cells(lineshop, lambda r: (r["family"], r["band"]), args.min_n),
                band_key)

    live_model = [r for r in model if r["bot_live"]]
    print_table("MARKET x BAND — NON-RETIRED MODEL-DRIVEN BOTS",
                cells(live_model, lambda r: (r["family"], r["band"]), args.min_n),
                band_key)

    print_table("BOT x MARKET x BAND (non-retired bots)",
                cells([r for r in kept if r["bot_live"]],
                      lambda r: (r["bot"], r["family"], r["band"]), args.min_n),
                lambda k: f"{k[0]:<26} {k[1]:<14} {k[2]}")

    print_table("MODEL-DRIVEN BOT x BAND, 1x2 AND OU ONLY, retired bots INCLUDED\n"
                "  (the pooled model table hides that the bots disagree with each other)",
                cells([r for r in model if r["family"] in ("1x2", "over_under")],
                      lambda r: (r["bot"], r["family"], r["band"]), args.min_n),
                lambda k: f"{k[0]:<26} {k[1]:<14} {k[2]}")

    print_table("LEAGUE TIER x MARKET x BAND (all bots)",
                cells(kept, lambda r: (f"tier{r['tier']}", r["family"], r["band"]),
                      args.min_n),
                lambda k: f"{k[0]:<7} {k[1]:<16} {k[2]}")

    pre = [r for r in kept if str(r["pick_time"].date()) < RECAL_DATE]
    post = [r for r in kept if str(r["pick_time"].date()) >= RECAL_DATE]
    print(f"\nENSEMBLE-RECALIBRATION split (gotcha 39): {len(pre)} rows before "
          f"{RECAL_DATE}, {len(post)} on/after. Everything above is therefore a "
          f"PRE-recalibration measurement;\nthe post-change segment is far too "
          f"small to conclude anything and is not shown as a pooled fallback.")

    print("\n" + "=" * 78)
    print("ASIAN HANDICAP — high-water price basis, INFLATED, not actionable")
    print("  0 of the settled AH rows carry `odds_at_pick_live`: the backfill that")
    print("  populates it covered 1x2 first and over/under from")
    print("  OU-LIVE-PRINT-BLIND-2026-09-03, and AH was never added. The de-vigged")
    print("  Pinnacle ANCHOR works fine for AH (pair home/away at the same")
    print("  handicap_line), so the only thing missing is the executable price.")
    print("  The table below therefore prices AH at `odds_at_pick`, a MAX() over")
    print("  the fixture's whole snapshot history — the exact basis that overstated")
    print("  1x2 ROI by +5.80pp. Read it for the SHAPE across bands, never for the")
    print("  level, and do not gate on it.")
    print("\n" + _ah_note())
    ah = [r for r in rows if r["family"] == "asian_handicap"]
    print_table("ASIAN HANDICAP x BAND (high-water basis — INFLATED)",
                cells(ah, lambda r: ("ah[hw]", r["band_hw"]), args.min_n,
                      clv_key="clv_hw", ret_key="unit_return_hw"), band_key)
    print_table("1X2 / OU x BAND on the SAME high-water basis "
                "(calibration for how much the basis inflates)",
                cells([r for r in rows if r["family"] in ("1x2", "over_under")],
                      lambda r: (r["family"] + "[hw]", r["band_hw"]), args.min_n,
                      clv_key="clv_hw", ret_key="unit_return_hw"), band_key)

    print("\n" + "=" * 78)
    print("PLACEBO — the grid is a search, so the null is the BEST cell in it.")
    print("Statistic is centred within market, so this tests the BAND effect and")
    print("not the market's overall level.")
    for label, pop in (("all bots", kept), ("model-driven bots only", model)):
        real, cell, null, p = placebo_max_t(pop, args.min_n, args.perms)
        q = lambda f: null[min(len(null) - 1, int(f * len(null)))]  # noqa: E731
        print(f"\n  [{label}]  best cell: {cell[0]} {cell[1]}  n={cell[5]}  "
              f"CLV {cell[2]*100:+.2f}%  (deviation {cell[3]*100:+.2f}pp, "
              f"t={cell[4]:+.2f})")
        print(f"    shuffled max |t| over {args.perms} perms: median {q(0.50):.2f}, "
              f"p90 {q(0.90):.2f}, p95 {q(0.95):.2f}, p99 {q(0.99):.2f}, "
              f"max {null[-1]:.2f}")
        print(f"    family-wise p = {p:.4f}  -> "
              f"{'CLEARS the placebo' if p < 0.05 else 'DOES NOT clear the placebo'}")

    print("\n  PER-BOT band placebo — the cell that decides this ticket.")
    print("  The pooled model-driven table and the non-retired one disagree in sign")
    print("  at 1x2 3.5-5.0, so the question is whether ONE bot really prices the")
    print("  top band better or whether a bot-sized subset of a search grid found")
    print("  noise. Each bot is tested against ITS OWN shuffled bands, so the null")
    print("  already contains 'somebody in this fleet looks good somewhere'.")
    by_bot = defaultdict(list)
    for r in model:
        by_bot[r["bot"]].append(r)
    for bot, rs in sorted(by_bot.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 120:
            continue
        # A bot whose grid has only ONE surviving cell has nothing to permute
        # against: the shuffled max |t| collapses to ~0 and every real number
        # then looks decisive. bot_dc_strong_fav produced exactly that — a
        # -0.03pp deviation at "p=0.0040" against a null p95 of 0.05.
        grid = cells(rs, lambda r: (r["family"], r["band"]), args.min_n)
        if len(grid) < 2:
            print(f"    {bot:<24} n={len(rs):<5} only {len(grid)} cell(s) reach "
                  f"min-n — no band contrast to test, skipped")
            continue
        real, cell, null, p = placebo_max_t(rs, args.min_n, args.perms)
        if cell is None:
            continue
        q95 = null[min(len(null) - 1, int(0.95 * len(null)))]
        print(f"    {bot:<24} n={len(rs):<5} best {cell[0]:<14}{cell[1]:<9} "
              f"n={cell[5]:<4} CLV {cell[2]*100:+6.2f}%  dev {cell[3]*100:+6.2f}pp  "
              f"t={cell[4]:+5.2f}  null p95={q95:.2f}  p={p:.4f}"
              f"{'  *' if p < 0.05 else ''}")

    print("\n  single-cell placebos (model-driven bots only, centred within market)")
    for fam, band in (("1x2", "<2.0"), ("1x2", "2.0-2.8"), ("1x2", "2.8-3.5"),
                      ("1x2", "3.5-5.0"), ("1x2", "5.0+"),
                      ("over_under", "<2.0"), ("over_under", "2.0-2.8"),
                      ("over_under", "2.8-3.5"), ("over_under", "3.5-5.0"),
                      ("double_chance", "<2.0"), ("double_chance", "2.0-2.8")):
        res = placebo_cell(model, fam, band, args.perms)
        if res:
            level, dev, pc, n = res
            print(f"    {fam:<15} {band:<8} n={n:<5} CLV {level*100:+6.2f}%  "
                  f"deviation {dev*100:+6.2f}pp  p={pc:.4f}"
                  f"{'  *' if pc < 0.05 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
