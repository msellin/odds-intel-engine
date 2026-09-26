#!/usr/bin/env python3
"""[[#182]] OWN-bot design research — which PICK-TIME filters make a per-book sharp trigger better?

Direction: 🤖 OWN. The owner wants one OWN bot per Estonian book we can bet at (Coolbet,
Unibet-Site, Epicbet, Tonybet), with picks of HIGHER quality than the public ones; volume does
not matter. The candidate base signal is the existing "book price beats Pinnacle's de-vigged fair
price" trigger. #150 graded those bots on the independent close at the pick-time price: Coolbet
1X2 +2.67% [−0.4, +5.7], Unibet 1X2 +2.58% [−0.7, +5.8], tight +1.24% — all undetermined. The
stale-window study (docs/STALE_WINDOW_STUDY_2026_09_24.md §8/§10) found that the legs where ONLY
Pinnacle moved are the ones the rest of the market does not follow (grader gap ~7.9 pts). This
study asks whether a short, pre-declared list of filters, each computable at pick time, separates
the good legs from the bad ones.

READ-ONLY. Nothing in production changes. Writes data/models/_research/own182/ (gitignored).

    python3 scripts/analysis/own_bot_filter_study.py

═══════════════════════════════ PRE-REGISTRATION ═══════════════════════════════
Written 2026-09-26, BEFORE the first run. The only data looked at beforehand were leg COUNTS per
bot/book (coverage), never an outcome or a CLV.

POPULATION
  * Every bot with anchor == ANCHOR_SHARP in workers/registry/bot_registry.py, all ledgers
    (shadow / forward_test), bot_ledger legs with result in (won, lost), NOT is_inplay,
    bookmaker in {Coolbet, Unibet-Site, Epicbet, Tonybet}.
  * PRIMARY market: 1x2. over_under_25 is reported descriptively only (too few legs, #150: < 50
    judged legs per O/U bot) and is outside the test family.
  * De-duplicated on (match, market, selection, bookmaker): the per-book triggers, the tight bot,
    trigger_1x2_sharp and the forward-test sharp arm often take the SAME bet; keep the earliest
    pick_time. The filters are about the bet, not the bot.
  * Excluded: (match, book) pairs with a row in odds_snapshots_quarantined or
    data_quality_findings (same exclusion as #140 bot_slice_analysis.py).

JUDGE (the only verdict metric)
  * CLV_ind = snap_odds × p_close_cons − 1, exactly #150 AMENDMENT 1:
      snap_odds    = the leg's OWN book's latest pre-kickoff odds_snapshots quote for that
                     selection at or before pick_time, no older than FRESH_MIN = 180 min
                     (the price the book actually showed; recorded prices before #162 W2.1 were
                     rewritten on re-sweeps and are not used);
      p_close_cons = leg_clv_sharp.p_close_cons, cons_status = 'ok': ≥ 5-book de-vigged
                     consensus close, Pinnacle AND the leg's own book excluded.
    Legs without either are not graded (coverage reported).
  * Context only, never a verdict: flat ROI at snap_odds; Pinnacle-close CLV at snap_odds.

FILTERS — all computed from odds_snapshots rows timestamped ≤ pick_time (no look-ahead).
Per book, the latest COMPLETE 1x2 set (workers.utils.anchor.sets_from_rows) in
[pick_time − 180 min, pick_time]; fair probabilities by workers.model.devig.devig (Shin).
  (a) CONSENSUS CONFIRMATION. p_pin = Pinnacle's fair prob for the selection; p_oth = MEDIAN fair
      prob over the OTHER books' sets (not Pinnacle, not the four Estonian books, not
      NEVER_IN_ANCHOR, one per SKIN_OF platform); needs ≥ 3 such books.
        a1 confirmed     p_oth ≥ 0.97 × p_pin   (the others do not say "less likely" by > 3% rel.)
        a2 unconfirmed   p_oth <  0.97 × p_pin   (only Pinnacle sees the value)
      Hypothesis: a2 are the losers (stale study §8/§10), a1 carries the value.
      ⚠️ Stated in advance: the judge is the consensus CLOSE of largely the same books, so a1 is
      partly favoured BY CONSTRUCTION (if the consensus does not move, CLV_ind ≈ the pick-time
      edge vs the consensus). The mirror image of ANALYSIS_GOTCHAS §67/§85. A surviving a1 is
      therefore reported with its "movement" part (CLV_ind − pick-time edge vs p_oth) and its
      ROI, and would need the exchange close (#121 §9) before it is trusted with money.
  (b) ODDS BAND (snap_odds):          b1 < 2.0   b2 2.0–3.5   b3 > 3.5
  (c) HOURS TO KICKOFF at pick:       c1 < 3 h   c2 3–12 h    c3 > 12 h
  (d) LIQUIDITY = number of books (any) with a complete 1x2 set in the window; tertiles of the
      graded population (outcome-blind): d1 low, d2 mid, d3 high.
  (e) FRESHNESS, split at the population median (outcome-blind):
        e1/e2  Pinnacle set age ≤ / > median
        e3/e4  own-book quote age ≤ / > median
  (f) EDGE vs Pinnacle fair at pick = snap_odds × p_pin − 1:  f1 < 3%   f2 3–6%   f3 ≥ 6%
Plus cell 0 = the unfiltered trigger (baseline). A leg missing a feature is left out of that
filter's cells only (count reported).

FAMILY: 1 + 2 + 3 + 3 + 3 + 2 + 2 + 3 = 19 cells, 1x2 only, pooled over the four books. Per-book
numbers are DESCRIPTIVE (outside the family) — the family stays small on purpose (§47).

TEST
  * Split at the median pick_time of the graded 1x2 population: older half = DISCOVERY, newer
    half = HOLDOUT (as #140 bot_slice_analysis.py).
  * Discovery: mean CLV_ind per cell, bootstrap over MATCHES (10,000 resamples, seed 182),
    one-sided p = share of resampled means ≤ 0. A cell is tested only with ≥ 30 discovery
    matches and ≥ 20 holdout matches; otherwise it enters Holm at p = 1.
  * Holm over all 19 discovery p-values at α = 0.05.
  * SURVIVES = Holm p < 0.05 AND the holdout mean > 0 AND holdout mean ≥ max(1.0 pt, 50% of the
    discovery mean) — #140's rule.
  * Also reported per cell: full-sample mean with 95% bootstrap CI, and the mean of the
    complementary legs (keep − drop is descriptive).

EXPECTED RESULT (stated before running)
  * Baseline 1x2 CLV_ind ≈ +1.5 … +2.5%, CI touching zero in the discovery half.
  * a2 (Pinnacle-only) clearly below a1; a1 positive — but partly by construction (see above).
  * b: longshots (> 3.5) noisiest; no clean band effect. c/e: fresher / nearer kickoff slightly
    better, not significant. d: no effect. f: bigger edges NOT better (§67 — the biggest
    "edges" are where a feed is wrong).
  * After Holm + holdout: most likely NO filter survives; if one does, it is a1 and it carries
    the construction caveat. Epicbet and Tonybet have no per-book bot (Epicbet legs come from the
    tight bot / forward-test arm, Tonybet only since 2026-09-21 → too few to say anything).

EXPLORATORY ADDENDUM (added AFTER the first run, 2026-09-26 — OUTSIDE the family, no p-values,
not a verdict). The three survivors (a1, c1, e1) overlap, so the first run's legs were cross-cut to see
which one carries the effect: c1∧a1, c1∧¬a1, a1∧¬c1, e1∧¬c1, a1∧c1∧e1. Printed after the pre-registered
tables with CIs and volume; the pre-registered numbers above are unchanged by it (it runs last, so the
seeded bootstrap for the family is identical).

VOLUME: for the recommended rule, deduped legs per day at each book over the last 7 full days of
pick_time (settled or not), features computed the same way.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EST_BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Tonybet")
FRESH_MIN = 180
B = 10_000
SEED = 182
ALPHA = 0.05
MIN_DISC, MIN_HOLD = 30, 20
HOLD_MIN_ABS, HOLD_MIN_FRAC = 0.01, 0.5
CONFIRM_REL = 0.97
SIDES = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
OUT = ROOT / "data/models/_research/own182"


def load_legs(bots: list[str], settled_only: bool = True) -> list[dict]:
    from workers.api_clients.db import execute_query
    res = "AND l.result IN ('won','lost')" if settled_only else ""
    return execute_query(
        f"""
        SELECT l.bot_name, l.source, l.pick_id::text AS pick_id, l.match_id::text AS match_id,
               l.market, lower(l.selection) AS selection, l.bookmaker, l.pick_time, l.kickoff,
               l.result,
               c.p_close_cons::float AS p_close_cons, c.cons_status,
               c.p_close::float AS p_close_pin, c.status AS pin_status,
               snap.odds::float AS snap_odds, snap.ts AS snap_ts,
               EXISTS (SELECT 1 FROM odds_snapshots_quarantined q
                        WHERE q.match_id = l.match_id AND q.bookmaker = l.bookmaker)
            OR EXISTS (SELECT 1 FROM data_quality_findings f
                        WHERE f.match_id = l.match_id AND f.bookmaker = l.bookmaker) AS data_fault
          FROM bot_ledger l
          LEFT JOIN leg_clv_sharp c
            ON c.leg_id = l.pick_id
           AND c.ledger = CASE l.source WHEN 'sim' THEN 'simulated_bets'
                                        WHEN 'shadow' THEN 'shadow_bets'
                                        WHEN 'forward_test' THEN 'picks_forward_test'
                                        ELSE l.source END
          LEFT JOIN LATERAL (
                SELECT o.odds, o."timestamp" AS ts FROM odds_snapshots o
                 WHERE o.match_id = l.match_id AND o.market = l.market AND o.selection = l.selection
                   AND o.bookmaker = l.bookmaker AND o.is_live IS NOT TRUE
                   AND o."timestamp" <= l.pick_time
                   AND o."timestamp" >= l.pick_time - make_interval(mins => %s)
                 ORDER BY o."timestamp" DESC LIMIT 1) snap ON true
         WHERE l.bot_name = ANY(%s) AND NOT l.is_inplay {res}
           AND l.bookmaker = ANY(%s) AND l.market = ANY(%s)
        """, (FRESH_MIN, bots, list(EST_BOOKS), list(SIDES))) or []


def dedup(rows: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["match_id"], r["market"], r["selection"], r["bookmaker"])
        if k not in best or r["pick_time"] < best[k]["pick_time"]:
            best[k] = r
    return list(best.values())


def pick_time_features(leg: dict) -> dict:
    """Features from snapshots ≤ pick_time only."""
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig
    from workers.utils.anchor import NEVER_IN_ANCHOR, SKIN_OF, sets_from_rows
    sides = SIDES[leg["market"]]
    t = leg["pick_time"]
    rows = execute_query(
        """SELECT o.bookmaker, lower(o.selection) AS sel, o.odds::float AS odds, o."timestamp"
             FROM odds_snapshots o
            WHERE o.match_id = %s AND o.market = %s AND o.is_live IS NOT TRUE AND o.odds > 1.01
              AND o."timestamp" <= %s AND o."timestamp" >= %s
              AND lower(o.selection) = ANY(%s)""",
        (leg["match_id"], leg["market"], t, t - timedelta(minutes=FRESH_MIN), list(sides))) or []
    sets = sets_from_rows(rows, sides)
    i = sides.index(leg["selection"]) if leg["selection"] in sides else None
    f: dict = {"n_books_any": len(sets)}
    if i is None:
        return f
    pin = sets.get("Pinnacle")
    if pin:
        p = devig(pin[0])
        if p:
            f["p_pin"] = p[i]
            f["pin_age_min"] = (t - pin[1]).total_seconds() / 60
    oth, seen = [], set()
    for b, (q, _ts) in sets.items():
        if b == "Pinnacle" or b in EST_BOOKS or SKIN_OF.get(b) in EST_BOOKS or b in NEVER_IN_ANCHOR:
            continue
        plat = SKIN_OF.get(b, b)
        if plat in seen:
            continue
        seen.add(plat)
        p = devig(q)
        if p and min(p) > 0:
            oth.append(p[i])
    f["n_oth"] = len(oth)
    if len(oth) >= 3:
        f["p_oth"] = median(oth)
    if leg.get("snap_ts") is not None:
        f["book_age_min"] = (t - leg["snap_ts"]).total_seconds() / 60
    return f


def enrich(legs: list[dict]) -> None:
    for r in legs:
        r.update(pick_time_features(r))
        r["hours_to_ko"] = (r["kickoff"] - r["pick_time"]).total_seconds() / 3600
        if r.get("snap_odds") and r.get("p_pin"):
            r["edge_pin"] = r["snap_odds"] * r["p_pin"] - 1
        if r.get("snap_odds") and r.get("p_oth"):
            r["edge_oth"] = r["snap_odds"] * r["p_oth"] - 1


def cell_defs(pop: list[dict]) -> dict:
    """name -> predicate (returns True/False, or None when the feature is missing)."""
    nb = sorted(r["n_books_any"] for r in pop)
    t1, t2 = nb[len(nb) // 3], nb[2 * len(nb) // 3]
    pin_med = median([r["pin_age_min"] for r in pop if r.get("pin_age_min") is not None])
    book_med = median([r["book_age_min"] for r in pop if r.get("book_age_min") is not None])

    def need(key, fn):
        return lambda r: None if r.get(key) is None else fn(r)

    defs = {
        "0 baseline": lambda r: True,
        "a1 confirmed": lambda r: None if (r.get("p_oth") is None or r.get("p_pin") is None)
        else r["p_oth"] >= CONFIRM_REL * r["p_pin"],
        "a2 unconfirmed": lambda r: None if (r.get("p_oth") is None or r.get("p_pin") is None)
        else r["p_oth"] < CONFIRM_REL * r["p_pin"],
        "b1 odds<2.0": lambda r: r["snap_odds"] < 2.0,
        "b2 odds 2.0-3.5": lambda r: 2.0 <= r["snap_odds"] <= 3.5,
        "b3 odds>3.5": lambda r: r["snap_odds"] > 3.5,
        "c1 <3h": lambda r: r["hours_to_ko"] < 3,
        "c2 3-12h": lambda r: 3 <= r["hours_to_ko"] <= 12,
        "c3 >12h": lambda r: r["hours_to_ko"] > 12,
        f"d1 books<{t1}": lambda r: r["n_books_any"] < t1,
        f"d2 books {t1}-{t2 - 1}": lambda r: t1 <= r["n_books_any"] < t2,
        f"d3 books>={t2}": lambda r: r["n_books_any"] >= t2,
        f"e1 pin age<={pin_med:.0f}m": need("pin_age_min", lambda r: r["pin_age_min"] <= pin_med),
        f"e2 pin age>{pin_med:.0f}m": need("pin_age_min", lambda r: r["pin_age_min"] > pin_med),
        f"e3 book age<={book_med:.0f}m": need("book_age_min", lambda r: r["book_age_min"] <= book_med),
        f"e4 book age>{book_med:.0f}m": need("book_age_min", lambda r: r["book_age_min"] > book_med),
        "f1 edge<3%": need("edge_pin", lambda r: r["edge_pin"] < 0.03),
        "f2 edge 3-6%": need("edge_pin", lambda r: 0.03 <= r["edge_pin"] < 0.06),
        "f3 edge>=6%": need("edge_pin", lambda r: r["edge_pin"] >= 0.06),
    }
    if t1 == t2:  # degenerate tertiles: keep the family size, the middle cell is just empty
        pass
    return defs, {"books_tertiles": [t1, t2], "pin_age_median": pin_med, "book_age_median": book_med}


def boot(rows: list[dict], key: str, rng) -> dict:
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by[r["match_id"]].append(r[key])
    if not by:
        return {"n": 0, "matches": 0}
    keys = list(by)
    sums = np.array([sum(by[k]) for k in keys])
    cnts = np.array([len(by[k]) for k in keys])
    mean = sums.sum() / cnts.sum()
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return {"n": int(cnts.sum()), "matches": len(keys), "mean": float(mean),
            "p": float((means <= 0).mean()),
            "lo": float(np.quantile(means, 0.025)), "hi": float(np.quantile(means, 0.975))}


def holm(pvals: dict[str, float]) -> dict[str, float]:
    order = sorted(pvals, key=pvals.get)
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * pvals[k]))
        out[k] = run
    return out


def summarise(rows: list[dict]) -> dict:
    roi = [(r["snap_odds"] - 1) if r["result"] == "won" else -1.0 for r in rows]
    pin = [r["clv_pin"] for r in rows if r.get("clv_pin") is not None]
    mv = [r["clv_ind"] - r["edge_oth"] for r in rows if r.get("edge_oth") is not None]
    return {"roi": float(np.mean(roi)) if roi else None,
            "clv_pin": float(np.mean(pin)) if pin else None,
            "movement": float(np.mean(mv)) if mv else None}


def main() -> None:
    from workers.registry.bot_registry import BOTS, ANCHOR_SHARP
    bots = [b.name for b in BOTS if b.anchor == ANCHOR_SHARP]
    raw = load_legs(bots)
    legs = dedup(raw)
    cov = {"raw_settled": len(raw), "dedup": len(legs),
           "data_fault": sum(1 for r in legs if r["data_fault"])}
    legs = [r for r in legs if not r["data_fault"]]
    cov["no_snap"] = sum(1 for r in legs if r["snap_odds"] is None)
    cov["no_cons_close"] = sum(1 for r in legs if r["snap_odds"] is not None and r["cons_status"] != "ok")
    graded = [r for r in legs if r["snap_odds"] is not None and r["cons_status"] == "ok"
              and r["p_close_cons"] is not None]
    for r in graded:
        r["clv_ind"] = r["snap_odds"] * r["p_close_cons"] - 1
        r["clv_pin"] = (r["snap_odds"] * r["p_close_pin"] - 1) if (
            r["pin_status"] == "ok" and r["p_close_pin"]) else None
    print(f"loaded {len(raw)} settled legs → {cov['dedup']} unique; data-fault {cov['data_fault']}; "
          f"no pick-time snap {cov['no_snap']}; no consensus close {cov['no_cons_close']}; graded {len(graded)}")
    enrich(graded)

    rng = np.random.default_rng(SEED)
    pop = [r for r in graded if r["market"] == "1x2"]
    ou = [r for r in graded if r["market"] == "over_under_25"]
    defs, thresholds = cell_defs(pop)
    split = sorted(r["pick_time"] for r in pop)[len(pop) // 2]
    disc = [r for r in pop if r["pick_time"] < split]
    hold = [r for r in pop if r["pick_time"] >= split]
    miss = {k: sum(1 for r in pop if fn(r) is None) for k, fn in defs.items()}

    res: dict[str, dict] = {}
    for name, fn in defs.items():
        inn = [r for r in pop if fn(r) is True]
        out_ = [r for r in pop if fn(r) is False]
        d = {"all": boot(inn, "clv_ind", rng), "complement": boot(out_, "clv_ind", rng),
             "disc": boot([r for r in disc if fn(r) is True], "clv_ind", rng),
             "hold": boot([r for r in hold if fn(r) is True], "clv_ind", rng),
             "missing": miss[name], **summarise(inn)}
        d["per_book"] = {b: {**boot([r for r in inn if r["bookmaker"] == b], "clv_ind", rng),
                             **summarise([r for r in inn if r["bookmaker"] == b])} for b in EST_BOOKS}
        tested = d["disc"].get("matches", 0) >= MIN_DISC and d["hold"].get("matches", 0) >= MIN_HOLD
        d["tested"] = tested
        d["p_disc"] = d["disc"]["p"] if tested else 1.0
        res[name] = d
    adj = holm({k: v["p_disc"] for k, v in res.items()})
    for k, d in res.items():
        d["p_holm"] = adj[k]
        dm, hm = d["disc"].get("mean"), d["hold"].get("mean")
        d["survives"] = bool(d["tested"] and d["p_holm"] < ALPHA and hm is not None and hm > 0
                             and hm >= max(HOLD_MIN_ABS, HOLD_MIN_FRAC * dm))

    pct = lambda x: "   —  " if x is None else f"{x * 100:+6.2f}"
    print(f"\n1X2 graded {len(pop)} legs / {len({r['match_id'] for r in pop})} matches; split at {split} "
          f"(disc {len(disc)}, hold {len(hold)}); thresholds {thresholds}")
    print(f"{'cell':24} {'n':>4} {'m':>4} {'CLV_ind':>7} {'95% CI':>16} | {'disc n':>6} {'disc':>6} {'p':>5} "
          f"{'holm':>5} | {'hold n':>6} {'hold':>6} | {'compl':>6} {'ROI':>6} {'pinCLV':>6} {'move':>6} miss  surv")
    for k, d in res.items():
        a, di, ho = d["all"], d["disc"], d["hold"]
        ci = f"[{a['lo'] * 100:+.1f},{a['hi'] * 100:+.1f}]" if a.get("n") else ""
        print(f"{k:24} {a.get('n', 0):4d} {a.get('matches', 0):4d} {pct(a.get('mean'))} {ci:>16} | "
              f"{di.get('n', 0):6d} {pct(di.get('mean'))} {di.get('p', 1):5.3f} {d['p_holm']:5.3f} | "
              f"{ho.get('n', 0):6d} {pct(ho.get('mean'))} | {pct(d['complement'].get('mean'))} "
              f"{pct(d['roi'])} {pct(d['clv_pin'])} {pct(d['movement'])} {d['missing']:4d}  "
              f"{'YES' if d['survives'] else ('-' if d['tested'] else 'untested')}")
    print("\nper book (descriptive):")
    for k, d in res.items():
        s = "  ".join(f"{b[:7]} n{v.get('n', 0)} {pct(v.get('mean'))}" for b, v in d["per_book"].items())
        print(f"  {k:24} {s}")

    ou_res = {b: {**boot([r for r in ou if r["bookmaker"] == b], "clv_ind", rng),
                  **summarise([r for r in ou if r["bookmaker"] == b])} for b in EST_BOOKS}
    ou_res["pooled"] = {**boot(ou, "clv_ind", rng), **summarise(ou)}
    print("\nO/U 2.5 (descriptive):", {k: (v.get("n"), pct(v.get("mean"))) for k, v in ou_res.items()})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(
        {"coverage": cov, "graded_1x2": len(pop), "split": str(split), "thresholds": thresholds,
         "cells": res, "ou": ou_res}, indent=2, default=str))
    with open(OUT / "legs.jsonl", "w") as fh:
        for r in graded:
            fh.write(json.dumps(r, default=str) + "\n")

    # volume: every deduped 1x2 leg (settled or not) in the last 7 full days, features computed
    allr = [r for r in dedup(load_legs(bots, settled_only=False))
            if not r["data_fault"] and r["market"] == "1x2" and r["snap_odds"] is not None]
    last = max(r["pick_time"] for r in allr).replace(hour=0, minute=0, second=0, microsecond=0)
    recent = [r for r in allr if last - timedelta(days=7) <= r["pick_time"] < last]
    enrich(recent)
    vol = {}
    for k, fn in defs.items():
        vol[k] = {b: round(sum(1 for r in recent if r["bookmaker"] == b and fn(r) is True) / 7, 1)
                  for b in EST_BOOKS}
    print(f"\nvolume per day, 1x2, {last - timedelta(days=7):%Y-%m-%d}..{last - timedelta(days=1):%Y-%m-%d}:")
    for k, v in vol.items():
        print(f"  {k:24} {v}")
    (OUT / "volume.json").write_text(json.dumps(vol, indent=2))

    # ── EXPLORATORY ADDENDUM (post-hoc, outside the family; see docstring) ──
    a1, c1 = defs["a1 confirmed"], defs["c1 <3h"]
    e1 = next(fn for k, fn in defs.items() if k.startswith("e1"))
    combos = {"c1 & a1": lambda r: c1(r) is True and a1(r) is True,
              "c1 & not a1": lambda r: c1(r) is True and a1(r) is False,
              "a1 & not c1": lambda r: a1(r) is True and c1(r) is False,
              "e1 & not c1": lambda r: e1(r) is True and c1(r) is False,
              "a1 & c1 & e1": lambda r: a1(r) is True and c1(r) is True and e1(r) is True}
    print("\nEXPLORATORY (post-hoc, no p-values):")
    expl = {}
    for k, fn in combos.items():
        inn = [r for r in pop if fn(r)]
        d = {"all": boot(inn, "clv_ind", rng), "disc": boot([r for r in disc if fn(r)], "clv_ind", rng),
             "hold": boot([r for r in hold if fn(r)], "clv_ind", rng), **summarise(inn),
             "per_book": {b: boot([r for r in inn if r["bookmaker"] == b], "clv_ind", rng) for b in EST_BOOKS},
             "per_day": {b: round(sum(1 for r in recent if r["bookmaker"] == b and fn(r)) / 7, 1)
                         for b in EST_BOOKS}}
        expl[k] = d
        a = d["all"]
        ci = f"[{a['lo'] * 100:+.1f},{a['hi'] * 100:+.1f}]" if a.get("n") else ""
        print(f"  {k:14} n{a.get('n', 0):4d} {pct(a.get('mean'))} {ci:>16}  disc {pct(d['disc'].get('mean'))} "
              f"hold {pct(d['hold'].get('mean'))}  ROI {pct(d['roi'])}  pinCLV {pct(d['clv_pin'])}")
        print("      " + "  ".join(f"{b[:7]} n{v.get('n', 0)} {pct(v.get('mean'))} "
                                  f"[{(v.get('lo') or 0) * 100:+.1f},{(v.get('hi') or 0) * 100:+.1f}]"
                                  for b, v in d["per_book"].items()) + f"  per day {d['per_day']}")
    (OUT / "exploratory.json").write_text(json.dumps(expl, indent=2, default=str))


if __name__ == "__main__":
    main()
