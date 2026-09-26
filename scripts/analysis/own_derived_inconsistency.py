#!/usr/bin/env python3
"""[[#191]] OWN research idea 2 — INTERNAL INCONSISTENCY in a book's derived markets.

PRE-REGISTERED 2026-09-27, before the first run. Nothing below was changed after seeing a result;
any change after the first run is appended as a dated AMENDMENT at the end of this docstring.

HYPOTHESIS. Kambi-style (Unibet-Site), Coolbet, Sportradar (Tonybet) and Epicbet boards derive side
markets by formula from their own 1X2 and O/U 2.5. When the main line moves, a derived market
sometimes lags or is mis-derived. A derived leg priced ABOVE the fair value implied by the same
book's own main line at the same fetch may be an OWN-only edge (🤖 OWN: it is only worth anything
at the book that made the error).

LITERATURE / STRUCTURE (CLAUDE.md "research before you train" — this is a price-consistency test,
not a model, but the derivation is a model and its shape matters):
  * DNB, DC, AH 0 and AH ±0.5 are EXACT functions of the 1X2 probabilities (no model needed):
    DNB = AH 0 = p_h/(p_h+p_a); DC 1X = p_h+p_d; AH home −0.5 = p_h; AH home +0.5 = p_h+p_d.
  * O/U lines other than 2.5, team totals and BTTS need a score model. Dixon & Coles (1997) —
    independent Poisson with a low-score dependence ρ — is the standard; with three unknowns
    (λh, λa, ρ) it is solved EXACTLY from three of the book's own numbers: P(H), P(D) (1X2) and
    P(over 2.5). λh−λa carries the 1X2 difference, λh+λa the total, ρ the draw excess (Karlis &
    Ntzoufras' point that difference and sum are separate information is why both inputs are needed).
    Poisson mis-specification (goal over-dispersion) will make some ladder lines look systematically
    off; that is WHY the judge below is an independent close and not this fit.
  * Expected result, stated in advance: NULL. Books derive side markets from the same engine and
    re-price them in the same sweep (verified: ≥ 94% of derived-market rows share a timestamp with
    the book's 1X2 row at Unibet-Site/Epicbet/Tonybet, 100% within 120 s incl. Coolbet); the
    derived margins are 4–8% (ANALYSIS_GOTCHAS §15: Coolbet DC sits 4–6% below fair, no tail).
    If anything fires it should be (a) DNB/DC/AH-0 right after a 1X2 move (true lag) or
    (b) ladder lines where Poisson is wrong (model artefact, should FAIL the independent judge).

DATA. odds_snapshots is full-resolution for ~7 days only (§59), so the universe is finished matches
kicked off in (now − 6.5 d, now − 150 min] with a score. Book set = ACCESSIBLE_BOOKMAKERS
(Coolbet, Unibet-Site, Tonybet, Epicbet). Tonybet data starts 2026-09-23.

DEFINITIONS.
  Fetch       per (book, match): every row of the book in [KO − 24 h, KO − 5 min], is_live not true,
              odds > 1.01, sorted by time and split into fetch clusters wherever two consecutive rows
              are > 90 s apart; within a cluster the last value per (market, line, selection) wins.
              Main line and derived leg MUST come from the same cluster (§62: complete sets from one
              fetch; never carry a leg forward from an earlier fetch).
  Own fair    1X2 = Shin de-vig (workers.model.devig.devig, §78) of the cluster's complete 1X2
              triple. O/U 2.5 = Shin of the complete pair. Dixon-Coles (λh, λa, ρ), ρ ∈ [−0.25, 0.25],
              scores 0..10, solved by least squares on (P(H), P(D), P(O2.5)); the fit is discarded if
              any residual > 0.003.
  Derived     group     markets (selections)                                         fair from
  legs        DNB       draw_no_bet (home, away)                                     1X2
              AH0       asian_handicap line 0 (home, away)                           1X2
              AH_HALF   asian_handicap line −0.5 / +0.5, home perspective (§53)     1X2
              DC        double_chance (1x, 12, x2)                                   1X2
              OU_LADDER over_under_05 / 15 / 35 / 45 (over, under) — .5 lines only  DC fit
              BTTS      btts (yes, no)                                               DC fit
              TT        team_total_{home,away}_{05,15,25} (over, under)             DC fit
  Edge_own    odds × p_fair_own − 1.
  Flag        edge_own ≥ X. PRIMARY X = 0.03; X = 0.01 and 0.05 reported as sensitivity, never in
              a verdict. First flagged cluster per (book, match, market, line, selection) = the pick
              (a bot bets once); pick price = that cluster's quote.
  Guards      live-implementable only (no future information): edge_own ≤ 0.25 (else a data fault,
              §9/§79 — counted and excluded); over-odds strictly rising 0.5 < 1.5 < 2.5 < 3.5 < 4.5
              over the lines present in the cluster (§80) for OU legs; a DC price must be shorter
              than each 1X2 leg it contains (§15) for DC legs.
  Lag type    a flag is "lag" when the book's previous cluster had the same leg at the same odds
              while its 1X2 triple changed — the mechanism the hypothesis names. Reported, not judged.

JUDGES (independent of the book and of the flag's own input).
  PRIMARY   ≥ 5-book consensus close of THE DERIVED MARKET ITSELF: workers.utils.anchor.compute_anchor
            on each other book's latest complete set in [KO − 60 min, KO], at = KO, max_age_min 60,
            min_books 5, own book excluded, Pinnacle rows removed before the call (§85: Pinnacle never
            enters the consensus input), Coolbet never a member (NEVER_IN_ANCHOR). DC is not a
            de-viggable set (legs overlap), so each book's DC triple is first mapped to its 1X2
            equivalent (h = (1x+12−x2)/2 etc. on implied probabilities), Shin-de-vigged via the same
            resolver, then summed back. Team totals have no ≥ 5-book close (only Pinnacle + our four
            books quote them) — TT is DESCRIPTIVE ONLY, outside the family.
            CLV_cons = pick_odds × p_close_cons − 1.
  SECONDARY Betfair-Exchange close: latest liquid capture (workers.utils.anchor.exchange_fair,
            spread ≤ 5%, ≥ 1,000 matched) in [KO − 30 min, KO]. 1X2 → DNB/AH0/AH_HALF/DC exactly;
            over_under_15/35 and btts direct; none for 0.5/4.5 lines or TT. Exchange data starts
            2026-09-24, so its n is small — corroboration, not a verdict.
  ROI       flat 1 unit at pick odds, settled on the final score (DNB/AH 0 push on a draw = 0).

UNCERTAINTY. Bootstrap over MATCHES (legs clustered by match), 10,000 resamples, seed 191; 95%
percentile CI; one-sided p = share of resampled means ≤ 0.

FAMILY + MULTIPLICITY.
  Cells     (book, group) for group ∈ {DNB, AH0, AH_HALF, DC, OU_LADDER, BTTS} — up to 24.
  Split     DISCOVERY = matches with int(md5(match_id)[0], 16) < 8; HOLDOUT = the rest. A split by
            match, not time — the whole window is 6.5 days and Tonybet exists only in its second half;
            the price paid is that both halves share market regime.
  Carry     a cell is carried to the holdout iff DISCOVERY n_judged ≥ 20 AND discovery mean
            CLV_cons > 0.
  Test      HOLDOUT, carried cells only, Holm at α = 0.05 on the one-sided p of mean CLV_cons.
  Verdict   "edge" = Holm-significant in the holdout with holdout n_judged ≥ 20 AND, where the
            exchange judged ≥ 10 of its legs, the exchange-judged mean not negative. Everything else
            is "no edge" (discovery mean ≤ 0 or n too small = "cannot tell / too thin", reported as such).
  Secondary Holm across all 24 cells on the FULL sample (cells with n_judged ≥ 30) — descriptive.

PLACEABILITY (reported per cell). Fire rate = flagged legs / distinct legs evaluated. Minutes to
kick-off at the flag. Persistence = among flags, share whose SAME book's next cluster (≤ 60 min
later) still quotes the leg at ≥ the pick odds, and share still flagged; plus whether the book has an
automated executor (best_price_router.PLACEABLE_BOOKS = Coolbet, Unibet-Site) or is manual-only.

Read-only. Writes CSV/JSON to data/models/_research/own191_derived/ (gitignored).

    PYTHONPATH=. python3 scripts/analysis/own_derived_inconsistency.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.automation.best_price_router import PLACEABLE_BOOKS  # noqa: E402
from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS  # noqa: E402
from workers.model.devig import devig  # noqa: E402
from workers.utils.anchor import compute_anchor, exchange_fair, sets_from_rows  # noqa: E402

OUT = ROOT / "data" / "models" / "_research" / "own191_derived"
BOOKS = sorted(ACCESSIBLE_BOOKMAKERS)
X_PRIMARY, X_SENS = 0.03, (0.01, 0.05)
N_BOOT, SEED = 10_000, 191
CLUSTER_GAP_S = 90
PICK_FROM_H, PICK_TO_MIN = 24, 5
CLOSE_WIN_MIN, EX_WIN_MIN = 60, 30
FIT_TOL = 0.003
EDGE_FAULT = 0.25
MIN_CARRY, MIN_FULL = 20, 30
FAMILY = ("DNB", "AH0", "AH_HALF", "DC", "OU_LADDER", "BTTS")
OU_LINES = {"over_under_05": 0.5, "over_under_15": 1.5, "over_under_25": 2.5,
            "over_under_35": 3.5, "over_under_45": 4.5}
TT_MARKETS = [f"team_total_{s}_{l}" for s in ("home", "away") for l in ("05", "15", "25")]
MARKETS = (["1x2", "draw_no_bet", "double_chance", "btts", "asian_handicap"]
           + list(OU_LINES) + TT_MARKETS)
AH_LINES = (-0.5, 0.0, 0.5)
EX_MARKETS = ("1x2", "over_under_15", "over_under_35", "btts")


# ── keys ────────────────────────────────────────────────────────────────────────────────────
def mkey(market: str, line) -> str | None:
    if market == "asian_handicap":
        if line is None or float(line) not in AH_LINES:
            return None
        return f"ah:{float(line) + 0.0:+.1f}"      # + 0.0 folds -0.0 into +0.0
    return market


def group_of(k: str) -> str:
    if k == "draw_no_bet":
        return "DNB"
    if k == "ah:+0.0":
        return "AH0"
    if k.startswith("ah:"):
        return "AH_HALF"
    if k == "double_chance":
        return "DC"
    if k in OU_LINES:
        return "OU_LADDER"
    if k == "btts":
        return "BTTS"
    return "TT"


def sides_of(k: str) -> tuple[str, ...]:
    if k == "1x2":
        return ("home", "draw", "away")
    if k == "double_chance":
        return ("1x", "12", "x2")
    if k == "draw_no_bet" or k.startswith("ah:"):
        return ("home", "away")
    if k == "btts":
        return ("yes", "no")
    return ("over", "under")


# ── Dixon-Coles ─────────────────────────────────────────────────────────────────────────────
_G = np.arange(11)
_LOGFACT = np.array([math.lgamma(i + 1) for i in _G])


def dc_grid(lh: float, la: float, rho: float) -> np.ndarray:
    ph = np.exp(_G * math.log(lh) - lh - _LOGFACT)
    pa = np.exp(_G * math.log(la) - la - _LOGFACT)
    m = np.outer(ph, pa)
    m[0, 0] *= 1 - lh * la * rho
    m[0, 1] *= 1 + lh * rho
    m[1, 0] *= 1 + la * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


_I, _J = np.meshgrid(_G, _G, indexing="ij")
_FIT_CACHE: dict = {}


def dc_fit(p_h: float, p_d: float, p_over25: float):
    key = (round(p_h, 4), round(p_d, 4), round(p_over25, 4))
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]

    def res(x):
        lh, la, rho = math.exp(x[0]), math.exp(x[1]), x[2]
        g = dc_grid(lh, la, rho)
        return [g[_I > _J].sum() - p_h, g[_I == _J].sum() - p_d, g[_I + _J > 2.5].sum() - p_over25]

    # starting point: total from O/U, split by the 1X2 difference
    tot = max(0.5, min(5.0, 2.6 + 3.0 * (p_over25 - 0.5)))
    diff = 1.6 * (p_h - (1 - p_h - p_d))
    x0 = [math.log(max(0.1, (tot + diff) / 2)), math.log(max(0.1, (tot - diff) / 2)), 0.0]
    try:
        r = least_squares(res, x0, bounds=([-3, -3, -0.25], [2.0, 2.0, 0.25]), xtol=1e-10, ftol=1e-12)
        out = None if max(abs(v) for v in r.fun) > FIT_TOL else dc_grid(math.exp(r.x[0]), math.exp(r.x[1]), r.x[2])
    except Exception:
        out = None
    _FIT_CACHE[key] = out
    return out


def fair_from_grid(g: np.ndarray, k: str) -> dict:
    if k in OU_LINES:
        o = g[_I + _J > OU_LINES[k]].sum()
        return {"over": o, "under": 1 - o}
    if k == "btts":
        y = g[(_I > 0) & (_J > 0)].sum()
        return {"yes": y, "no": 1 - y}
    side, line = k.split("_")[2], int(k.split("_")[3]) / 10
    o = g[(_I if side == "home" else _J) > line].sum()
    return {"over": o, "under": 1 - o}


def fair_from_1x2(p: dict, k: str) -> dict:
    h, d, a = p["home"], p["draw"], p["away"]
    if k in ("draw_no_bet", "ah:+0.0"):
        return {"home": h / (h + a), "away": a / (h + a)}
    if k == "ah:-0.5":
        return {"home": h, "away": d + a}
    if k == "ah:+0.5":
        return {"home": h + d, "away": a}
    if k == "double_chance":
        return {"1x": h + d, "12": h + a, "x2": d + a}
    raise KeyError(k)


def settle(k: str, sel: str, sh: int, sa: int) -> float | None:
    """Return per unit staked: 1 = win, 0 = loss, None = push (refund)."""
    if k in ("draw_no_bet", "ah:+0.0"):
        if sh == sa:
            return None
        return float((sh > sa) == (sel == "home"))
    if k == "ah:-0.5":
        return float(sh > sa) if sel == "home" else float(sh <= sa)
    if k == "ah:+0.5":
        return float(sh >= sa) if sel == "home" else float(sa > sh)
    if k == "double_chance":
        return float({"1x": sh >= sa, "12": sh != sa, "x2": sa >= sh}[sel])
    if k == "btts":
        return float((sh > 0 and sa > 0) == (sel == "yes"))
    if k in OU_LINES:
        return float((sh + sa > OU_LINES[k]) == (sel == "over"))
    side, line = k.split("_")[2], int(k.split("_")[3]) / 10
    g = sh if side == "home" else sa
    return float((g > line) == (sel == "over"))


# ── close judges ────────────────────────────────────────────────────────────────────────────
def dc_to_1x2_rows(rows: list[dict]) -> list[dict]:
    """Map each book-fetch DC triple to its 1X2-equivalent pseudo-odds (see docstring)."""
    by = defaultdict(dict)
    for r in rows:
        by[(r["bookmaker"], r["timestamp"])][r["sel"]] = r["odds"]
    out = []
    for (b, ts), q in by.items():
        if not all(s in q for s in ("1x", "12", "x2")):
            continue
        i1x, i12, ix2 = 1 / q["1x"], 1 / q["12"], 1 / q["x2"]
        h, d, a = (i1x + i12 - ix2) / 2, (i1x + ix2 - i12) / 2, (i12 + ix2 - i1x) / 2
        if min(h, d, a) <= 0.01:
            continue
        for s, v in (("home", h), ("draw", d), ("away", a)):
            out.append({"bookmaker": b, "sel": s, "odds": 1 / v, "timestamp": ts})
    return out


def cons_close(rows: list[dict], k: str, exclude: str, ko) -> tuple[dict | None, int]:
    if k == "double_chance":
        rows = dc_to_1x2_rows(rows)
        sides = ("home", "draw", "away")
    else:
        sides = sides_of(k)
    sets = sets_from_rows(rows, sides)
    a = compute_anchor(sets, sides, at=ko, exclude_book=exclude, max_age_min=CLOSE_WIN_MIN, min_books=5)
    if a.source != "consensus":
        return None, a.n_books
    p = dict(a.probs)
    if k == "double_chance":
        p = fair_from_1x2(p, k)
    return p, a.n_books


def ex_close(exq: dict, k: str) -> dict | None:
    """exq: market -> {sel: quote} (latest liquid capture)."""
    if k in ("draw_no_bet", "ah:+0.0", "ah:-0.5", "ah:+0.5", "double_chance"):
        q = exq.get("1x2")
        return fair_from_1x2(q, k) if q else None
    if k in ("over_under_15", "over_under_35", "btts"):
        return exq.get(k)
    return None


# ── data ────────────────────────────────────────────────────────────────────────────────────
def load_matches() -> pd.DataFrame:
    rows = execute_query("""
        SELECT id::text AS mid, date AS ko, score_home::int AS sh, score_away::int AS sa
          FROM matches
         WHERE status = 'finished' AND score_home IS NOT NULL AND score_away IS NOT NULL
           AND date > now() - interval '6.5 days' AND date <= now() - interval '150 minutes'""")
    return pd.DataFrame(rows)


def load_batch(mids: list[str]):
    tgt = execute_query("""
        SELECT o.match_id::text mid, o.bookmaker bk, o.market, o.handicap_line::float8 line,
               lower(o.selection) sel, o.odds::float8 od, o.timestamp ts
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = ANY(%s) AND o.market = ANY(%s)
           AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND (o.market <> 'asian_handicap' OR o.handicap_line IN (-0.5, 0, 0.5))
           AND o.timestamp >= m.date - make_interval(hours => %s)
           AND o.timestamp <= m.date - make_interval(mins => %s)
         ORDER BY o.timestamp""", (mids, BOOKS, MARKETS, PICK_FROM_H, PICK_TO_MIN)) or []
    close = execute_query("""
        SELECT o.match_id::text mid, o.bookmaker bk, o.market, o.handicap_line::float8 line,
               lower(o.selection) sel, o.odds::float8 od, o.timestamp ts
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s) AND o.bookmaker <> 'Pinnacle'
           AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND (o.market <> 'asian_handicap' OR o.handicap_line IN (-0.5, 0, 0.5))
           AND o.timestamp <= m.date AND o.timestamp >= m.date - make_interval(mins => %s)""",
                          (mids, [x for x in MARKETS if x != "1x2"], CLOSE_WIN_MIN)) or []
    ex = execute_query("""
        WITH last AS (
          SELECT DISTINCT ON (q.match_id, q.market) q.match_id, q.market, q.market_id, q.captured_at
            FROM exchange_quotes q JOIN matches m ON m.id = q.match_id
           WHERE q.match_id = ANY(%s::uuid[]) AND q.market = ANY(%s)
             AND q.captured_at <= m.date AND q.captured_at >= m.date - make_interval(mins => %s)
           ORDER BY q.match_id, q.market, q.captured_at DESC)
        SELECT q.match_id::text mid, q.market, lower(q.selection) sel, q.back::float8 back,
               q.lay::float8 lay, q.market_matched::float8 market_matched
          FROM exchange_quotes q JOIN last l
            ON q.market_id = l.market_id AND q.captured_at = l.captured_at AND q.match_id = l.match_id""",
                       (mids, list(EX_MARKETS), EX_WIN_MIN)) or []
    return tgt, close, ex


def clusters(rows: list[dict]) -> list[tuple]:
    """rows of one (book, match), time-sorted → [(ts, {key: {sel: odds}})]."""
    out, cur, t0, last_t = [], {}, None, None
    for r in rows:
        if last_t is not None and (r["ts"] - last_t).total_seconds() > CLUSTER_GAP_S:
            out.append((t0, cur))
            cur, t0 = {}, None
        k = mkey(r["market"], r["line"])
        if k is not None:
            cur.setdefault(k, {})[r["sel"]] = r["od"]
            t0 = r["ts"] if t0 is None else t0
        last_t = r["ts"]
    if cur:
        out.append((t0, cur))
    return out


def ladder_ok(board: dict) -> bool:
    ov = [(OU_LINES[k], board[k]["over"]) for k in OU_LINES if k in board and "over" in board[k]]
    ov.sort()
    return all(ov[i][1] < ov[i + 1][1] for i in range(len(ov) - 1))


def dc_ok(board: dict, sel: str) -> bool:
    x = board.get("1x2", {})
    need = {"1x": ("home", "draw"), "12": ("home", "away"), "x2": ("draw", "away")}[sel]
    return all(s in x for s in need) and all(board["double_chance"][sel] < x[s] for s in need)


# ── scan ────────────────────────────────────────────────────────────────────────────────────
def scan() -> tuple[pd.DataFrame, pd.DataFrame]:
    ms = load_matches()
    print(f"matches in window: {len(ms)}")
    meta = ms.set_index("mid").to_dict("index")
    mids = list(ms["mid"])
    picks, evaluated = [], defaultdict(set)
    for i in range(0, len(mids), 80):
        chunk = mids[i:i + 80]
        tgt, close, ex = load_batch(chunk)
        by_bm = defaultdict(list)
        for r in tgt:
            by_bm[(r["bk"], r["mid"])].append(r)
        close_by = defaultdict(list)
        for r in close:
            k = mkey(r["market"], r["line"])
            if k:
                close_by[(r["mid"], k)].append({"bookmaker": r["bk"], "sel": r["sel"],
                                                "odds": r["od"], "timestamp": r["ts"]})
        ex_raw = defaultdict(dict)
        for r in ex:
            ex_raw[(r["mid"], r["market"])][r["sel"]] = r
        exq = defaultdict(dict)
        for (mid, mk), q in ex_raw.items():
            probs, liquid, _ = exchange_fair(q, sides_of(mk))
            if probs and liquid:
                exq[mid][mk] = probs
        cons_cache = {}
        for (bk, mid), rows in by_bm.items():
            ko = meta[mid]["ko"]
            cl = clusters(rows)
            first = {}          # (k, sel, X) -> pick dict
            prev = None
            for ci, (ts, board) in enumerate(cl):
                x = board.get("1x2", {})
                if not all(s in x for s in ("home", "draw", "away")):
                    prev = board
                    continue
                p = devig([x["home"], x["draw"], x["away"]])
                if not p:
                    prev = board
                    continue
                p1 = dict(zip(("home", "draw", "away"), p))
                grid = None
                ou = board.get("over_under_25", {})
                if "over" in ou and "under" in ou:
                    po = devig([ou["over"], ou["under"]])
                    if po:
                        grid = dc_fit(p1["home"], p1["draw"], po[0])
                lad_ok = ladder_ok(board)
                for k, legs in board.items():
                    if k in ("1x2", "over_under_25"):
                        continue
                    g = group_of(k)
                    if g in ("OU_LADDER", "BTTS", "TT"):
                        if grid is None:
                            continue
                        fair = fair_from_grid(grid, k)
                    else:
                        fair = fair_from_1x2(p1, k)
                    for sel, od in legs.items():
                        if sel not in fair:
                            continue
                        evaluated[(bk, g)].add((mid, k, sel))
                        edge = od * fair[sel] - 1
                        if edge > EDGE_FAULT:
                            continue
                        if g == "OU_LADDER" and not lad_ok:
                            continue
                        if g == "DC" and not dc_ok(board, sel):
                            continue
                        for X in (X_PRIMARY,) + X_SENS:
                            if edge < X or (k, sel, X) in first:
                                continue
                            lag = bool(prev and prev.get(k, {}).get(sel) == od
                                       and prev.get("1x2") and prev["1x2"] != x)
                            nxt = None
                            for ts2, b2 in cl[ci + 1:]:
                                if (ts2 - ts).total_seconds() > 3600:
                                    break
                                if sel in b2.get(k, {}):
                                    nxt = b2
                                    break
                            n_od = nxt[k][sel] if nxt else None
                            first[(k, sel, X)] = dict(
                                book=bk, mid=mid, key=k, group=g, sel=sel, X=X, odds=od,
                                fair_own=fair[sel], edge_own=edge, ts=ts,
                                min_to_ko=(ko - ts).total_seconds() / 60, lag=lag,
                                next_seen=nxt is not None,
                                next_holds=(n_od is not None and n_od >= od - 1e-9))
                prev = board
            for pk in first.values():
                k, sel = pk["key"], pk["sel"]
                ck = (mid, k, bk)
                if ck not in cons_cache:
                    cons_cache[ck] = cons_close(close_by.get((mid, k), []), k, bk, ko)
                pc, nb = cons_cache[ck]
                pk["p_close_cons"] = pc.get(sel) if pc else None
                pk["cons_books"] = nb
                pe = ex_close(exq.get(mid, {}), k)
                pk["p_close_ex"] = pe.get(sel) if pe else None
                s = settle(k, sel, meta[mid]["sh"], meta[mid]["sa"])
                pk["pnl"] = 0.0 if s is None else (pk["odds"] - 1) * s - (1 - s)
                pk["split"] = "disc" if int(hashlib.md5(mid.encode()).hexdigest()[0], 16) < 8 else "hold"
                picks.append(pk)
        print(f"  {min(i + 80, len(mids))}/{len(mids)} matches, {len(picks)} flags", flush=True)
    df = pd.DataFrame(picks)
    if len(df):
        df["clv_cons"] = df["odds"] * df["p_close_cons"] - 1
        df["clv_ex"] = df["odds"] * df["p_close_ex"] - 1
    ev = pd.DataFrame([{"book": b, "group": g, "n_legs_evaluated": len(v)} for (b, g), v in evaluated.items()])
    return df, ev


# ── stats ───────────────────────────────────────────────────────────────────────────────────
def boot(d: pd.DataFrame, col: str, rng) -> dict:
    d = d[d[col].notna()]
    if d.empty:
        return {"n": 0}
    g = d.groupby("mid")[col].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    means = s[idx].sum(1) / c[idx].sum(1)
    return {"n": int(len(d)), "matches": int(len(g)), "mean": float(d[col].mean()),
            "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5)),
            "p": float((means <= 0).mean())}


def holm(ps: dict) -> dict:
    items = sorted(ps.items(), key=lambda kv: kv[1])
    m, out, stop = len(items), {}, False
    for i, (k, p) in enumerate(items):
        thr = 0.05 / (m - i)
        if stop or p > thr:
            stop = True
            out[k] = False
        else:
            out[k] = True
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df, ev = scan()
    df.to_csv(OUT / "flags.csv", index=False)
    ev.to_csv(OUT / "evaluated.csv", index=False)
    rng = np.random.default_rng(SEED)
    rows = []
    for X in (X_PRIMARY,) + X_SENS:
        dx = df[df["X"] == X]
        for (bk, g), d in dx.groupby(["book", "group"]):
            nev = ev[(ev.book == bk) & (ev.group == g)]["n_legs_evaluated"].sum()
            r = {"X": X, "book": bk, "group": g, "flags": len(d), "legs_evaluated": int(nev),
                 "fire_rate": len(d) / nev if nev else None,
                 "median_min_to_ko": float(d["min_to_ko"].median()),
                 "lag_share": float(d["lag"].mean()),
                 "next_seen": float(d["next_seen"].mean()),
                 "next_holds": float(d.loc[d["next_seen"], "next_holds"].mean()) if d["next_seen"].any() else None,
                 "automated_executor": bk in PLACEABLE_BOOKS,
                 "mean_edge_own": float(d["edge_own"].mean())}
            for split in ("all", "disc", "hold"):
                ds = d if split == "all" else d[d["split"] == split]
                for col in ("clv_cons", "clv_ex", "pnl"):
                    b = boot(ds, col, rng)
                    for kk, vv in b.items():
                        r[f"{split}_{col}_{kk}"] = vv
            rows.append(r)
    res = pd.DataFrame(rows)
    # pre-registered test
    prim = res[(res.X == X_PRIMARY) & (res.group.isin(FAMILY))].copy()
    carried = prim[(prim["disc_clv_cons_n"].fillna(0) >= MIN_CARRY) & (prim["disc_clv_cons_mean"] > 0)]
    hold_p = {(r.book, r.group): r.hold_clv_cons_p for r in carried.itertuples()
              if (r.hold_clv_cons_n or 0) > 0}
    hres = holm(hold_p) if hold_p else {}
    full_p = {(r.book, r.group): r.all_clv_cons_p for r in prim.itertuples()
              if (r.all_clv_cons_n or 0) >= MIN_FULL}
    fres = holm(full_p) if full_p else {}
    verdicts = []
    for r in prim.itertuples():
        c = (r.book, r.group)
        v = "too thin"
        if (r.disc_clv_cons_n or 0) >= MIN_CARRY:
            v = "no edge (discovery ≤ 0)" if not r.disc_clv_cons_mean > 0 else "no edge (holdout fails Holm)"
        if hres.get(c) and (r.hold_clv_cons_n or 0) >= MIN_CARRY and not (
                (r.all_clv_ex_n or 0) >= 10 and r.all_clv_ex_mean < 0):
            v = "EDGE"
        verdicts.append({"book": r.book, "group": r.group, "carried": c in hold_p,
                         "holdout_holm": hres.get(c), "full_holm": fres.get(c), "verdict": v})
    vd = pd.DataFrame(verdicts)
    res.to_csv(OUT / "cells.csv", index=False)
    vd.to_csv(OUT / "verdicts.csv", index=False)
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)
    show = ["X", "book", "group", "flags", "legs_evaluated", "fire_rate", "lag_share", "next_holds",
            "median_min_to_ko", "mean_edge_own", "all_clv_cons_n", "all_clv_cons_mean", "all_clv_cons_lo",
            "all_clv_cons_hi", "all_clv_ex_n", "all_clv_ex_mean", "all_pnl_n", "all_pnl_mean",
            "disc_clv_cons_n", "disc_clv_cons_mean", "hold_clv_cons_n", "hold_clv_cons_mean", "hold_clv_cons_p"]
    print(res[show].round(4).to_string())
    print(vd.to_string())
    json.dump({"n_flags": int(len(df)), "carried": [list(k) for k in hold_p],
               "holdout_holm": {f"{k[0]}|{k[1]}": v for k, v in hres.items()},
               "full_holm": {f"{k[0]}|{k[1]}": v for k, v in fres.items()}},
              open(OUT / "summary.json", "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
