#!/usr/bin/env python3
"""OWN per-market bots — an ADVERSARIAL re-run of the market-expansion sweep.

Read-only. Writes nothing. See docs/OWN_PER_MARKET_BOTS_2026_09_14.md.

WHY THIS EXISTS. `docs/OWN_MARKET_EXPANSION_2026_09_14.md` concluded "add no
market; none of 17 qualifies". Four things in that run are attacked here, each
of which is a known repo failure mode reappearing:

  A. THE FUNCTIONAL-FORM HOLE (ANALYSIS_GOTCHAS §42), for the FIFTH time.
     `own_market_expansion_sweep.build_bets` scores each leg
        edge = p * odds - 1                      (an EXPECTED-ROI floor)
     and `evaluate` gates on `edge >= floor`. The live gate in
     `workers/jobs/pick_triggers._window` is
        cal - 1/odds >= edge_floor               (a PROBABILITY-DIFFERENCE floor)
     and since roi_edge = prob_edge * odds the two are the SAME curve only at
     odds 1.0. A constant ROI floor is a FALLING prob-edge requirement in odds
     (2% ROI edge is 2.0pp of probability at 1.00 and 0.5pp at 4.00), so it
     preferentially admits LONG prices; a constant prob floor does the opposite.
     `docs/OWN_SWEEP_VERIFICATION_2026_09_14.md` found the only coherent
     positive family in 1x2/OU at odds <= 2.50 — exactly the band an ROI floor
     under-samples. So the market sweep's "no edge" verdict is unproven for the
     gate we actually run. This script sweeps BOTH forms, and BANDS.

  B. THE 17-DAY WINDOW IS WRONG FOR HALF THE CATALOGUE. The sweep ran
     `--days 17` on every market, justified by "every bolt-on market's first row
     is 2026-08-29". Measured here, that is false for four of them:
        over_under_15/35/45  Coolbet from 2026-05-20, Pinnacle from 2026-04-23
        asian_handicap       Coolbet from 2026-05-28, Pinnacle from 2023-07-21
        double_chance        Coolbet from 2026-05-31
        btts                 Coolbet from 2026-05-20
     The window discarded ~100 days of exactly the markets that had them.
     `--days` here defaults to 120 and every cell prints its own DATE SPAN.

  C. GATE 1 CONFLATED "PINNACLE QUOTES THIS MARKET" WITH "A SHARP ANCHOR
     EXISTS". Double chance failed gate 1 as "Pinnacle does not price it" —
     but DC outcomes are unions of 1X2 outcomes, so de-vigged Pinnacle 1X2
     gives P(1X)=P(h)+P(d) exactly (ANALYSIS_GOTCHAS §4, already implemented in
     get_devigged_pinnacle_close_prob). DC is rebuilt here on a synthetic
     anchor. (BTTS is NOT derivable from 1X2 — it needs the joint, not the
     margin — so its gate-1 failure stands.)

  D. POOLING HIDES / MANUFACTURES BOOK EFFECTS (§10, §62) and the three books
     have different feed start dates. Every cell is reported PER BOOK as well as
     pooled, always with its date span, because a 4-hour lead filter silently
     selecting one 11-day era is how a +16% was retracted this morning.

WHAT IS REUSED AND WHY. Leg construction, assembly (§63), grading, the de-vig,
the outlier guard and the clustered CI are imported verbatim from
`own_market_expansion_sweep`. Two independent implementations already agreed on
six dipstick figures to the digit (OWN_SWEEP_VERIFICATION claim 3), so
re-writing them a third time buys nothing; the SELECTION RULE — which is where
the hole is — is re-implemented here from scratch.

NEGATIVE CONTROL. Driven at the IDENTICAL gate (same floor, same band, same
one-bet-per-fixture collapse), never at a nominally-equal floor that passes 10x
the legs — that mismatch is what made "junk beats real" look true this morning.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query
from workers.model.devig import devig

import scripts.own_market_expansion_sweep as S


BANDS = [(1.01, 2.00), (1.01, 2.50), (1.01, 99.0)]
FLOORS = [0.01, 0.02, 0.03, 0.05]
# The ONE cell nominated in advance, so the other 47 per market are exploratory
# and are counted as such (§60 / multiple comparisons).
PRIMARY = (0.02, (1.01, 2.50))


# ── double chance: the synthetic sharp anchor gate 1 never built ─────────────
DC_SEL = ("1x", "12", "x2")
DC_FROM_1X2 = {"1x": ("home", "draw"), "12": ("home", "away"), "x2": ("draw", "away")}


def load_dc(days: int, ko_h: float | None):
    """Coolbet/Epicbet double_chance quotes + Pinnacle 1x2 quotes, one pull."""
    ko = "" if ko_h is None else f" AND o.timestamp >= mt.date - interval '{ko_h} hours'"
    return execute_query(
        f"""
        SELECT o.match_id, o.bookmaker, o.market, o.selection, o.odds::float AS odds,
               NULL::float AS line, o.timestamp, mt.date, mt.status,
               mt.score_home, mt.score_away, mt.ht_score_home, mt.ht_score_away,
               NULL::int AS corners_home, NULL::int AS corners_away,
               NULL::int AS corners_home_ht, NULL::int AS corners_away_ht,
               NULL::int AS n_cards
          FROM odds_snapshots o JOIN matches mt ON mt.id = o.match_id
         WHERE o.timestamp > now() - (%s || ' days')::interval
           AND o.is_live IS NOT TRUE AND o.timestamp < mt.date{ko}
           AND ((o.bookmaker = ANY(%s) AND o.market = 'double_chance')
             OR (o.bookmaker = 'Pinnacle' AND o.market = '1x2'))
        """,
        (str(days), list(S.EXEC_BOOKS)),
    )


def build_dc_legs(days: int, ko_h, align_min: float, scramble=False, rng=None):
    """Legs for double chance against a Pinnacle-1X2-derived anchor.

    The anchor's quoted-equivalent price is 1/P_dc inflated by Pinnacle's own
    1X2 overround, so the production outlier guard compares like with like — a
    guard run against the FAIR price would be looser than production by exactly
    the vig, which is the direction that lets a mislabelled quote through."""
    rows = load_dc(days, ko_h)
    obs, facts = defaultdict(lambda: defaultdict(list)), {}
    for r in rows:
        if r["odds"] is None or r["odds"] <= 1.0:
            continue
        sel, mk = r["selection"], r["market"]
        if mk == "1x2" and sel not in S.X3:
            continue
        if mk == "double_chance" and sel not in DC_SEL:
            continue
        obs[r["match_id"]][(r["bookmaker"], mk)].append((r["timestamp"], sel, r["odds"]))
        facts[r["match_id"]] = r

    pool = []
    prepared = {}
    for mid, by in obs.items():
        a = S.assemble(by.get(("Pinnacle", "1x2"), []), S.X3)
        if not a:
            continue
        anc = []
        for ts, q in a:
            d = devig([q[s] for s in S.X3])
            if not d:
                continue
            p3 = dict(zip(S.X3, d))
            v = sum(1.0 / q[s] for s in S.X3) - 1.0
            pdc = {k: p3[x] + p3[y] for k, (x, y) in DC_FROM_1X2.items()}
            anc.append((ts, pdc, v))
            pool.append(pdc)
        if anc:
            prepared[mid] = (anc, by)

    out = []
    for mid, (anc, by) in prepared.items():
        f = facts[mid]
        for b in S.EXEC_BOOKS:
            eq = S.assemble(by.get((b, "double_chance"), []), DC_SEL)
            if not eq:
                continue
            best = None
            for ts, q in reversed(eq):
                at, pdc, v = min(anc, key=lambda x: abs((x[0] - ts).total_seconds()))
                gap = abs((at - ts).total_seconds()) / 60.0
                if gap <= align_min:
                    best = (ts, q, pdc, v, gap)
                    break
            if best is None:
                continue
            ts, q, pdc, v, gap = best
            if scramble:
                if len(pool) < 2:
                    continue
                pdc = rng.choice(pool)
            legs = []
            for s in DC_SEL:
                if pdc[s] <= 0 or pdc[s] >= 1:
                    continue
                anchor_quote = (1.0 / pdc[s]) / (1.0 + v)
                if q[s] > anchor_quote * S.OUTLIER_MULT_3WAY:
                    continue
                g = S.grade(S.MARKETS_BY_KEY["double_chance"], s, (None, None), f)
                if g is None:
                    continue
                legs.append({"sel": s, "odds": q[s], "p": pdc[s],
                             "edge": pdc[s] * q[s] - 1.0, "grade": g})
            if legs:
                out.append({"mid": mid, "key": (None, None), "book": b,
                            "date": f["date"], "gap": gap, "legs": legs,
                            "status": f["status"]})
    return out


# ── the selection rule, re-implemented ──────────────────────────────────────
def prob_edge(leg) -> float:
    return leg["p"] - 1.0 / leg["odds"]


def select(bets, floor, band, book=None, form="prob", one_per_fixture=True):
    """Legs clearing `floor` under `form` and inside `band`.

    form='prob' : p - 1/odds >= floor        <- the LIVE gate (pick_triggers)
    form='roi'  : p*odds - 1  >= floor       <- what the market sweep swept

    one_per_fixture reproduces the market sweep's blast-radius collapse (best
    leg per fixture+book). Off, every qualifying leg counts — more power, more
    within-fixture correlation, which the clustered CI already absorbs."""
    lo, hi = band
    picked = []
    for b in bets:
        if book is not None and b["book"] != book:
            continue
        ok = []
        for l in b["legs"]:
            if not (lo <= l["odds"] <= hi):
                continue
            e = prob_edge(l) if form == "prob" else l["edge"]
            if e >= floor:
                ok.append((e, l))
        if not ok:
            continue
        if one_per_fixture:
            picked.append({**b, "leg": max(ok, key=lambda x: x[0])[1],
                           "score": max(x[0] for x in ok)})
        else:
            for e, l in ok:
                picked.append({**b, "leg": l, "score": e})
    if not one_per_fixture:
        return picked
    by = {}
    for p in picked:
        k = (p["mid"], p["book"])
        if k not in by or p["score"] > by[k]["score"]:
            by[k] = p
    return list(by.values())


def cell(picked):
    r = S.roi_ci(picked)
    if r is None:
        return None
    ds = sorted(p["date"] for p in picked)
    r["first"], r["last"] = ds[0].date(), ds[-1].date()
    r["days"] = (ds[-1] - ds[0]).days + 1
    return r


def fmt(r):
    return (f"n={r['n']:5d} fx={r['fixtures']:5d} ROI {r['roi']*100:+7.2f}% "
            f"CI[{r['lo']*100:+7.2f},{r['hi']*100:+7.2f}] gap {r['median_gap']:5.1f}m "
            f"{r['first']}..{r['last']} ({r['days']:3d}d)")


def holdout(picked, frac=0.25):
    s = sorted(picked, key=lambda p: p["date"])
    if len(s) < 40:
        return None, None
    k = int(len(s) * (1 - frac))
    return cell(s[:k]), cell(s[k:])


# ── driver ──────────────────────────────────────────────────────────────────
def run_market(key, days, ko_h, align_min, rng, floors, bands, forms,
               one_per_fixture, verbose_all):
    m = S.MARKETS_BY_KEY[key]
    print(f"\n{'='*100}\n{m.label}  [{key}]  days<={days}  align<={align_min:.0f}m"
          f"  ko_window={ko_h}h\n{'='*100}", flush=True)

    if key == "double_chance":
        bets = build_dc_legs(days, ko_h, align_min)
        ctrl = build_dc_legs(days, ko_h, align_min, scramble=True, rng=rng)
        allowed = None
    else:
        rows = S.load_market(m, days, (S.ANCHOR_BOOK,) + S.EXEC_BOOKS)
        print(f"  rows loaded: {len(rows):,}")
        obs, facts = S.index_rows(m, rows)
        allowed = None
        if m.lined or m.fixed_line is not None:
            q = S.gate_same_quantity(m, days, diag_books=())
            if q["applicable"]:
                allowed = q["pass_keys"]
                print(f"  same-quantity: {q['n_pass']}/{q['n_lines']} lines pass"
                      f"  flat-ladder books: {sorted(q['flat_books']) or 'none'}")
                if not q["pass"]:
                    print("  -> QUANTITY FAIL")
                    return
        cal = S.gate_settlement_calibration(m, obs, facts, allowed)
        if cal:
            print(f"  grading-vs-anchor: n={cal['n']} anchor={cal['anchor']:.3f} "
                  f"realised={cal['realised']:.3f} gap={cal['gap']*100:+.1f}pp z={cal['z']:+.1f}")
        bets = S.build_bets(m, obs, facts, align_min, allowed_keys=allowed)
        ctrl = S.build_bets(m, obs, facts, align_min, scramble=True, rng=rng,
                            allowed_keys=allowed)
    print(f"  candidate fixtures*book: {len(bets):,}   junk-arm: {len(ctrl):,}")
    if not bets:
        return

    # per-book availability, printed because a cell's span is only meaningful
    # against the book's own feed history
    for b in S.EXEC_BOOKS:
        sub = [x for x in bets if x["book"] == b]
        if sub:
            ds = sorted(x["date"] for x in sub)
            print(f"    {b:14s} candidates={len(sub):6,}  {ds[0].date()}..{ds[-1].date()}")

    n_cells = 0
    for form in forms:
        for fl in floors:
            for band in bands:
                for bk in list(S.EXEC_BOOKS) + [None]:
                    r = cell(select(bets, fl, band, bk, form, one_per_fixture))
                    if r is None or r["n"] < 25:
                        continue
                    n_cells += 1
                    c = cell(select(ctrl, fl, band, bk, form, one_per_fixture))
                    prim = (form == "prob" and (fl, band) == PRIMARY)
                    star = " *PRIMARY" if prim else ""
                    sig = r["lo"] > 0 or r["hi"] < 0
                    if not (verbose_all or prim or (sig and r["n"] >= 40)):
                        continue
                    jk = (f"n={c['n']:4d} {c['roi']*100:+6.2f}%" if c else "n/a")
                    print(f"  [{form}] floor {fl*100:4.1f}% band {band[0]:.2f}-{band[1]:<5.2f} "
                          f"{(bk or 'POOLED'):14s} {fmt(r)} | junk {jk:>18s}"
                          f" | n@80% {S.power_n(abs(r['roi']), r['sd']):>10,.0f}{star}")
                    fo = S.folds(select(bets, fl, band, bk, form, one_per_fixture))
                    if fo:
                        print("      folds " + " / ".join(
                            f"{x['roi']*100:+.1f}%(n={x['n']})" for x in fo))
                    is_, oos = holdout(select(bets, fl, band, bk, form, one_per_fixture))
                    if oos:
                        print(f"      IS  {fmt(is_)}\n      OOS {fmt(oos)}")
    print(f"  cells with n>=25 evaluated for this market: {n_cells}")
    return n_cells


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="1x2,over_under_25")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--ko-window-h", type=float, default=None,
                    help="only quotes within this many hours of kickoff (volume guard)")
    ap.add_argument("--align-min", type=float, default=15.0)
    ap.add_argument("--floors", default="0.01,0.02,0.03,0.05")
    ap.add_argument("--bands", default="1.01-2.00,1.01-2.50,1.01-99")
    ap.add_argument("--forms", default="prob,roi")
    ap.add_argument("--all-legs", action="store_true",
                    help="do NOT collapse to one bet per fixture+book")
    ap.add_argument("--verbose-all", action="store_true")
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()

    floors = [float(x) for x in a.floors.split(",")]
    bands = [tuple(float(y) for y in x.split("-")) for x in a.bands.split(",")]
    forms = [x for x in a.forms.split(",") if x]
    rng = random.Random(a.seed)
    total = 0
    for k in a.markets.split(","):
        if not k:
            continue
        total += run_market(k, a.days, a.ko_window_h, a.align_min, rng, floors,
                            bands, forms, not a.all_legs, a.verbose_all) or 0
    print(f"\nTOTAL CELLS (n>=25) EVALUATED: {total}. Per-bet return sd ~1.3; "
          f"§60 power applies to every one of them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
