#!/usr/bin/env python3
"""[[#121]] Phase 2 (i) — STALE WINDOWS, independent REPLICATION of scripts/stale_window_study.py (2026-09-24) with a scrape-level definition: when Pinnacle moves, do the books we can bet lag, and is the lagging
price worth anything against an INDEPENDENT close?

PRE-REGISTRATION (written 2026-09-26 before the first run; nothing below is tuned on its output).

  Data       finished matches, kickoff in the last 9 days (full-resolution Pinnacle history; older
             snapshots are pruned to open/close), markets 1x2 and over_under_25, pre-match quotes only.
  Pinnacle   complete sets (every side quoted within 2 min), de-vigged with devig.fair_prob
             (Shin 3-way, power 2-way). A MOVE on a selection = its fair probability rises by >= 3%
             relative between two consecutive Pinnacle sets (the selection got shorter: a lagging
             book now offers too long a price on it). Moves in the last 15 min before kickoff are ignored.
  Books      Coolbet, Unibet-Site, Epicbet, Tonybet (the Estonian books; our executors exist for the
             first two). We only SEE a book when we scrape it, so staleness is what we observed:
               STALE  = the book's FIRST quote after the move (within 90 min) shows the SAME price as its
                        last quote before the move;
               A leg  = a STALE quote whose EV against Pinnacle's NEW fair price is >= 3%
                        (odds x p_new - 1) — the fresh-move opportunity, available at that scrape.
               B leg  = CONTROL: the book's quote beats Pinnacle's CURRENT fair price by >= 3% with NO
                        Pinnacle move on that selection in the previous 90 min ("static generous" — what
                        the existing per-book sharp triggers mostly catch).
             One leg per (match, market, selection, book) per group, the first qualifying quote.
  Judge      INDEPENDENT close (not Pinnacle's, not the own book's — ANALYSIS_GOTCHAS §85): per other
             book (Pinnacle, the leg's book and non-offer feeds excluded) its latest complete pre-match set
             within 60 min of kickoff, de-vigged with fair_prob; median per selection, renormalised; needs
             >= 5 books. CLV_ind = odds x p_close - 1. Pinnacle-close CLV is printed for context only.
  Tests      per book: mean CLV_ind of A > 0 (one-sided, bootstrap over matches, 10,000, seed 121), Holm
             across the four books; and A - B > 0 per book (same bootstrap). n >= 30 A legs to judge.
  Expected   A > 0 for Coolbet / Unibet-Site (they lag most: 81% / 68% unmoved after a >= 3% Pinnacle
             move in the #121 audit), around +1 … +3%, and A > B; Epicbet smaller (it follows faster);
             Tonybet unknown. If A <= B the fresh-move condition adds nothing over the existing triggers.
  Also       share of Pinnacle moves where each book was stale at its first post-move scrape (replication of
             the audit's 81 / 68 / 43%), and the median time until the book's price changed.

Read-only. Writes data/models/_research/stale121/{legs.csv,summary.json} (gitignored).

    python3 scripts/analysis/stale_window_replication.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.model.devig import fair_prob  # noqa: E402

DAYS = 9
MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Tonybet")
NOT_ANCHOR = {"Pinnacle", "Max", "Avg", "Betfair Exchange", "BetWin", "Betfred", "Unibet", "Unibet-Kambi",
              "Coolbet-OddsAPI"}
MOVE_REL = 0.03
EV_MIN = 0.03
LOOK_MIN = 90
KO_QUIET_MIN = 15
SET_TOL_S = 120
CLOSE_MIN = 60
CONS_MIN_BOOKS = 5
N_MIN = 30
B = 10_000
SEED = 121
CHUNK = 250


def _sets(quotes: list, sides: tuple) -> list:
    """quotes: [(ts, sel, odds)] ascending → [(ts, [odds per side])] complete sets within SET_TOL_S."""
    out, last = [], {}
    for ts, sel, o in quotes:
        last[sel] = (ts, o)
        if all(s in last for s in sides):
            t0 = min(last[s][0] for s in sides)
            if (ts - t0).total_seconds() <= SET_TOL_S:
                out.append((ts, [last[s][1] for s in sides]))
    return out


def _fair_sets(sets: list) -> list:
    out = []
    for ts, odds in sets:
        p = fair_prob(odds)
        if p:
            out.append((ts, p))
    return out


def load_chunk(ids: list) -> list:
    from workers.api_clients.db import execute_query
    return execute_query(
        """SELECT o.match_id::text AS mid, o.bookmaker AS bk, o.market AS mk, o.selection AS sel,
                  o.odds::float AS odds, o."timestamp" AS ts, m.date AS ko,
                  CASE WHEN m.score_home > m.score_away THEN 'home' WHEN m.score_home = m.score_away
                       THEN 'draw' ELSE 'away' END AS res, (m.score_home + m.score_away) AS goals
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s) AND o.is_live IS NOT TRUE
              AND o.odds > 1.01 AND o."timestamp" < m.date
            ORDER BY o."timestamp" """, (ids, list(MARKETS))) or []


def independent_close(by_book: dict, sides: tuple, ko, exclude: str) -> list | None:
    probs = []
    for bk, quotes in by_book.items():
        if bk in NOT_ANCHOR or bk == exclude:
            continue
        sets = [s for s in _sets(quotes, sides) if (ko - s[0]).total_seconds() <= CLOSE_MIN * 60]
        if not sets:
            continue
        p = fair_prob(sets[-1][1])
        if p:
            probs.append(p)
    if len(probs) < CONS_MIN_BOOKS:
        return None
    med = [median(p[i] for p in probs) for i in range(len(sides))]
    s = sum(med)
    return [x / s for x in med]


def analyse_market(rows: list, mk: str, legs: list, move_stats: dict) -> None:
    sides = MARKETS[mk]
    q = defaultdict(lambda: defaultdict(list))          # (mid) -> bk -> [(ts, sel, odds)]
    ko = {}
    for r in rows:
        if r["mk"] != mk:
            continue
        q[r["mid"]][r["bk"]].append((r["ts"], r["sel"], r["odds"]))
        ko[r["mid"]] = r["ko"]
    for mid, by_book in q.items():
        pin = _fair_sets(_sets(by_book.get("Pinnacle", []), sides))
        if len(pin) < 2:
            continue
        k = ko[mid]
        moves = []                                    # (t, side_idx, p_new)
        for (t0, p0), (t1, p1) in zip(pin, pin[1:]):
            if (k - t1).total_seconds() < KO_QUIET_MIN * 60:
                continue
            for i in range(len(sides)):
                if p0[i] > 0 and p1[i] / p0[i] - 1 >= MOVE_REL:
                    moves.append((t1, i, p1[i]))
        pin_close = pin[-1][1] if (k - pin[-1][0]).total_seconds() <= CLOSE_MIN * 60 else None
        for bk in BOOKS:
            quotes = by_book.get(bk) or []
            if not quotes:
                continue
            per_side = defaultdict(list)
            for ts, sel, o in quotes:
                per_side[sel].append((ts, o))
            close = independent_close(by_book, sides, k, bk)
            taken = set()
            # A: stale after a move
            for t, i, p_new in moves:
                sel = sides[i]
                ser = per_side.get(sel) or []
                before = [x for x in ser if x[0] <= t]
                after = [x for x in ser if t < x[0] <= t + timedelta(minutes=LOOK_MIN)]
                if not before or not after:
                    continue
                stale = abs(after[0][1] - before[-1][1]) < 1e-9
                move_stats[(bk, mk)]["moves_seen"] += 1
                move_stats[(bk, mk)]["stale"] += int(stale)
                if stale:
                    changed = [x for x in ser if x[0] > t and abs(x[1] - before[-1][1]) > 1e-9]
                    if changed:
                        move_stats[(bk, mk)]["lag_min"].append((changed[0][0] - t).total_seconds() / 60)
                ev = after[0][1] * p_new - 1
                if stale and ev >= EV_MIN and ("A", sel) not in taken:
                    taken.add(("A", sel))
                    legs.append(_leg("A", mid, mk, sel, bk, after[0][1], after[0][0], ev, close, pin_close, sides,
                                     rows_res(rows, mid)))
            # B: static generous (no move on that selection in the previous LOOK_MIN)
            for sel in sides:
                i = sides.index(sel)
                for ts, o in per_side.get(sel) or []:
                    if ("B", sel) in taken or (k - ts).total_seconds() < KO_QUIET_MIN * 60:
                        continue
                    cur = [p for (tp, p) in pin if tp <= ts]
                    if not cur:
                        continue
                    if any(t - timedelta(minutes=LOOK_MIN) <= ts and t <= ts and j == i for t, j, _ in moves):
                        continue
                    ev = o * cur[-1][i] - 1
                    if ev >= EV_MIN:
                        taken.add(("B", sel))
                        legs.append(_leg("B", mid, mk, sel, bk, o, ts, ev, close, pin_close, sides, rows_res(rows, mid)))


_RES_CACHE: dict = {}


def rows_res(rows, mid):
    if mid not in _RES_CACHE:
        r = next(x for x in rows if x["mid"] == mid)
        _RES_CACHE[mid] = (r["res"], r["goals"])
    return _RES_CACHE[mid]


def _leg(group, mid, mk, sel, bk, odds, ts, ev, close, pin_close, sides, res):
    i = sides.index(sel)
    won = (res[0] == sel) if mk == "1x2" else ((res[1] > 2.5) == (sel == "over"))
    return {"group": group, "mid": mid, "market": mk, "selection": sel, "book": bk, "odds": odds, "ts": ts,
            "ev_at_pick": ev,
            "clv_ind": odds * close[i] - 1 if close else None,
            "clv_pin": odds * pin_close[i] - 1 if pin_close else None,
            "pnl": (odds - 1) if won else -1.0}


def boot(vals: dict, rng):
    keys = list(vals)
    sums = np.array([sum(vals[k]) for k in keys]); cnts = np.array([len(vals[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(sums.sum() / cnts.sum()), means


def main() -> None:
    from workers.api_clients.db import execute_query
    ids = [r["id"] for r in execute_query(
        """SELECT m.id::text AS id FROM matches m
            WHERE m.status = 'finished' AND m.score_home IS NOT NULL
              AND m.date >= now() - make_interval(days => %s)
              AND EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.match_id = m.id AND o.bookmaker = 'Pinnacle'
                          AND o.market = '1x2')""", (DAYS,)) or []]
    legs: list = []
    move_stats = defaultdict(lambda: {"moves_seen": 0, "stale": 0, "lag_min": []})
    for c in range(0, len(ids), CHUNK):
        rows = load_chunk(ids[c:c + CHUNK])
        for mk in MARKETS:
            analyse_market(rows, mk, legs, move_stats)
        _RES_CACHE.clear()
        print(f"  {min(c + CHUNK, len(ids))}/{len(ids)} matches, {len(legs)} legs", flush=True)

    print("\nSTALENESS after a >= 3% Pinnacle move (first scrape within 90 min):")
    for (bk, mk), s in sorted(move_stats.items()):
        lag = f"median lag {median(s['lag_min']):.0f} min" if s["lag_min"] else ""
        print(f"  {bk:12} {mk:14} moves {s['moves_seen']:5d}  stale {s['stale'] / max(1, s['moves_seen']):5.0%}  {lag}")

    rng = np.random.default_rng(SEED)
    res, pvals = {}, {}
    print("\nCLV against the INDEPENDENT close (ex-Pinnacle, ex-own-book consensus):")
    for bk in BOOKS:
        for g in ("A", "B"):
            vals = defaultdict(list)
            pin = []
            pnl = []
            for L in legs:
                if L["book"] == bk and L["group"] == g and L["clv_ind"] is not None and abs(L["clv_ind"]) <= 1:
                    vals[L["mid"]].append(L["clv_ind"]); pnl.append(L["pnl"])
                    if L["clv_pin"] is not None:
                        pin.append(L["clv_pin"])
            n = sum(len(v) for v in vals.values())
            if not n:
                continue
            m, means = boot(vals, rng)
            res[(bk, g)] = (m, means, vals)
            lo, hi = np.quantile(means, [0.025, 0.975])
            p = float((means <= 0).mean())
            if g == "A" and n >= N_MIN:
                pvals[bk] = p
            print(f"  {bk:12} {g}  n {n:4d}  CLV_ind {m * 100:+6.2f}% [{lo * 100:+.2f}, {hi * 100:+.2f}]  "
                  f"p(<=0) {p:.3f}  | Pinnacle-close CLV {np.mean(pin) * 100 if pin else float('nan'):+.2f}%  "
                  f"flat ROI {np.mean(pnl) * 100:+.1f}%")
    order = sorted(pvals, key=pvals.get)
    holm, run = {}, 0.0
    for j, bk in enumerate(order):
        run = max(run, min(1.0, (len(order) - j) * pvals[bk])); holm[bk] = run
    print("\nVERDICTS (A > 0, Holm across books with n >= 30; A - B > 0):")
    summary = {}
    for bk in BOOKS:
        a, b = res.get((bk, "A")), res.get((bk, "B"))
        line = {"n_A": sum(len(v) for v in a[2].values()) if a else 0}
        if a:
            line["clv_A"] = a[0]
        if bk in holm:
            line["holm_p"] = holm[bk]
            line["verdict"] = "stale edge" if holm[bk] < 0.05 and a[0] > 0 else "undetermined"
        else:
            line["verdict"] = "too few"
        if a and b:
            d = a[1] - b[1]
            line["A_minus_B"] = a[0] - b[0]
            line["p_A_le_B"] = float((d <= 0).mean())
        summary[bk] = line
        print(f"  {bk:12} {line}")
    out = ROOT / "data/models/_research/stale121"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "legs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(legs[0].keys()) if legs else ["group"])
        w.writeheader(); w.writerows(legs)
    (out / "summary.json").write_text(json.dumps(
        {"verdicts": summary, "staleness": {f"{k[0]}|{k[1]}": {"moves": v["moves_seen"], "stale": v["stale"],
                                                                 "median_lag_min": median(v["lag_min"]) if v["lag_min"] else None}
                                            for k, v in move_stats.items()}}, indent=2, default=str))


if __name__ == "__main__":
    main()
