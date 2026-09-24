#!/usr/bin/env python3
"""#149 — EXPLORATORY scan: does the 1X2 consensus-outlier mechanism exist in other markets?

Mechanism (the one that worked for 1X2 in B2, `dev/active/1x2-model-rebuild-plan.md`):
a single book's quote sits well above a de-vigged multi-book consensus, and the pick then
shows positive CLV against Pinnacle's de-vigged close. Here it is measured MODEL-FREE:

  * decision instant = the quoting book's OPENING complete market (first pre-kickoff
    timestamp at which every leg of that market/line exists for that book). Older rows keep
    only opening + latest, so the opening is the only instant that is exact in both eras;
  * fair probability = LEAVE-ONE-OUT consensus: mean of the OTHER publishable books'
    de-vigged probabilities, each book's latest complete market at or before the instant and
    at most 3 h old; >= 4 other books required; Marathonbet dropped when 1xBet is present
    (same feed). De-vig methods: Shin (primary, gotcha #78), proportional (worst-calibrated,
    reported for contrast) and power;
  * double chance: consensus built from the other books' 1X2 (legs summed) — a DC board is
    not a mutually-exclusive market and cannot be de-vigged directly;
  * outlier = EV = p_fair * odds - 1 >= 3% / 5% / 8%; EV > 25% dropped (the live
    ODDS-OUTLIER-FILTER's 1.25x anchor rule); odds <= 10; Pinnacle is never the quoting book;
  * one pick per (match, market, line) = best EV across books and selections; the AF vs
    direct-sweeper split re-selects within each book set (consensus always uses all books);
  * CLV target = Pinnacle's latest complete pre-kickoff market at the SAME market+line, taken
    only if it is within 3 h of kickoff (gotcha #16: an abandoned AH/OU rung is not a close),
    Shin de-vigged. DC and DNB have no Pinnacle market: derived from Pinnacle's 1X2 close.
    Secondary target (labelled, gotcha #74): leave-one-out consensus close of >= 4 books;
  * ROI = flat 1u at the quoted odds, settled from stored results: goals FT/HT, AH (home-
    perspective line, gotcha #53; pushes refund), corners FT from match_stats. NOT settled:
    cards (settlement deliberately has no cards resolver — definitions differ by book),
    1H corners (gotcha #76), quarter AH lines (excluded entirely);
  * guards: O/U ladder (over odds must rise with the line per match x book, gotcha #80) for
    FT and 1H; wrong-board guard (gotcha #79): a (match, book) whose de-vigged probabilities
    sit > 20 pp off the preliminary consensus in >= 2 market families is dropped everywhere;
    `matches.date_disputed_at` rows dropped (gotcha #21).

Statistics: everything clustered by MATCH (a match carries several lines). CI = 95% cluster
bootstrap (2,000 resamples, fixed seed). Raw p = one-sided cluster-robust z for mean CLV > 0.
Holm across every reporting unit with >= 10 CLV picks at each EV threshold (1X2 is a
calibration row and sits OUTSIDE the family). This is a multi-market SCAN: a unit that
passes is a candidate to confirm forward, not a finding.

Read-only (readonly session). Output: data/models/_research/market2/ (gitignored).

    python3 scripts/scan_consensus_outlier_markets.py
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
from workers.jobs.daily_pipeline_v2 import is_publishable_book  # noqa: E402

WINDOW_START = "2026-08-31T00:00:00Z"
WINDOW_END = "2026-09-25T00:00:00Z"          # exclusive
WINDOW_DAYS = 25.0
FULL_HISTORY_FROM = datetime(2026, 9, 17, tzinfo=timezone.utc).timestamp()
OUT_DIR = ROOT / "data" / "models" / "_research" / "market2"
DIRECT = frozenset({"Coolbet", "Unibet-Site", "Epicbet", "Tonybet"})
EV_THRESHOLDS = (0.03, 0.05, 0.08)
EV_CAP = 0.25
ODDS_MAX = 10.0
MIN_OTHERS = 4
STATE_MAX_AGE_S = 3 * 3600
CLOSE_MAX_BEFORE_KO_S = 3 * 3600
CLOSE_LEG_SPREAD_S = 600
BOARD_DEV = 0.20
CHUNK = 250
N_BOOT = 2000
SEED = 149
METHODS = ("shin", "prop", "pow")

SELS = {
    "3way": ("home", "draw", "away"),
    "dc": ("1x", "x2", "12"),
    "ha": ("home", "away"),
    "ou": ("over", "under"),
    "btts": ("yes", "no"),
}


# ───────────────────────────── market vocabulary ─────────────────────────────
def classify(market: str):
    """market -> (unit family, selection set, settle kind) or None."""
    if market == "1x2":
        return "1X2 (calibration)", "3way", "1x2_ft"
    if market == "1x2_1h":
        return "1X2 1H", "3way", "1x2_ht"
    if market == "double_chance":
        return "Double chance", "dc", "dc"
    if market == "draw_no_bet":
        return "Draw no bet", "ha", "dnb"
    if market == "btts":
        return "BTTS", "btts", "btts"
    if market == "asian_handicap":
        return "AH", "ha", "ah"
    if re.fullmatch(r"over_under_\d+", market):
        return "O/U FT", "ou", "goals_ft"
    if re.fullmatch(r"over_under_1h_\d+", market):
        return "O/U 1H", "ou", "goals_ht"
    if re.fullmatch(r"team_total_(home|away)_\d+", market):
        return "Team total FT", "ou", "team_goals_ft"
    if re.fullmatch(r"team_total_1h_(home|away)_\d+", market):
        return "Team total 1H", "ou", "team_goals_ht"
    if re.fullmatch(r"corners_ou_\d+", market):
        return "Corners O/U FT", "ou", "corners_ft"
    if re.fullmatch(r"corners_(home|away)_ou_\d+", market):
        return "Team corners FT", "ou", "team_corners_ft"
    if re.fullmatch(r"corners_1h_ou_\d+", market):
        return "Corners O/U 1H", "ou", None
    if re.fullmatch(r"cards_ou_\d+", market):
        return "Cards O/U", "ou", None
    if re.fullmatch(r"cards_(home|away)_ou_\d+", market):
        return "Team cards", "ou", None
    return None


def line_of(market: str, hl) -> float | None:
    """Numeric line: handicap_line when stored, else an unambiguous 2-digit label (gotcha #61)."""
    if hl is not None and not (isinstance(hl, float) and math.isnan(hl)):
        return float(hl)
    m = re.search(r"_(\d+)$", market)
    if m and len(m.group(1)) == 2:
        return int(m.group(1)) / 10.0
    return None


def unit_of(family: str, market: str, line: float | None) -> str:
    if family == "AH":
        return f"AH {line:+.1f}" if abs(line) <= 2.5 else "AH |line|>2.5"
    if family in ("O/U FT", "O/U 1H"):
        return f"{family} {line:.1f}"
    return family


# ───────────────────────────── DB (read-only) ─────────────────────────────
def conn():
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.set_session(readonly=True, autocommit=True)
    with c.cursor() as cur:
        cur.execute("SET statement_timeout='900s'")
    return c


CONN = None
NULLQ: list = []   # EVERY quote with a consensus (any EV) — the null / EV-CLV curve


def q(sql, params=None):
    with CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


MARKET_SQL = """(o.market IN ('1x2','1x2_1h','double_chance','draw_no_bet','btts','asian_handicap')
   OR o.market ~ '^(over_under_|team_total_|corners_ou_|corners_home_ou_|corners_away_ou_|corners_1h_ou_|cards_ou_|cards_home_ou_|cards_away_ou_)[0-9a-z_]*$')"""


def load_chunk(ids):
    """Rows needed: every row up to the LAST opening of its (match, market[, AH line]) group
    (so any book's state at any opening instant is available) + each leg's latest row (close)."""
    return q(f"""
      WITH r AS (
        SELECT o.match_id::text match_id, o.bookmaker, o.market,
               CASE WHEN o.market = 'asian_handicap' THEN o.handicap_line END::float8 ahl,
               o.handicap_line::float8 hl, o.selection, o.odds::float8 odds,
               extract(epoch FROM o."timestamp")::float8 ts, extract(epoch FROM m.date)::float8 ko,
               o."timestamp" t
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND o."timestamp" < m.date AND {MARKET_SQL}),
      w AS (
        SELECT r.*, min(t) OVER (PARTITION BY match_id, bookmaker, market, ahl) open_b,
               row_number() OVER (PARTITION BY match_id, bookmaker, market, ahl, selection ORDER BY t DESC) rn
          FROM r),
      g AS (SELECT w.*, max(open_b) OVER (PARTITION BY match_id, market, ahl) last_open FROM w)
      SELECT match_id, bookmaker, market, hl, ahl, selection, odds, ts, ko, (rn = 1) is_last
        FROM g WHERE t <= last_open OR rn = 1""", (ids,))


# ───────────────────────────── de-vig (vectorised) ─────────────────────────────
def devig_prop(O):
    inv = 1.0 / O
    return inv / inv.sum(1, keepdims=True)


def devig_shin(O):
    inv = 1.0 / O
    tot = inv.sum(1, keepdims=True)
    lo = np.zeros((len(O), 1))
    hi = np.full((len(O), 1), 1 - 1e-9)
    for _ in range(60):
        z = (lo + hi) / 2
        s = ((np.sqrt(z * z + 4 * (1 - z) * inv * inv / tot) - z) / (2 * (1 - z))).sum(1, keepdims=True)
        up = s > 1
        lo = np.where(up, z, lo)
        hi = np.where(up, hi, z)
    z = (lo + hi) / 2
    P = (np.sqrt(z * z + 4 * (1 - z) * inv * inv / tot) - z) / (2 * (1 - z))
    P = P / P.sum(1, keepdims=True)
    return np.where(tot > 1, P, inv / tot)       # no margin -> proportional (as devig.shin_devig)


def devig_pow(O):
    inv = 1.0 / O
    lo = np.full((len(O), 1), 0.2)
    hi = np.full((len(O), 1), 20.0)
    for _ in range(60):
        k = (lo + hi) / 2
        s = (inv ** k).sum(1, keepdims=True)
        up = s > 1                                 # sum too big -> raise k
        lo = np.where(up, k, lo)
        hi = np.where(up, hi, k)
    P = inv ** ((lo + hi) / 2)
    return P / P.sum(1, keepdims=True)


DEVIG = {"shin": devig_shin, "prop": devig_prop, "pow": devig_pow}


# ───────────────────────────── chunk processing ─────────────────────────────
def states_for(df_m: pd.DataFrame, sels: tuple) -> pd.DataFrame:
    """Complete-market states per (match, mkey, book): at every timestamp, the latest leg of
    each selection (forward-filled within the book's series — handles Coolbet's per-leg writes,
    gotcha #62). Rows before all legs exist are dropped."""
    w = df_m.pivot_table(index=["match_id", "mkey", "bookmaker", "ts"], columns="selection",
                         values="odds", aggfunc="last")
    w = w.reindex(columns=list(sels))
    w = w.groupby(level=[0, 1, 2]).ffill().dropna()
    return w.reset_index()


def with_probs(st: pd.DataFrame, sels: tuple) -> pd.DataFrame:
    O = st[list(sels)].to_numpy(float)
    for m in METHODS:
        P = DEVIG[m](O)
        for i, s in enumerate(sels):
            st[f"{m}_{s}"] = P[:, i]
    return st


def dc_from_1x2(st1: pd.DataFrame) -> pd.DataFrame:
    """Virtual double-chance consensus states from 1X2 states (legs summed)."""
    st1 = st1[st1.mkey == "1x2"]                  # NOT 1x2_1h
    out = st1[["match_id", "bookmaker", "ts"]].copy()
    out["mkey"] = "double_chance"
    for m in METHODS:
        h, d, a = st1[f"{m}_home"], st1[f"{m}_draw"], st1[f"{m}_away"]
        out[f"{m}_1x"], out[f"{m}_x2"], out[f"{m}_12"] = h + d, d + a, h + a
    return out


def consensus(quotes: pd.DataFrame, cons: pd.DataFrame, sels: tuple, exclude: set) -> pd.DataFrame:
    """LOO consensus at each quote's instant. quotes: [qid, match_id, mkey, bookmaker, ts]."""
    pcols = [f"{m}_{s}" for m in METHODS for s in sels]
    if exclude:
        key = list(zip(cons.match_id, cons.bookmaker))
        cons = cons[[k not in exclude for k in key]]
    books = cons[["match_id", "mkey", "bookmaker"]].drop_duplicates().rename(columns={"bookmaker": "ob"})
    left = quotes.merge(books, on=["match_id", "mkey"])
    left = left[left.ob != left.bookmaker]
    if left.empty:
        return pd.DataFrame(columns=["qid", "n_others"] + pcols)
    right = cons.rename(columns={"bookmaker": "ob", "ts": "cts"})[["match_id", "mkey", "ob", "cts"] + pcols]
    left = left.sort_values("ts")
    right = right.sort_values("cts")
    j = pd.merge_asof(left, right, left_on="ts", right_on="cts", by=["match_id", "mkey", "ob"],
                      direction="backward", tolerance=float(STATE_MAX_AGE_S))
    j = j.dropna(subset=["cts"])
    # Marathonbet mirrors 1xBet: count it once
    q1x = set(j.qid[j.ob == "1xBet"])
    j = j[~((j.ob == "Marathonbet") & j.qid.isin(q1x))]
    g = j.groupby("qid")
    out = g[pcols].mean()
    out["n_others"] = g.size()
    return out.reset_index()


def close_states(df_m: pd.DataFrame, sels: tuple) -> pd.DataFrame:
    """Per (match, mkey, book): latest complete pre-KO market, legs within 10 min, <= 3 h pre-KO."""
    last = df_m[df_m.is_last]
    w = last.pivot_table(index=["match_id", "mkey", "bookmaker"], columns="selection", values="odds", aggfunc="last")
    ts = last.groupby(["match_id", "mkey", "bookmaker"]).ts.agg(["min", "max"])
    ko = last.groupby(["match_id", "mkey", "bookmaker"]).ko.first()
    w = w.reindex(columns=list(sels)).join(ts).join(ko)
    w = w.dropna(subset=list(sels))
    w = w[(w["max"] - w["min"] <= CLOSE_LEG_SPREAD_S) & (w["ko"] - w["max"] <= CLOSE_MAX_BEFORE_KO_S)]
    w = w.reset_index()
    if w.empty:
        return w
    P = devig_shin(w[list(sels)].to_numpy(float))
    for i, s in enumerate(sels):
        w[f"c_{s}"] = P[:, i]
    return w[["match_id", "mkey", "bookmaker", "max"] + [f"c_{s}" for s in sels]]


def ladder_flags(opens: pd.DataFrame) -> set:
    """(match, book, family) whose opening over-odds do not strictly rise with the line."""
    bad = set()
    o = opens[opens.family.isin(["O/U FT", "O/U 1H"])]
    for (mid, b, fam), g in o.groupby(["match_id", "bookmaker", "family"]):
        g = g.sort_values("line")
        ov = g["over"].to_numpy(float)
        if len(ov) > 1 and np.any(np.diff(ov) <= 0):
            bad.add((mid, b, fam))
    return bad


def process_chunk(rows: list[dict], diag: dict):
    df = pd.DataFrame(rows)
    if df.empty:
        return [], [], []
    df = df[df.bookmaker.map(is_publishable_book)]
    info = {mk: classify(mk) for mk in df.market.unique()}
    df = df[df.market.map(lambda m: info[m] is not None)]
    df["family"] = df.market.map(lambda m: info[m][0])
    df["kind"] = df.market.map(lambda m: info[m][1])
    # numeric line; keep .5 lines for totals, full/half for AH (quarter lines excluded)
    lab = {mk: line_of(mk, None) for mk in df.market.unique()}
    df["line"] = np.where(df.hl.notna(), df.hl, df.market.map(lab).astype(float))
    df.loc[df.kind != "ou", "line"] = np.nan
    is_tot = df.kind == "ou"
    df = df[~is_tot | (df.line.notna() & ((df.line * 2) % 2 == 1))]
    is_ah = df.family == "AH"
    diag["ah_quarter_rows_dropped"] += int((is_ah & df.ahl.notna() & ((df.ahl * 4) % 2 == 1)).sum())
    df = df[~is_ah | (df.ahl.notna() & ((df.ahl * 2) % 1 == 0))]
    df["mkey"] = np.where(df.family == "AH", "asian_handicap|" + df.ahl.astype(str), df.market)
    meta = df[["mkey", "market", "family", "kind", "line", "ahl"]].drop_duplicates("mkey").set_index("mkey")

    ko_of = df.groupby("match_id").ko.first().to_dict()
    all_states, all_close = {}, {}
    for kind, dk in df.groupby("kind"):
        sels = SELS[kind]
        all_states[kind] = with_probs(states_for(dk, sels), sels)
        all_close[kind] = close_states(dk, sels)
    # openings (first complete state) per (match, mkey, book)
    opens_by_kind = {k: st.sort_values("ts").groupby(["match_id", "mkey", "bookmaker"]).head(1).copy()
                     for k, st in all_states.items()}
    for k, o in opens_by_kind.items():
        o["family"] = o.mkey.map(meta.family)
        o["line"] = o.mkey.map(meta.line)
    # ── guard 1: O/U ladder
    bad_ladder = ladder_flags(opens_by_kind.get("ou", pd.DataFrame(columns=["family", "match_id", "bookmaker", "line", "over"])))
    diag["ladder_flagged_pairs"] += len(bad_ladder)
    if bad_ladder:
        for k in ("ou",):
            if k in all_states:
                st = all_states[k]
                fam = st.mkey.map(meta.family)
                keep = [(m, b, f) not in bad_ladder for m, b, f in zip(st.match_id, st.bookmaker, fam)]
                all_states[k] = st[keep]
                o = opens_by_kind[k]
                opens_by_kind[k] = o[[(m, b, f) not in bad_ladder for m, b, f in zip(o.match_id, o.bookmaker, o.family)]]

    def cons_source(kind):
        if kind == "dc":
            return dc_from_1x2(all_states["3way"]) if "3way" in all_states else None
        return all_states[kind]

    def run(exclude: set):
        res = {}
        for kind, o in opens_by_kind.items():
            sels = SELS[kind]
            cs = cons_source(kind)
            if cs is None or o.empty:
                continue
            quotes = o[["match_id", "mkey", "bookmaker", "ts"]].copy()
            quotes["qid"] = np.arange(len(quotes))
            c = consensus(quotes, cs, sels, exclude)
            res[kind] = (quotes, c)
        return res

    # ── guard 2: wrong boards — preliminary consensus, then drop (match, book) off in >= 2 families
    prelim = run(set())
    devs = []
    for kind, (quotes, c) in prelim.items():
        sels = SELS[kind]
        o = opens_by_kind[kind].reset_index(drop=True)
        own = o[[f"shin_{s}" for s in sels]].to_numpy(float) if kind != "dc" else None
        if own is None:
            continue
        cc = c.set_index("qid").reindex(np.arange(len(o)))
        cm = cc[[f"shin_{s}" for s in sels]].to_numpy(float)
        dev = np.nanmax(np.abs(own - cm), axis=1)
        devs.append(pd.DataFrame({"match_id": o.match_id, "bookmaker": o.bookmaker, "family": o.family, "dev": dev}))
    exclude = set()
    if devs:
        d = pd.concat(devs)
        d = d[d.dev > BOARD_DEV]
        nf = d.groupby(["match_id", "bookmaker"]).family.nunique()
        exclude = set(nf[nf >= 2].index)
    diag["board_flagged_pairs"] += len(exclude)
    for (mid, b) in exclude:
        diag["board_flagged_by_book"][b] = diag["board_flagged_by_book"].get(b, 0) + 1
    final = run(exclude)

    cands = []
    for kind, (quotes, c) in final.items():
        sels = SELS[kind]
        o = opens_by_kind[kind].reset_index(drop=True)
        o["qid"] = np.arange(len(o))
        j = o.merge(c, on="qid", suffixes=("", "_c"))
        j = j[j.n_others >= MIN_OTHERS]
        j = j[[(m, b) not in exclude for m, b in zip(j.match_id, j.bookmaker)]]
        j = j[j.bookmaker != "Pinnacle"]
        diag["quotes_with_consensus"] += len(j)
        diag["quotes_by_unit_kind"][kind] = diag["quotes_by_unit_kind"].get(kind, 0) + len(j)
        for s in sels:
            odds = j[s].to_numpy(float)
            evs = {m: j[f"{m}_{s}_c"].to_numpy(float) * odds - 1 for m in METHODS}
            mx = np.maximum.reduce([evs[m] for m in METHODS])
            NULLQ.append(pd.DataFrame({"match_id": j.match_id.to_numpy(), "mkey": j.mkey.to_numpy(),
                                       "bookmaker": j.bookmaker.to_numpy(), "selection": s, "odds": odds,
                                       "ev_shin": evs["shin"], "family": j.family.to_numpy()}))
            keep = (mx >= min(EV_THRESHOLDS)) & (odds <= ODDS_MAX)
            if not keep.any():
                continue
            sub = j[keep]
            rec = pd.DataFrame({"match_id": sub.match_id.to_numpy(), "mkey": sub.mkey.to_numpy(),
                                "bookmaker": sub.bookmaker.to_numpy(), "selection": s,
                                "odds": odds[keep], "ts": sub.ts.to_numpy(), "n_others": sub.n_others.to_numpy()})
            for m in METHODS:
                rec[f"ev_{m}"] = evs[m][keep]
                rec[f"pfair_{m}"] = sub[f"{m}_{s}_c"].to_numpy(float)
            cands.append(rec)
    # closes: Pinnacle per (match, mkey), consensus close per book (LOO later)
    closes = []
    for kind, cl in all_close.items():
        if kind == "dc" or cl is None or len(cl) == 0:     # DC board is not exclusive; derived below
            continue
        cl = cl[[(m, b) not in exclude for m, b in zip(cl.match_id, cl.bookmaker)]]
        sels = SELS[kind]
        lng = cl.melt(id_vars=["match_id", "mkey", "bookmaker"], value_vars=[f"c_{s}" for s in sels],
                      var_name="selection", value_name="p")
        lng["selection"] = lng.selection.str[2:]
        closes.append(lng)
        if kind == "3way":    # derived DC and DNB closes from 1X2 (Pinnacle has neither market)
            one = cl[cl.mkey == "1x2"]
            h, d_, a = one.c_home, one.c_draw, one.c_away
            for mk, pairs in (("double_chance", {"1x": h + d_, "x2": d_ + a, "12": h + a}),
                              ("draw_no_bet", {"home": h / (h + a), "away": a / (h + a)})):
                for s, p in pairs.items():
                    closes.append(pd.DataFrame({"match_id": one.match_id, "mkey": mk,
                                                "bookmaker": one.bookmaker + ("*1x2" if mk == "draw_no_bet" else ""),
                                                "selection": s, "p": p}))
    cands = pd.concat(cands) if cands else pd.DataFrame()
    if len(cands):
        cands["ko"] = cands.match_id.map(ko_of)
        for col in ("market", "family", "line", "ahl"):
            cands[col] = cands.mkey.map(meta[col])
    return cands, (pd.concat(closes) if closes else pd.DataFrame()), meta


# ───────────────────────────── settlement ─────────────────────────────
def settle(c: pd.DataFrame, res: pd.DataFrame) -> np.ndarray:
    """1 win, 0 loss, 0.5 push (refund), nan unsettled."""
    r = c.merge(res, on="match_id", how="left")
    out = np.full(len(r), np.nan)
    for i, x in enumerate(r.itertuples(index=False)):
        if x.status != "finished" or x.gh is None or math.isnan(x.gh):
            continue
        fam, s, L, mk = x.family, x.selection, x.line, x.market
        gh, ga, hh, ha = x.gh, x.ga, x.hh, x.ha
        ch, ca = x.ch, x.ca

        def ou(v):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return np.nan
            return float((v > L) == (s == "over"))
        if fam == "1X2 (calibration)" or fam == "1X2 1H":
            a_, b_ = (gh, ga) if fam.startswith("1X2 (") else (hh, ha)
            if a_ is None or b_ is None or (isinstance(a_, float) and math.isnan(a_)):
                continue
            w = "home" if a_ > b_ else ("away" if a_ < b_ else "draw")
            out[i] = float(w == s)
        elif fam == "Double chance":
            w = "home" if gh > ga else ("away" if gh < ga else "draw")
            out[i] = float(w in {"1x": ("home", "draw"), "x2": ("draw", "away"), "12": ("home", "away")}[s])
        elif fam == "Draw no bet":
            out[i] = 0.5 if gh == ga else float((gh > ga) == (s == "home"))
        elif fam == "BTTS":
            out[i] = float(((gh > 0) and (ga > 0)) == (s == "yes"))
        elif fam == "AH":
            v = (gh - ga) + x.ahl                 # home-perspective line (gotcha #53)
            out[i] = 0.5 if v == 0 else float((v > 0) == (s == "home"))
        elif fam == "O/U FT":
            out[i] = ou(gh + ga)
        elif fam == "O/U 1H":
            out[i] = ou(np.nan if hh is None or math.isnan(hh) else hh + ha)
        elif fam == "Team total FT":
            out[i] = ou(gh if "_home_" in mk else ga)
        elif fam == "Team total 1H":
            out[i] = ou(hh if "_home_" in mk else ha)
        elif fam == "Corners O/U FT":
            out[i] = ou(np.nan if ch is None or math.isnan(ch) else ch + ca)
        elif fam == "Team corners FT":
            out[i] = ou(ch if "_home_" in mk else ca)
    return out


# ───────────────────────────── statistics ─────────────────────────────
def cluster_stats(x: np.ndarray, cl: np.ndarray, rng) -> dict:
    ok = ~np.isnan(x)
    x, cl = x[ok], cl[ok]
    n = len(x)
    if n == 0:
        return {"n": 0}
    codes, inv = np.unique(cl, return_inverse=True)
    G = len(codes)
    sums = np.bincount(inv, weights=x, minlength=G)
    cnts = np.bincount(inv, minlength=G).astype(float)
    mean = x.mean()
    # cluster-robust SE of a ratio mean
    resid = sums - mean * cnts
    se = math.sqrt(G / max(G - 1, 1) * (resid ** 2).sum()) / cnts.sum() if G > 1 else float("nan")
    z = mean / se if se and se > 0 else float("nan")
    p = 0.5 * math.erfc(z / math.sqrt(2)) if not math.isnan(z) else float("nan")
    idx = rng.integers(0, G, (N_BOOT, G))
    bs = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"n": n, "matches": G, "mean": float(mean), "lo": float(np.percentile(bs, 2.5)),
            "hi": float(np.percentile(bs, 97.5)), "p": p}


def holm(ps: dict) -> dict:
    items = sorted((p, k) for k, p in ps.items() if p is not None and not math.isnan(p))
    m = len(items)
    out, run = {k: None for k in ps}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def picks_for(c: pd.DataFrame, method: str, thr: float, bookset: str) -> pd.DataFrame:
    x = c[(c[f"ev_{method}"] >= thr) & (c[f"ev_{method}"] <= EV_CAP)]
    if bookset == "af":
        x = x[~x.bookmaker.isin(DIRECT)]
    elif bookset == "direct":
        x = x[x.bookmaker.isin(DIRECT)]
    x = x.sort_values(f"ev_{method}", ascending=False)
    return x.drop_duplicates(["match_id", "mkey"])


def main() -> int:
    global CONN
    CONN = conn()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ms = q("""SELECT id::text match_id FROM matches WHERE date >= %s AND date < %s
               AND date_disputed_at IS NULL ORDER BY date""", (WINDOW_START, WINDOW_END))
    ids = [m["match_id"] for m in ms]
    if os.environ.get("SCAN_MAX_MATCHES"):          # smoke/debug only: every Nth match
        step = max(1, len(ids) // int(os.environ["SCAN_MAX_MATCHES"]))
        ids = ids[::step]
    print(f"{len(ids):,} matches (kickoff {WINDOW_START[:10]}..2026-09-24, undisputed)")
    diag = {"rows": 0, "ah_quarter_rows_dropped": 0, "ladder_flagged_pairs": 0, "board_flagged_pairs": 0,
            "board_flagged_by_book": {}, "quotes_with_consensus": 0, "quotes_by_unit_kind": {}}
    cands, closes, metas = [], [], []
    for i in range(0, len(ids), CHUNK):
        rows = load_chunk(ids[i:i + CHUNK])
        diag["rows"] += len(rows)
        c, cl, meta = process_chunk(rows, diag)
        if len(c):
            cands.append(c)
        if len(cl):
            closes.append(cl)
        print(f"  chunk {i // CHUNK + 1}/{math.ceil(len(ids) / CHUNK)}: {len(rows):,} rows, "
              f"{len(c):,} candidates, {time.time() - t0:.0f}s", flush=True)
    C = pd.concat(cands, ignore_index=True)
    CL = pd.concat(closes, ignore_index=True)
    # Pinnacle close (native; DC/DNB derived from Pinnacle 1X2)
    pin = CL[CL.bookmaker.isin(["Pinnacle", "Pinnacle*1x2"])]
    pin = pin[~((pin.mkey == "draw_no_bet") & (pin.bookmaker == "Pinnacle"))]   # (none exists anyway)
    pin = pin.drop_duplicates(["match_id", "mkey", "selection"]).rename(columns={"p": "p_pin_close"})
    C = C.merge(pin[["match_id", "mkey", "selection", "p_pin_close"]], on=["match_id", "mkey", "selection"], how="left")
    # LOO consensus close (>= 4 other books; derived DNB rows excluded, DC rows are 1X2-derived per book)
    cc = CL[~CL.bookmaker.str.endswith("*1x2")]
    agg = cc.groupby(["match_id", "mkey", "selection"]).p.agg(["sum", "count"]).reset_index()
    own = cc.rename(columns={"p": "p_own"})
    C = C.merge(agg, on=["match_id", "mkey", "selection"], how="left")
    C = C.merge(own[["match_id", "mkey", "selection", "bookmaker", "p_own"]],
                on=["match_id", "mkey", "selection", "bookmaker"], how="left")
    o = C.p_own.fillna(0)
    k = C["count"].fillna(0) - C.p_own.notna()
    C["p_cons_close"] = np.where(k >= MIN_OTHERS, (C["sum"].fillna(0) - o) / k.replace(0, np.nan), np.nan)
    C["clv_pin"] = C.odds * C.p_pin_close - 1
    C["clv_cons"] = C.odds * C.p_cons_close - 1
    # results
    res = pd.DataFrame(q("""
        SELECT m.id::text match_id, m.status, m.score_home::float8 gh, m.score_away::float8 ga,
               m.ht_score_home::float8 hh, m.ht_score_away::float8 ha,
               s.corners_home::float8 ch, s.corners_away::float8 ca
          FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
         WHERE m.date >= %s AND m.date < %s""", (WINDOW_START, WINDOW_END)))
    res = res.drop_duplicates("match_id")
    C["won"] = settle(C[["match_id", "family", "selection", "line", "market", "ahl"]], res)
    C["pnl"] = np.where(C.won == 0.5, 0.0, np.where(C.won == 1, C.odds - 1, np.where(C.won == 0, -1.0, np.nan)))
    C["unit"] = [unit_of(f, m, l if f != "AH" else a) for f, m, l, a in zip(C.family, C.market, C.line, C.ahl)]
    C["source"] = np.where(C.bookmaker.isin(DIRECT), "direct", "af")
    C.to_csv(OUT_DIR / "candidates.csv.gz", index=False)
    # NULL: every opening quote (all EV), CLV vs the same Pinnacle close, by family x source x EV band.
    # If CLV tracks EV from negative to positive, the consensus predicts the close; the null row
    # (all quotes) is what "just bet this book's opening board" earns.
    N = pd.concat(NULLQ, ignore_index=True)
    N = N[N.odds <= ODDS_MAX].merge(pin[["match_id", "mkey", "selection", "p_pin_close"]],
                                    on=["match_id", "mkey", "selection"], how="inner")
    N["clv_pin"] = N.odds * N.p_pin_close - 1
    N["source"] = np.where(N.bookmaker.isin(DIRECT), "direct", "af")
    N["ev_band"] = pd.cut(N.ev_shin, [-9, -0.05, 0, 0.03, 0.05, 0.08, 0.25, 99],
                          labels=["<-5%", "-5..0", "0..3", "3..5", "5..8", "8..25", ">25"]).astype(str)
    nt = N.groupby(["family", "source", "ev_band"]).agg(n=("clv_pin", "size"), clv_mean=("clv_pin", "mean")).reset_index()
    na = N.groupby(["family", "source"]).agg(n=("clv_pin", "size"), clv_mean=("clv_pin", "mean")).reset_index()
    na["ev_band"] = "ALL"
    pd.concat([nt, na]).to_csv(OUT_DIR / "null_ev_clv_curve.csv", index=False)
    CONN.close()

    rng = np.random.default_rng(SEED)
    rows = []
    for method in METHODS:
        for thr in EV_THRESHOLDS:
            per = {}
            for bs in ("all", "af", "direct"):
                P = picks_for(C, method, thr, bs)
                for u, g in P.groupby("unit"):
                    per.setdefault(u, {})[bs] = g
            for u, d in per.items():
                g = d.get("all", pd.DataFrame(columns=C.columns))
                r = {"method": method, "ev_min": thr, "unit": u, "n": len(g),
                     "per_week": len(g) * 7 / WINDOW_DAYS,
                     "pin_close_share": float(g.p_pin_close.notna().mean()) if len(g) else None,
                     "mean_ev": float(g[f"ev_{method}"].mean()) if len(g) else None,
                     "mean_odds": float(g.odds.mean()) if len(g) else None}
                cl = g.match_id.to_numpy()
                for tag, col in (("clv", "clv_pin"), ("clvcons", "clv_cons"), ("roi", "pnl")):
                    s = cluster_stats(g[col].to_numpy(float), cl, rng) if len(g) else {"n": 0}
                    for kk, v in s.items():
                        r[f"{tag}_{kk}"] = v
                late = g[g.ko >= FULL_HISTORY_FROM]
                s = cluster_stats(late.clv_pin.to_numpy(float), late.match_id.to_numpy(), rng) if len(late) else {"n": 0}
                r["clv_late_n"], r["clv_late_mean"] = s.get("n", 0), s.get("mean")
                for bs in ("af", "direct"):
                    gb = d.get(bs)
                    s = cluster_stats(gb.clv_pin.to_numpy(float), gb.match_id.to_numpy(), rng) if gb is not None and len(gb) else {"n": 0}
                    r[f"{bs}_n"] = 0 if gb is None else len(gb)
                    r[f"{bs}_clv_n"], r[f"{bs}_clv_mean"] = s.get("n", 0), s.get("mean")
                    r[f"{bs}_clv_lo"], r[f"{bs}_clv_hi"] = s.get("lo"), s.get("hi")
                rows.append(r)
    T = pd.DataFrame(rows)
    T["holm_p"] = None
    for (method, thr), g in T.groupby(["method", "ev_min"]):
        fam = g[(g.unit != "1X2 (calibration)") & (g.clv_n >= 10)]
        h = holm(dict(zip(fam.index, fam.clv_p)))
        for idx, v in h.items():
            T.loc[idx, "holm_p"] = v
        T.loc[g.index, "holm_m"] = len(fam)
    T.to_csv(OUT_DIR / "scan_table.csv", index=False)
    # per-book view of the candidates (data-quality: a book that is ALWAYS the outlier)
    bk = C[C.ev_shin >= 0.05].groupby(["family", "bookmaker"]).agg(
        n=("odds", "size"), clv_mean=("clv_pin", "mean"), clv_n=("clv_pin", "count")).reset_index()
    bk.to_csv(OUT_DIR / "outliers_by_book_ev5.csv", index=False)
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "window": [WINDOW_START, WINDOW_END],
               "matches": len(ids), "diag": diag, "candidates": int(len(C)), "runtime_s": time.time() - t0,
               "params": {"ev_thresholds": EV_THRESHOLDS, "ev_cap": EV_CAP, "odds_max": ODDS_MAX,
                          "min_others": MIN_OTHERS, "state_max_age_s": STATE_MAX_AGE_S,
                          "close_max_before_ko_s": CLOSE_MAX_BEFORE_KO_S, "board_dev": BOARD_DEV,
                          "n_boot": N_BOOT, "seed": SEED}}
    (OUT_DIR / "scan_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    show = T[T.method == "shin"].copy()
    cols = ["ev_min", "unit", "n", "per_week", "clv_n", "clv_mean", "clv_lo", "clv_hi", "clv_p", "holm_p",
            "pin_close_share", "roi_n", "roi_mean", "roi_lo", "roi_hi", "af_clv_n", "af_clv_mean",
            "direct_clv_n", "direct_clv_mean", "clvcons_n", "clvcons_mean"]
    with pd.option_context("display.width", 320, "display.max_columns", 40, "display.max_rows", 500,
                           "display.float_format", "{:.4f}".format):
        print(show.sort_values(["ev_min", "unit"])[cols].to_string(index=False))
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
