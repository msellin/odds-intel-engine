#!/usr/bin/env python3
"""[[#186]] LOCAL-BOOKS-LEAD-SIGNAL — do the Estonian books lead the global prices on some leagues?

READ-ONLY research. Nothing in production reads or is changed by this script.

MOTIVATION (2026-09-26, EBK v GrIFK, Finnish Kakkonen). Coolbet, Epicbet and Unibet-Site moved EBK
home from ~2.0 to 1.33 between 12:30 and 15:30 UTC while API-Football's Pinnacle sat at 1.91-1.92 and
Bet365 at 1.95 all day. #182 answered it defensively (own_bet_board.market_split: no price when our
books' line is > 8 pts from the anchor). Owner hypothesis: on small local leagues the Estonian books
are better informed; when they move together and the global price has not moved, the GLOBAL price is
value (👥 PICKS — Telegram readers bet at global books). Second angle (🤖 OWN): the Estonian book that
LAGS the other local books may be value against the local consensus.

PRE-REGISTRATION — written 2026-09-26 BEFORE the first run; nothing below is tuned on output.

DATA
  Matches     finished, result known, kickoff 2026-05-20 .. 2026-09-25 (UTC), market 1x2.
              In practice Coolbet 1x2 coverage starts in August (May/June: < 20 matches).
  LOCAL books Coolbet, Epicbet, Unibet-Site, Tonybet (Optibet: 18 rows total, ignored).
  GLOBAL books every other book in odds_snapshots except anchor.NEVER_IN_ANCHOR (aggregates,
              retired/phantom feeds).
  Retention   odds_snapshots older than ~7 days keeps only is_opening, is_closing (|mtk| <= 15) and
              the latest pre-KO row per series (ANALYSIS_GOTCHAS §59; verified 2026-09-26: Pinnacle
              / Bet365 ~20 rows per series at 8 days, ~6 at 14+ days; Coolbet / Epicbet 1 row per
              series at 30 days). So the primary test is AT KICK-OFF.
  Sets        one COMPLETE set per book from one fetch (anchor.sets_from_rows, §62/§65), last one
              at or before kickoff, is_live false, never a post-KO row.

DEFINITIONS
  LOCAL line  each local book's last pre-KO complete 1x2 set no older than 180 min before KO,
              Shin-de-vigged (devig.fair_prob); members within 120 min of the newest local member
              (§65 common window); >= 2 books; per-side MEDIAN of the members, renormalised to 1.
  GLOBAL line de-vigged Pinnacle close (last complete set <= 60 min before KO, any overround), else
              anchor.compute_anchor over non-local, non-Pinnacle books at KO with min_books = 5
              ('consensus'); else the match is dropped. The source is carried on every row.
  DATA-FAULT GUARDS (§67: a divergence signal SEARCHES for data faults; §79: Epicbet / Unibet-Site
              stored other matches' boards before 2026-09-24). A local book is dropped from a match
              when ANY of:
                * (match, book) has a data_quality_findings row (wrong_fixture_board, mirrored_1x2,
                  single_market_off, swapped_two_way);
                * any of its 1x2 legs is > GUARD_RATIO (1.5625) from the GLOBAL fair odds of that
                  leg (the anchor's inversion guard);
                * its O/U 2.5 fair over-prob differs from the global O/U 2.5 fair (Pinnacle, else a
                  >= 3-book consensus) by > 0.15 — a whole-board mismatch, not an opinion on 1x2.
  SIGNAL(G)   s* = argmax_s (p_local_s - p_global_s); fires when that gap >= G AND every local
              member individually has (p_book_s* - p_global_s*) >= G/2 ("they moved TOGETHER"; one
              mis-mapped book cannot fire it alone). Side to bet = s* (the side the locals made
              SHORTER). One signal per match. Grid G in {0.05, 0.08, 0.12}.
  LIVENESS    (owner: "are the global odds executable, or just frozen in our feed?"). Our AF poll
              writes a row every fetch whether or not the price changed, so a row timestamp is OUR
              fetch time, not the book's; AF's own `update` time is not stored. Liveness is therefore
              read from the price path: for a global book, history is AVAILABLE when it has >= 3
              pre-KO rows in [KO-6h, KO] and the earliest is <= KO-3h (i.e. the series is not
              pruned). LIVE = any 1x2 leg changed value in [KO-6h, KO]; FROZEN = history available
              and no leg changed; UNKNOWN = pruned. Reported for Pinnacle and for the book that
              supplies the best global price. A weaker proxy for pruned rows (Pinnacle open != close)
              is reported descriptively.

TESTS — the pre-registered family (Holm, alpha 0.05, 15 tests = 5 x 3 values of G)
  (a) OUTCOME  mean over signal matches of LL_global - LL_local (LL = -ln p(outcome)); > 0 means the
               local line predicted better. Paired by match.
  (b1) PICKS   flat 1-unit ROI backing s* at the BEST global close price: max over non-local
               publishable books' last complete pre-KO set no older than 60 min (a book whose leg is
               > 1.5625x from the median of the other books is dropped).
  (b2) PICKS   the same at Pinnacle's own close price (Pinnacle-anchored matches only).
  (c) MOVE     on Pinnacle-anchored signal matches with a Pinnacle opening set >= 6 h before KO:
               mean (p_pin_close_s* - p_pin_open_s*). > 0 = Pinnacle moved toward the locals.
  (d) OWN      matches with >= 3 local books after guards. For each local book b, LOO = per-side
               median of the OTHER locals (>= 2), renormalised; lag_b = max_s (p_LOO_s - p_b_s).
               Fires when lag_b >= G and every other local has (p_other_s - p_b_s) >= G/2. One bet
               per match (the book with the largest lag), flat 1 unit at b's own raw price.
  Uncertainty  bootstrap over matches, 10,000 resamples, seed 186; two-sided p = 2 x min(share of
               resampled means <= 0, share >= 0). ROI cells with < 2 losses are flagged (§66).
  Verdict      a cell is judged only with n >= 30; otherwise 'too few' (still counted in Holm with
               p = 1). 'Positive' = Holm-adjusted p < 0.05 and mean > 0; 'negative' = adjusted p <
               0.05 and mean < 0; else 'undetermined'.

DESCRIPTIVE, OUTSIDE THE FAMILY
  * signal frequency per G, per global-anchor source, per local book set;
  * the same (a)/(b) split by Pinnacle liveness (LIVE / FROZEN / UNKNOWN) and by best-price-book
    liveness; by country group (Nordic/Baltic = Finland, Sweden, Norway, Denmark, Iceland, Estonia,
    Latvia, Lithuania, Faroe-Islands vs rest); by leagues.tier (§65: tier is a weak proxy);
  * (a) on ALL matches (not only signal matches) as the base rate;
  * EV of the (d) bets against the Pinnacle close fair (independent of the local consensus, §85).

EXPECTED RESULT (stated before the run)
  * Frequency: G=0.05 fires on a few % of matches, G=0.12 on well under 1%, concentrated in small
    leagues and on stale local lines (Coolbet's last pre-KO quote is median ~105 min old in Sept).
  * (a): the Pinnacle close is the sharpest public line and local soft books carry 6-12% margins;
    expected LL_global - LL_local <= 0 overall, i.e. no local advantage, and not significant on the
    signal subset. A stale local line vs a fresh global close biases AGAINST the hypothesis.
  * (b): ROI at the global close ~ -(global margin), roughly -2..-5%, CI wide and spanning 0 at the
    larger G; no cell positive after Holm.
  * (c): mean movement ~0 (Pinnacle does not follow the local books).
  * (d): ROI ~ -(local margin), negative or undetermined.
  A true positive would look like: (a) > 0 AND (b) > 0 on LIVE global quotes, concentrated in
  Nordic/Baltic leagues. Anything that holds only on FROZEN global quotes is not bettable.

AMENDMENT 1 (2026-09-26, AFTER the first run — post-hoc diagnostics, NOT in the family). The first run
put every family cell at 'undetermined' after Holm but with positive point estimates (G=0.05: ROI +18%).
Added, descriptive only: timing of the local vs Pinnacle quotes; whether AF-Pinnacle's VALUE changed in
the last 60 min (and minutes since its last value change); Pinnacle at the locals' own instant; month;
youth/reserve competitions; which book supplies the best price. Finding: AF-Pinnacle's last value change
is a median 120 min before KO on ALL matches (changes in the last hour on only 14%), i.e. AF's /odds
refreshes about every 2 h (docs/AF_ENDPOINT_FREQUENCY.md) and its "close" is a ~2 h old quote, while
the local scrapes are a median 13 min old.
AMENDMENT 2 (same day, post-hoc, NOT in the family). The decisive liveness check the pre-registration
could not do on pruned history: Betfair Exchange (own reader, every 15 min, since 2026-09-24) is a live
global price outside API-Football. At the last capture <= 30 min pre-KO, on the locals' favoured side,
does the exchange sit with the locals or with AF-Pinnacle? (It sits with the locals — see findings.)
Findings: dev/active/local-lead-signal-findings.md.

Run:  PYTHONPATH=. python3 scripts/analysis/local_lead_signal.py [--dump out.json]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import timedelta
from statistics import median

from workers.api_clients.db import execute_query
from workers.model.devig import fair_prob
from workers.utils.anchor import GUARD_RATIO, NEVER_IN_ANCHOR, PIN, compute_anchor, sets_from_rows

LOCAL = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet")
IGNORE = {"Optibet"}
SIDES = ("home", "draw", "away")
OU = ("over", "under")
G_GRID = (0.05, 0.08, 0.12)
START, END = "2026-05-20", "2026-09-26"
LOCAL_MAX_AGE = 180
LOCAL_WINDOW = 120
PIN_MAX_AGE = 60
PRICE_MAX_AGE = 60
OU_BOARD_MAX = 0.15
MIN_N = 30
B = 10_000
NORDIC = {"Finland", "Sweden", "Norway", "Denmark", "Iceland", "Estonia", "Latvia", "Lithuania",
          "Faroe-Islands", "Faroe Islands"}
DQ_CHECKS = ("wrong_fixture_board", "mirrored_1x2", "single_market_off", "swapped_two_way")


# ── loading ──────────────────────────────────────────────────────────────────
def load():
    matches = execute_query(
        """WITH cand AS (
             SELECT o.match_id FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
              WHERE o.market = '1x2' AND o.bookmaker = ANY(%s) AND COALESCE(o.is_live,false) = false
                AND o.timestamp <= m.date AND o.timestamp >= m.date - interval '180 minutes'
                AND m.status = 'finished' AND m.result IS NOT NULL
                AND m.date >= %s AND m.date < %s
              GROUP BY 1 HAVING count(DISTINCT o.bookmaker) >= 2)
           SELECT m.id::text AS id, m.date AS ko, m.result, l.country, l.tier, l.name AS league
             FROM cand c JOIN matches m ON m.id = c.match_id LEFT JOIN leagues l ON l.id = m.league_id""",
        (list(LOCAL), START, END)) or []
    ids = [m["id"] for m in matches]
    rows = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        rows += execute_query(
            """SELECT o.match_id::text AS mid, o.bookmaker, o.market, lower(o.selection) AS sel,
                      o.odds::float AS odds, o.timestamp, COALESCE(o.is_opening,false) AS is_opening
                 FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                WHERE o.match_id = ANY(%s::uuid[]) AND o.market IN ('1x2','over_under_25')
                  AND COALESCE(o.is_live,false) = false AND o.odds > 1.01 AND o.timestamp <= m.date
                  AND (o.timestamp >= m.date - interval '6 hours' OR o.is_opening)""",
            (chunk,)) or []
    dq = execute_query(
        "SELECT match_id::text AS mid, bookmaker FROM data_quality_findings WHERE check_name = ANY(%s)",
        (list(DQ_CHECKS),)) or []
    return matches, rows, {(d["mid"], d["bookmaker"]) for d in dq}


# ── per-match construction ───────────────────────────────────────────────────
def age_min(ko, ts):
    return (ko - ts).total_seconds() / 60.0


def renorm(p):
    s = sum(p)
    return [x / s for x in p]


def liveness(rows_1x2_book, ko):
    """LIVE / FROZEN / UNKNOWN from one book's pre-KO 1x2 rows in [KO-6h, KO]."""
    w = [r for r in rows_1x2_book if r["timestamp"] >= ko - timedelta(hours=6)]
    if len(w) < 3 or min(r["timestamp"] for r in w) > ko - timedelta(hours=3):
        return "UNKNOWN"
    by_sel = defaultdict(set)
    for r in w:
        by_sel[r["sel"]].add(round(r["odds"], 3))
    return "LIVE" if any(len(v) > 1 for v in by_sel.values()) else "FROZEN"


def build(m, rows, dq):
    ko = m["ko"]
    r1 = [r for r in rows if r["market"] == "1x2" and r["sel"] in SIDES]
    rou = [r for r in rows if r["market"] == "over_under_25" and r["sel"] in OU]
    sets = {b: v for b, v in sets_from_rows(r1, SIDES).items() if b not in IGNORE}
    ou_sets = sets_from_rows(rou, OU)
    out = {"id": m["id"], "result": m["result"], "country": m["country"], "tier": m["tier"],
           "league": m["league"]}

    # GLOBAL
    pin = sets.get(PIN)
    glob = None
    if pin and age_min(ko, pin[1]) <= PIN_MAX_AGE:
        fp = fair_prob(pin[0])
        if fp:
            glob, src = fp, "pinnacle"
    if glob is None:
        nonlocal_sets = {b: v for b, v in sets.items() if b not in LOCAL and b != PIN}
        a = compute_anchor(nonlocal_sets, SIDES, at=ko, min_books=5)
        if a.source == "consensus":
            glob, src = [a.probs[s] for s in SIDES], "consensus"
    if glob is None:
        return None
    out["src"] = src
    out["glob"] = glob

    # global O/U 2.5 for the board check
    g_ou = None
    if ou_sets.get(PIN) and age_min(ko, ou_sets[PIN][1]) <= PIN_MAX_AGE:
        f = fair_prob(ou_sets[PIN][0])
        g_ou = f[0] if f else None
    if g_ou is None:
        a = compute_anchor({b: v for b, v in ou_sets.items() if b not in LOCAL and b != PIN},
                           OU, at=ko, min_books=5, min_thin_books=3)
        if a.ok and a.source != "none":
            g_ou = a.probs["over"]

    # LOCAL members, with guards
    members, dropped = {}, {}
    for b in LOCAL:
        if b not in sets:
            continue
        q, ts = sets[b]
        if age_min(ko, ts) > LOCAL_MAX_AGE:
            continue
        if (m["id"], b) in dq:
            dropped[b] = "dq"
            continue
        gfair = [1.0 / p for p in glob]
        if any(max(q[i] / gfair[i], gfair[i] / q[i]) > GUARD_RATIO for i in range(3)):
            dropped[b] = "guard"
            continue
        if g_ou is not None and b in ou_sets and age_min(ko, ou_sets[b][1]) <= LOCAL_MAX_AGE:
            f = fair_prob(ou_sets[b][0])
            if f and abs(f[0] - g_ou) > OU_BOARD_MAX:
                dropped[b] = "ou_board"
                continue
        fp = fair_prob(q)
        if fp:
            members[b] = (fp, ts, q)
    if members:
        newest = max(v[1] for v in members.values())
        members = {b: v for b, v in members.items()
                   if (newest - v[1]).total_seconds() / 60 <= LOCAL_WINDOW}
    out["dropped"] = dropped
    out["n_local"] = len(members)
    out["local_books"] = sorted(members)
    if len(members) < 2:
        return out
    loc = renorm([median(v[0][i] for v in members.values()) for i in range(3)])
    out["loc"] = loc
    out["local_age"] = max(age_min(ko, v[1]) for v in members.values())
    gaps = [loc[i] - glob[i] for i in range(3)]
    si = max(range(3), key=lambda i: gaps[i])
    out["side"] = SIDES[si]
    out["gap"] = gaps[si]
    out["min_member_gap"] = min(v[0][si] - glob[si] for v in members.values())

    # best global price for s*, and Pinnacle's own
    cands = {b: v[0] for b, v in sets.items()
             if b not in LOCAL and b not in NEVER_IN_ANCHOR and age_min(ko, v[1]) <= PRICE_MAX_AGE}
    best_b, best = None, None
    for b, q in cands.items():
        others = [v[si] for o, v in cands.items() if o != b]
        if len(others) >= 2:
            med = median(others)
            if max(q[si] / med, med / q[si]) > GUARD_RATIO:
                continue
        if best is None or q[si] > best:
            best_b, best = b, q[si]
    out["best_book"], out["best_odds"] = best_b, best
    out["pin_odds"] = pin[0][si] if (pin and src == "pinnacle") else None

    # liveness
    by_book = defaultdict(list)
    for r in r1:
        by_book[r["bookmaker"]].append(r)
    out["pin_live"] = liveness(by_book.get(PIN, []), ko) if src == "pinnacle" else "N/A"
    # POST-HOC diagnostics (AMENDMENT 1) — timing and same-instant comparison
    newest_local = max(v[1] for v in members.values())
    out["local_newest_age"] = age_min(ko, newest_local)
    out["pin_age"] = age_min(ko, pin[1]) if (pin and src == "pinnacle") else None
    out["month"] = ko.strftime("%Y-%m")
    pr = [r for r in by_book.get(PIN, []) if r["timestamp"] >= ko - timedelta(minutes=60)]
    out["pin_changed_60"] = (None if len(pr) < 4 else
                             any(len({round(r["odds"], 3) for r in pr if r["sel"] == x}) > 1 for x in SIDES))
    out["pin_at_local"] = None
    # effective Pinnacle quote age: minutes before KO of its last VALUE change (unpruned series only)
    out["pin_last_change"] = None
    if out["pin_live"] in ("LIVE", "FROZEN"):
        seq = sorted(by_book[PIN], key=lambda r: r["timestamp"])
        last, lc = {}, None
        for r in seq:
            if r["sel"] in last and abs(last[r["sel"]] - r["odds"]) > 1e-9:
                lc = r["timestamp"]
            last[r["sel"]] = r["odds"]
        out["pin_last_change"] = age_min(ko, lc) if lc else 999.0
    if src == "pinnacle":
        ps = sets_from_rows([r for r in by_book.get(PIN, []) if r["timestamp"] <= newest_local], SIDES).get(PIN)
        if ps and (newest_local - ps[1]).total_seconds() / 60 <= 60:
            f = fair_prob(ps[0])
            out["pin_at_local"] = f[si] if f else None
    out["p_loc_side"], out["p_glob_side"] = loc[si], glob[si]
    out["best_live"] = liveness(by_book.get(best_b, []), ko) if best_b else "N/A"
    # weak proxy on pruned rows: did Pinnacle's s* price move open -> close?
    pin_open = sets_from_rows([r for r in by_book.get(PIN, []) if r["is_opening"]], SIDES).get(PIN)
    out["pin_open_age_h"] = age_min(ko, pin_open[1]) / 60 if pin_open else None
    if pin_open and src == "pinnacle":
        po = fair_prob(pin_open[0])
        out["pin_move"] = (glob[si] - po[si]) if po else None
        out["pin_open_ne_close"] = any(abs(a - b) > 1e-9 for a, b in zip(pin_open[0], pin[0]))
    else:
        out["pin_move"], out["pin_open_ne_close"] = None, None

    # (d) OWN — lagging local book vs the other locals
    out["own"] = None
    if len(members) >= 3:
        bestlag = None
        for b, (pb, _, qb) in members.items():
            others = {o: v for o, v in members.items() if o != b}
            loo = renorm([median(v[0][i] for v in others.values()) for i in range(3)])
            lg = [loo[i] - pb[i] for i in range(3)]
            j = max(range(3), key=lambda i: lg[i])
            mo = min(v[0][j] - pb[j] for v in others.values())
            if bestlag is None or lg[j] > bestlag["lag"]:
                bestlag = {"book": b, "lag": lg[j], "min_other": mo, "side": SIDES[j],
                           "odds": qb[j], "pin_fair": glob[j] if src == "pinnacle" else None}
        out["own"] = bestlag
    return out


# ── statistics ───────────────────────────────────────────────────────────────
def boot(vals, seed=186):
    import numpy as np
    n = len(vals)
    if n == 0:
        return None
    v = np.asarray(vals, dtype=float)
    mean = float(v.mean())
    if n < 2:
        return {"n": n, "mean": mean, "lo": None, "hi": None, "p": 1.0}
    rng = np.random.default_rng(seed)
    ms = np.sort(v[rng.integers(0, n, size=(B, n))].mean(axis=1))
    le, ge = float((ms <= 0).mean()), float((ms >= 0).mean())
    return {"n": n, "mean": mean, "lo": float(np.quantile(ms, 0.025)), "hi": float(np.quantile(ms, 0.975)),
            "p": min(1.0, 2 * min(le, ge))}


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - k) * ps[i]))
        adj[i] = run
    return adj


def ll(p):
    return -math.log(max(p, 1e-12))


def a_vals(rs):
    return [ll(r["glob"][SIDES.index(r["result"])]) - ll(r["loc"][SIDES.index(r["result"])]) for r in rs]


def roi_vals(rs, key):
    return [(r[key] - 1.0) if r["result"] == r["side"] else -1.0 for r in rs if r.get(key)]


def fires(r, g):
    return r.get("loc") is not None and r["gap"] >= g and r["min_member_gap"] >= g / 2


def own_fires(r, g):
    o = r.get("own")
    return o is not None and o["lag"] >= g and o["min_other"] >= g / 2


def fmt(s, pct=False):
    if not s:
        return "n=0"
    k = 100 if pct else 1
    lo = "—" if s["lo"] is None else f"{s['lo'] * k:+.3f}"
    hi = "—" if s["hi"] is None else f"{s['hi'] * k:+.3f}"
    u = "%" if pct else ""
    return f"n={s['n']:>4}  mean {s['mean'] * k:+.3f}{u}  [{lo}, {hi}]  p={s['p']:.3f}"


def exchange_check(usable, ko_by_id):
    """AMENDMENT 2 (post-hoc). Betfair Exchange (our own reader, every 15 min, since 2026-09-24) is a
    LIVE global price that does not go through API-Football. At the last capture <= 30 min before KO,
    does the exchange sit with the LOCAL line or with AF's Pinnacle close on the locals' favoured side?"""
    import numpy as np
    from workers.utils.anchor import exchange_fair
    out = []
    for r in usable:
        ko = ko_by_id[r["id"]]
        if r["src"] != "pinnacle" or ko.strftime("%Y-%m-%d") < "2026-09-24":
            continue
        ex = execute_query(
            """WITH last AS (SELECT market_id, captured_at FROM exchange_quotes
                              WHERE match_id = %s AND market = '1x2' AND captured_at <= %s
                                AND captured_at >= %s - interval '30 minutes'
                              ORDER BY captured_at DESC LIMIT 1)
               SELECT lower(q.selection) AS selection, q.back::float AS back, q.lay::float AS lay,
                      q.market_matched::float AS market_matched
                 FROM exchange_quotes q JOIN last l ON q.market_id = l.market_id AND q.captured_at = l.captured_at""",
            (r["id"], ko, ko)) or []
        probs, liquid, _ = exchange_fair({x["selection"]: x for x in ex}, SIDES, max_spread=0.10, min_matched=0)
        if probs:
            out.append((r["p_loc_side"] - r["p_glob_side"], probs[r["side"]] - r["p_glob_side"],
                        probs[r["side"]], r["p_loc_side"], r["p_glob_side"], liquid))
    if len(out) < 3:
        print("  exchange check: too few matches")
        return
    a = np.array([(o[0], o[1]) for o in out])
    slope, icpt = np.polyfit(a[:, 0], a[:, 1], 1)
    print(f"  matches since 2026-09-24 with an exchange 1x2 capture <= 30 min pre-KO: {len(out)} "
          f"(liquid by the anchor's 5%/EUR1k rule: {sum(1 for o in out if o[5])})")
    print(f"  on the locals' favoured side: corr(local - AFpin, exchange - AFpin) = "
          f"{np.corrcoef(a[:, 0], a[:, 1])[0, 1]:.2f}, OLS slope {slope:.2f} (1 = exchange sits WITH the locals, "
          f"0 = with AF Pinnacle)")
    for g in (0.03, 0.05, 0.08):
        sub = [o for o in out if o[0] >= g]
        if sub:
            print(f"  gap >= {g:.2f}: n={len(sub)}  local - AFpin {np.mean([o[0] for o in sub]):+.3f}  "
                  f"exchange - AFpin {np.mean([o[1] for o in sub]):+.3f}  exchange nearer the locals on "
                  f"{sum(1 for o in sub if abs(o[2] - o[3]) < abs(o[2] - o[4]))}/{len(sub)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    args = ap.parse_args()
    matches, rows, dq = load()
    by_mid = defaultdict(list)
    for r in rows:
        by_mid[r["mid"]].append(r)
    built = [x for x in (build(m, by_mid[m["id"]], dq) for m in matches) if x]
    usable = [r for r in built if r.get("loc") is not None]
    drops = defaultdict(int)
    for r in built:
        for b, why in r["dropped"].items():
            drops[(b, why)] += 1
    print(f"candidate matches {len(matches)}  with a global line {len(built)}  "
          f"with >=2 local books after guards {len(usable)}")
    print("  global source:", dict(sorted(defaultdict(int, {s: sum(1 for r in usable if r['src'] == s)
                                                          for s in ('pinnacle', 'consensus')}).items())))
    print("  guard drops (book, reason):", dict(drops))
    print("  local book sets:", dict(sorted(
        {k: sum(1 for r in usable if "+".join(r["local_books"]) == k)
         for k in {"+".join(r["local_books"]) for r in usable}}.items(), key=lambda kv: -kv[1])))
    la = sorted(r["local_age"] for r in usable)
    print(f"  oldest local member age at KO: median {la[len(la)//2]:.0f} min, p90 {la[int(.9*len(la))]:.0f}")
    base = boot(a_vals(usable))
    print("\nBASE RATE (a) on ALL usable matches, LL_global - LL_local:", fmt(base))

    fam, labels, cells = [], [], {}
    for g in G_GRID:
        sig = [r for r in usable if fires(r, g)]
        sig_pin = [r for r in sig if r["src"] == "pinnacle"]
        sig_mv = [r for r in sig_pin if r["pin_move"] is not None and (r["pin_open_age_h"] or 0) >= 6]
        own = [r["own"] | {"result": r["result"]} for r in usable if own_fires(r, g)]
        own_roi = [(o["odds"] - 1.0) if o["result"] == o["side"] else -1.0 for o in own]
        res = {
            "a": boot(a_vals(sig)),
            "b1": boot(roi_vals(sig, "best_odds")),
            "b2": boot(roi_vals(sig_pin, "pin_odds")),
            "c": boot([r["pin_move"] for r in sig_mv]),
            "d": boot(own_roi),
        }
        losses = {"b1": sum(1 for r in sig if r.get("best_odds") and r["result"] != r["side"]),
                  "b2": sum(1 for r in sig_pin if r.get("pin_odds") and r["result"] != r["side"]),
                  "d": sum(1 for o in own if o["result"] != o["side"])}
        cells[g] = (sig, sig_pin, own, res, losses)
        for k, s in res.items():
            labels.append((g, k))
            fam.append(1.0 if (not s or s["n"] < MIN_N) else s["p"])
    adj = holm(fam)
    print(f"\nFREQUENCY (of {len(usable)} usable matches)")
    for g in G_GRID:
        sig, sig_pin, own, _, _ = cells[g]
        print(f"  G={g:.2f}: signal {len(sig)} ({100*len(sig)/len(usable):.2f}%), Pinnacle-anchored "
              f"{len(sig_pin)}; OWN lag fires {len(own)} (of {sum(1 for r in usable if r.get('own'))} "
              f"matches with >=3 locals)")
    print("\nPRE-REGISTERED FAMILY (Holm over 15; a = LL_global-LL_local, b/d = ROI, c = Pinnacle "
          "prob move toward locals)")
    out_family = []
    for (g, k), p_adj in zip(labels, adj):
        s = cells[g][3][k]
        if not s or s["n"] < MIN_N:
            verdict = "too few"
        elif p_adj < 0.05:
            verdict = "positive" if s["mean"] > 0 else "negative"
        else:
            verdict = "undetermined"
        lw = cells[g][4].get(k)
        flag = "  !CI(<2 losses)" if lw is not None and lw < 2 and s else ""
        print(f"  G={g:.2f} {k:>2}: {fmt(s, pct=k in ('b1','b2','d'))}  holm={p_adj:.3f}  {verdict}{flag}")
        out_family.append({"G": g, "test": k, **(s or {}), "p_holm": p_adj, "verdict": verdict})

    print("\nDESCRIPTIVE (outside the family)")
    for g in G_GRID:
        sig, sig_pin, own, _, _ = cells[g]
        print(f"\n  G={g:.2f}")
        for lab, key in (("Pinnacle liveness", "pin_live"), ("best-price-book liveness", "best_live")):
            for st in ("LIVE", "FROZEN", "UNKNOWN"):
                sub = [r for r in sig if r[key] == st]
                if sub:
                    print(f"    {lab:>24} {st:<8} a {fmt(boot(a_vals(sub)))} | "
                          f"b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        prox = [r for r in sig_pin if r["pin_open_ne_close"] is not None]
        print(f"    Pinnacle open!=close proxy: {sum(1 for r in prox if r['pin_open_ne_close'])}/{len(prox)} moved")
        for lab, f in (("Nordic/Baltic", lambda r: r["country"] in NORDIC),
                       ("rest", lambda r: r["country"] not in NORDIC)):
            sub = [r for r in sig if f(r)]
            print(f"    {lab:>14}: a {fmt(boot(a_vals(sub)))} | b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        tiers = sorted({r["tier"] for r in sig if r["tier"] is not None})
        for t in tiers:
            sub = [r for r in sig if r["tier"] == t]
            print(f"    tier {t}: a {fmt(boot(a_vals(sub)))} | b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        for bk in LOCAL:
            sub = [r for r in sig if bk in r["local_books"]]
            print(f"    with {bk:<12} n={len(sub)}")
        side_ct = defaultdict(int)
        for r in sig:
            side_ct[r["side"]] += 1
        print(f"    sides: {dict(side_ct)}  mean best odds "
              f"{sum(r['best_odds'] for r in sig if r.get('best_odds')) / max(1, sum(1 for r in sig if r.get('best_odds'))):.2f}")
        ev = [o["odds"] * o["pin_fair"] - 1 for o in own if o["pin_fair"]]
        if ev:
            print(f"    OWN lag bets EV vs Pinnacle close fair: {fmt(boot(ev), True)}")
        own_bk = defaultdict(int)
        for o in own:
            own_bk[o["book"]] += 1
        print(f"    OWN lagging book: {dict(own_bk)}")
        lg = defaultdict(int)
        for r in sig:
            lg[f"{r['country']} / {r['league']}"] += 1
        print("    top leagues:", sorted(lg.items(), key=lambda kv: -kv[1])[:8])

    print("\nPOST-HOC DIAGNOSTICS (AMENDMENT 1 — added after the first run; NOT part of the family)")
    for g in G_GRID[:2]:
        sig = cells[g][0]
        sp = [r for r in sig if r["pin_age"] is not None]
        later = sum(1 for r in sp if r["local_newest_age"] < r["pin_age"])
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float("nan")
        print(f"\n  G={g:.2f}  n={len(sig)}")
        print(f"    timing: Pinnacle close median {med([r['pin_age'] for r in sp]):.0f} min before KO; newest "
              f"local {med([r['local_newest_age'] for r in sig]):.0f} min; locals LATER than Pinnacle close "
              f"on {later}/{len(sp)}")
        ch = [r for r in sp if r["pin_changed_60"] is not None]
        for st in (True, False):
            sub = [r for r in ch if r["pin_changed_60"] == st]
            if sub:
                print(f"    Pinnacle changed in last 60 min = {st}: a {fmt(boot(a_vals(sub)))} | "
                      f"b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        sa = [r for r in sp if r["pin_at_local"] is not None]
        if sa:
            mv = [r["p_glob_side"] - r["pin_at_local"] for r in sa]
            print(f"    same instant: Pinnacle s* prob at the locals' time vs at close, n={len(sa)}: "
                  f"gap at locals' time {sum(r['p_loc_side'] - r['pin_at_local'] for r in sa) / len(sa):+.3f}, "
                  f"Pinnacle move locals'-time -> close {fmt(boot(mv))}")
        n = len(sig)
        win = sum(1 for r in sig if r["result"] == r["side"])
        print(f"    signal side won {win}/{n} = {win / max(n, 1):.3f}; mean p_local {sum(r['p_loc_side'] for r in sig) / max(n, 1):.3f}"
              f", mean p_global {sum(r['p_glob_side'] for r in sig) / max(n, 1):.3f}")
        for mo in sorted({r["month"] for r in sig}):
            sub = [r for r in sig if r["month"] == mo]
            print(f"    {mo}: a {fmt(boot(a_vals(sub)))} | b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        youth = lambda r: any(k in (r["league"] or "") for k in ("Trophy", "Premier League 2", "Development",
                                                                 "U21", "U23", "U19", "Reserve", "II"))
        for lab, f in (("youth/reserve/cup-trophy", youth), ("other", lambda r: not youth(r))):
            sub = [r for r in sig if f(r)]
            print(f"    {lab:>25}: a {fmt(boot(a_vals(sub)))} | b1 {fmt(boot(roi_vals(sub, 'best_odds')), True)}")
        for lab, pop in (("signal", sig), ("ALL usable", [r for r in usable if r.get("pin_age") is not None])):
            xs = sorted(r["pin_last_change"] for r in pop if r.get("pin_last_change") is not None)
            c60 = [r["pin_changed_60"] for r in pop if r.get("pin_changed_60") is not None]
            if xs:
                print(f"    {lab:>10}: Pinnacle last value change median {xs[len(xs)//2]:.0f} min before KO "
                      f"(n={len(xs)}); changed in last 60 min on {sum(c60)}/{len(c60)} = {sum(c60)/max(1,len(c60)):.2f}")
        books = defaultdict(int)
        for r in sig:
            books[r["best_book"]] += 1
        print(f"    best-price book: {dict(books)}")

    print("\nAMENDMENT 2 (post-hoc) — is the AF 'global close' live? Betfair Exchange at kick-off")
    exchange_check(usable, {m["id"]: m["ko"] for m in matches})
    if args.dump:
        with open(args.dump, "w") as fh:
            json.dump({"family": out_family,
                       "signals": {str(g): [{k: v for k, v in r.items() if k not in ("own",)}
                                            for r in cells[g][0]] for g in G_GRID}},
                      fh, default=str, indent=1)


if __name__ == "__main__":
    main()
