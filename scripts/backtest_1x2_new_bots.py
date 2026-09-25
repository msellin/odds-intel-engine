#!/usr/bin/env python3
"""HONEST BACKTEST of the three 1X2 paper bots ([[#141]] step B).

    bot_v10_1x2          baseline — served `predictions` ensemble 1X2 -> calibrate_prob
    bot_rating_1x2_v1    "NEW"    — rating model r1x2_d8plus_v1, used as is
    bot_combined_1x2_v1  "NEW+"   — combined model r1x2_comb_v1, used as is

PRE-REGISTERED RULES (owner, 2026-09-24 — do not change):
  * window FIXED at kickoff dates 2026-08-31 .. 2026-09-24 — the only
    out-of-sample window; no other windows, no sub-period chosen by result;
  * bet price = OPENING price (old odds history keeps only opening + latest
    pre-kickoff after 7 days) at the book set the live bot prices from
    (`is_publishable_book` + the ODDS-OUTLIER-FILTER, as in _load_today_from_db);
    every odds row bounded by `timestamp < matches.date`;
  * CLV vs Pinnacle CLOSE de-vigged (Shin, workers.model.devig) for every pick;
  * model probabilities are what was knowable before kickoff:
      - NEW  : D8+ logit fitted on gated rows with kickoff < 2026-08-31 over the
               walk-forward rating features (research cache, leak-guarded);
      - NEW+ : production combiner (workers.model.combined_1x2.fit/predict) fitted
               on 2026-05-01..2026-08-30 with OPENING legs, rating input fitted
               <= 2026-04-30 — exactly round 3b's OPEN / COMB-HYB confirm run;
      - base : stored `predictions` rows (source='ensemble', production version,
               not shadow) created before kickoff;
  * every pick is reported; ROI on ~3.5 weeks is noise, CLV is the readout.

READ-ONLY against the DB: the connection is opened with readonly=True, and the
two pipeline helpers that would reach the DB themselves (improvements.* via
execute_query, supabase_client._bet_veto_reason via get_conn) are served from
in-memory, as-of data or the same read-only connection. Nothing is written to
simulated_bets or any other table. Output goes to
data/models/_research/1x2/backtest/ (gitignored).

    python3 scripts/backtest_1x2_new_bots.py          # B  (three bots, bot rules)
    python3 scripts/backtest_1x2_new_bots.py --b2     # B2 (NEW+ only, pre-registered EV arms N1-N4)
    python3 scripts/backtest_1x2_new_bots.py --b3     # B3 (exploratory grid, split + Holm m=10 + Hansen SPA)
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# ── fixed, pre-registered window (kickoff dates, UTC, inclusive) ──────────────
WINDOW_START = "2026-08-31"
WINDOW_END = "2026-09-24"
WINDOW_START_EPOCH = 1788134400.0        # 2026-08-31T00:00:00Z
WINDOW_END_EPOCH = 1790294400.0          # 2026-09-25T00:00:00Z (exclusive)
NEW_TRAIN_CUT_EPOCH = WINDOW_START_EPOCH  # NEW rating logit sees kickoff < window start only
COMB_FIT_FROM = "2026-05-01"
COMB_FIT_TO = "2026-08-31"               # exclusive
OUT_DIR = ROOT / "data" / "models" / "_research" / "1x2" / "backtest"
BOTS = ("bot_v10_1x2", "bot_rating_1x2_v1", "bot_combined_1x2_v1")
LABEL = {"bot_v10_1x2": "baseline", "bot_rating_1x2_v1": "NEW", "bot_combined_1x2_v1": "NEW+"}
FLAT_STAKE = 1.0
BANKROLL = 1000.0                        # pipeline default; only feeds the stake-floor gate
N_BOOT = 5000

# Production env for the gates, as read from /opt/odds-intel-engine/.env on
# 2026-09-24. Applied BEFORE and AFTER the imports below (improvements.py calls
# load_dotenv, which would otherwise pull the local .env's values in).
PROD_ENV_SET = {
    "LEAGUE_EFF_EDGE_BUMP_ENABLED": "true",
    "META_B_ML3_ENABLED": "false",
    "VETO_TIER_MAX": "3",
    "VETO_EDGE_CAP": "0.25",
    "ANCHOR_GAP_MID_BAND_ENABLED": "true",
    # VPS says isotonic, but the active bundle (v20260712) ships no isotonic_*.pkl,
    # so every call falls back to Platt there. Force the effective behaviour here so
    # a local bundle directory cannot change the answer.
    "STAGE2_CALIBRATOR": "platt",
}
PROD_ENV_UNSET = ("CAL_ALPHA_ODDS_V2_ENABLED", "ELITE_LEAGUE_FILTER_ENABLED", "BOT_COHORT_OVERRIDES")


def _apply_prod_env() -> None:
    os.environ.update(PROD_ENV_SET)
    for k in PROD_ENV_UNSET:
        os.environ.pop(k, None)


_apply_prod_env()
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
_apply_prod_env()

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, is_publishable_book  # noqa: E402
import workers.model.improvements as IMP  # noqa: E402
from workers.model.improvements import (  # noqa: E402
    calibrate_prob, compute_odds_movement, compute_alignment, compute_kelly, compute_stake,
)
from workers.model import meta_b_ml3  # noqa: E402
import workers.api_clients.supabase_client as SC  # noqa: E402
from workers.model.devig import devig  # noqa: E402
from workers.model.combined_1x2 import fit as comb_fit, predict as comb_predict  # noqa: E402
_apply_prod_env()
meta_b_ml3.META_B_ML3_ENABLED = False     # module reads the env at import time

# Pipeline constants copied from run_morning / _load_today_from_db. They are
# locals there (not importable); `scripts/smoke_test.py` BACKTEST-1X2-NEW-BOTS
# pins each one against the pipeline source so they cannot drift silently.
PINNACLE_VETO_GAP = 0.12
DATA_TIER_EDGE_BUMP = {"A": 0.00, "B": 0.02, "C": 0.08}
ALN_BUMP = {"LOW": 0.01, "MEDIUM": 0.0, "HIGH": 0.0, "NONE": 0.0}
OUTLIER_MULT_1X2 = 1.25
OUTLIER_MIN_BOOKS = 3
SHARP_BMS = {"Pinnacle"}
SOFT_BMS = {"Bwin", "Unibet", "Sportingbet", "Betway", "NordicBet", "10Bet", "1xBet"}

CACHE = ROOT / "data" / "models" / "_research" / "1x2"
SEL = ("home", "draw", "away")


# ───────────────────────────── DB (read-only) ─────────────────────────────
def _conn():
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.set_session(readonly=True, autocommit=True)
    with c.cursor() as cur:
        cur.execute("SET statement_timeout='900s'")
    return c


CONN = None


def _q(sql: str, params=None) -> list[dict]:
    with CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


@contextlib.contextmanager
def _ro_get_conn(*_a, **_k):
    """Stand-in for supabase_client.get_conn: hands out the read-only connection."""
    yield CONN


def _ep(ts) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def _iso(e: float | None) -> str | None:
    return None if e is None else datetime.fromtimestamp(e, timezone.utc).isoformat()


# ───────────────────────────── data pulls ─────────────────────────────
def load_matches() -> dict[str, dict]:
    rows = _q("""
        SELECT m.id::text match_id, extract(epoch FROM m.date)::float8 ko, m.status,
               m.score_home gh, m.score_away ga, m.date_disputed_at IS NOT NULL disputed,
               extract(epoch FROM m.lineups_fetched_at)::float8 lineups_ep,
               l.tier raw_tier, l.country, l.name league_name,
               th.name home_team, ta.name away_team
          FROM matches m
          LEFT JOIN leagues l ON l.id = m.league_id
          LEFT JOIN teams th ON th.id = m.home_team_id
          LEFT JOIN teams ta ON ta.id = m.away_team_id
         WHERE m.date >= %s AND m.date < %s""", (f"{WINDOW_START}T00:00:00Z", _iso(WINDOW_END_EPOCH)))
    return {r["match_id"]: r for r in rows}


def load_odds(ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Per (match, book, selection): the EARLIEST pre-kickoff non-live 1X2 row
    (= opening), and Pinnacle's LATEST pre-kickoff non-live row (= close)."""
    opens, closes = [], []
    for i in range(0, len(ids), 2000):
        chunk = ids[i:i + 2000]
        opens += _q("""
            SELECT DISTINCT ON (o.match_id, o.bookmaker, o.selection)
                   o.match_id::text match_id, o.bookmaker, o.selection, o.odds::float8 odds,
                   extract(epoch FROM o."timestamp")::float8 ts, o.is_opening
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = '1x2'
               AND o.is_live IS NOT TRUE AND o.is_closing IS NOT TRUE
               AND o.odds > 1.01 AND o."timestamp" < m.date
             ORDER BY o.match_id, o.bookmaker, o.selection, o."timestamp" ASC""", (chunk,))
        closes += _q("""
            SELECT DISTINCT ON (o.match_id, o.selection)
                   o.match_id::text match_id, o.selection, o.odds::float8 odds,
                   extract(epoch FROM o."timestamp")::float8 ts
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = '1x2' AND o.bookmaker = 'Pinnacle'
               AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date
             ORDER BY o.match_id, o.selection, o."timestamp" DESC""", (chunk,))
    return opens, closes


def load_baseline_preds(ids: list[str]) -> dict[str, dict]:
    """Production (non-shadow) ensemble 1X2 rows created before kickoff. Where a
    match carries more than one production version (388 matches: the 08-23 -> 08-30
    bundle switch, and a v14 episode 09-15..09-21), the version first written most
    recently is taken — it is the one production was serving at the end."""
    rows = _q("""
        SELECT p.match_id::text match_id, p.market, p.model_version, p.model_probability::float8 p,
               p.reasoning, extract(epoch FROM p.created_at)::float8 created
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.match_id = ANY(%s::uuid[]) AND p.source = 'ensemble'
           AND p.market IN ('1x2_home','1x2_draw','1x2_away')
           AND coalesce(p.reasoning, '') NOT LIKE '%%shadow=%%'
           AND p.created_at < m.date""", (ids,))
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by[r["match_id"]][r["model_version"]][r["market"]] = r
    out = {}
    for mid, vers in by.items():
        best = max(vers.items(), key=lambda kv: max(x["created"] for x in kv[1].values()))
        ver, mk = best
        tier = None
        for x in mk.values():
            rs = x.get("reasoning") or ""
            if "data_tier=" in rs:
                tier = rs.split("data_tier=")[1][:1]
        out[mid] = {"version": ver, "data_tier": tier or "A",
                    "home_prob": mk.get("1x2_home", {}).get("p"),
                    "draw_prob": mk.get("1x2_draw", {}).get("p"),
                    "away_prob": mk.get("1x2_away", {}).get("p")}
    if UNSWAP_065:
        SWAP_STATS.update(_unswap_xgb_leg(out, ids))
    return out


# #065 / 1X2-CLASS-ORDER-INVERTED (fixed in 50ec7347, 2026-09-14 07:08 UTC): the XGBoost
# 1X2 leg was served home/away SWAPPED from 2026-05-10. Measured on this window (B3
# check): stored source='xgboost' home prob vs Pinnacle's opening implied home is
# corr -0.134 on kickoffs before the fix, +0.625 after. The stored ENSEMBLE is
# pw*Poisson + xw*XGB (one weight for all three outcomes; tier-dependent), so it
# carries the swap only through the XGB leg (~0.16 weight) and still correlates
# positively with Pinnacle (0.27). Where both legs are stored, xw is solved per match
# by least squares over the three outcomes and the ensemble is rebuilt with the XGB
# home/away put back. Matches with no stored XGB leg were Poisson-only (ensemble ==
# Poisson on 99.6%) and are unaffected. The rows are the LAST pre-kickoff write, so
# kickoff before the fix commit = a pre-fix write.
UNSWAP_065 = True
SWAP_FIX_EPOCH = 1789369728.0             # 2026-09-14T07:08:48Z, commit 50ec7347
SWAP_STATS: dict = {}


def _unswap_xgb_leg(out: dict, ids: list[str]) -> dict:
    rows = _q("""
        SELECT p.match_id::text match_id, p.source, p.market, p.model_version,
               p.model_probability::float8 p, extract(epoch FROM m.date)::float8 ko
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.match_id = ANY(%s::uuid[]) AND p.source IN ('xgboost', 'poisson')
           AND p.market IN ('1x2_home','1x2_draw','1x2_away')
           AND coalesce(p.reasoning, '') NOT LIKE '%%shadow=%%'
           AND p.created_at < m.date AND m.date < to_timestamp(%s)""", (ids, SWAP_FIX_EPOCH))
    xgb, poi = defaultdict(dict), defaultdict(dict)
    for r in rows:
        if r["source"] == "xgboost":
            if r["model_version"] == out.get(r["match_id"], {}).get("version"):
                xgb[r["match_id"]][r["market"][4:]] = r["p"]
        else:
            poi[r["match_id"]][r["market"][4:]] = r["p"]
    fixed = skipped = 0
    for mid, x in xgb.items():
        o, pz = out.get(mid), poi.get(mid, {})
        if not o or not all(k in x and k in pz and o[f"{k}_prob"] is not None for k in SEL):
            continue
        E = np.array([o[f"{k}_prob"] for k in SEL]); P = np.array([pz[k] for k in SEL]); X = np.array([x[k] for k in SEL])
        den = float(((X - P) ** 2).sum())
        if den < 1e-9:
            continue
        xw = float(((E - P) * (X - P)).sum() / den)
        if not (0.0 <= xw <= 1.0) or np.abs(P + xw * (X - P) - E).max() > 0.01:
            skipped += 1
            continue
        Xu = X[[2, 1, 0]]                               # put home/away back
        Eu = P + xw * (Xu - P)
        o["home_prob"], o["draw_prob"], o["away_prob"] = map(float, Eu)
        o["unswapped_065"] = True
        fixed += 1
    return {"matches_ensemble_rebuilt": fixed, "matches_skipped_blend_not_linear": skipped,
            "matches_prefix_with_xgb_leg": len(xgb)}


def load_asof_side_data(ids: list[str]) -> tuple[dict, dict]:
    eff = defaultdict(list)
    for r in _q("""SELECT match_id::text match_id, signal_value::float8 v,
                          extract(epoch FROM captured_at)::float8 ts
                     FROM match_signals WHERE match_id = ANY(%s::uuid[])
                      AND signal_name = 'league_clv_efficiency'""", (ids,)):
        eff[r["match_id"]].append((r["ts"], r["v"]))
    news = defaultdict(list)
    for r in _q("""SELECT match_id::text match_id, impact_type::text impact_type,
                          impact_magnitude::float8 impact_magnitude,
                          extract(epoch FROM detected_at)::float8 ts
                     FROM news_events WHERE match_id = ANY(%s::uuid[])""", (ids,)):
        news[r["match_id"]].append(r)
    return eff, news


def load_calibration() -> list[tuple[float, str, float, float, float | None]]:
    rows = _q("""SELECT market, platt_a::float8 a, platt_b::float8 b, platt_c::float8 c,
                        extract(epoch FROM fitted_at)::float8 ts
                   FROM model_calibration
                  WHERE market LIKE '1x2%%' OR market LIKE 'shrinkage_alpha_%%'
                  ORDER BY fitted_at""")
    return [(r["ts"], r["market"], r["a"], r["b"], r["c"]) for r in rows]


# ───────────────────────────── walk-forward probabilities ─────────────────────────────
def walk_forward_probs() -> tuple[dict, dict, dict]:
    """NEW and NEW+ probabilities for window matches, from the leak-guarded research
    cache. Returns ({mid: (h,d,a)} for NEW gated rows, same for NEW+ gated rows,
    diagnostics)."""
    import ab_1x2_rating_arms as A
    import ab_1x2_combined as C
    f = pd.read_parquet(CACHE / "features_hist_full.parquet")
    f = f[~f["extra"].astype(bool)].copy()
    f["ko"] = f["kickoff"].astype("int64") / 1e6        # datetime64[us] -> epoch s (no tz ops)
    g = A.gated(f).to_numpy()
    ko = f["ko"].to_numpy()
    te_mask = (ko >= WINDOW_START_EPOCH) & (ko < WINDOW_END_EPOCH)
    tr_mask = (ko >= 1656633600.0) & (ko < NEW_TRAIN_CUT_EPOCH) & g
    te = f[te_mask].reset_index(drop=True)
    R = A.fit_predict("D8+", f[tr_mask], te)
    te_g = A.gated(te).to_numpy()
    new = {mid: tuple(map(float, p)) for mid, p, ok in zip(te.match_id, R, te_g) if ok}
    y = te["y"].to_numpy()
    diag = {"new_window_rows": int(len(te)), "new_gated": int(te_g.sum()), "new_train_rows": int(tr_mask.sum()),
            "new_ll_gated": float(A.per_match_ll(R[te_g], y[te_g]).mean())}

    # NEW+: round 3b's OPEN build (rating fitted <= 2026-04-30, opening legs, AF),
    # combiner = production fit/predict on 2026-05-01..2026-08-30.
    d = C.build("open")
    dko = d["kickoff"].astype("int64").to_numpy() / 1e6
    fit_mask = (dko >= 1777593600.0) & (dko < WINDOW_START_EPOCH)
    win_mask = (dko >= WINDOW_START_EPOCH) & (dko < WINDOW_END_EPOCH)
    params = comb_fit(d[fit_mask])
    dw = d[win_mask].reset_index(drop=True)
    P, grp = comb_predict(dw, params)
    gg = A.gated(dw).to_numpy() | (grp != "none")
    comb = {mid: tuple(map(float, p)) for mid, p, ok in zip(dw.match_id, P, gg) if ok}
    yw = dw["y"].to_numpy()
    diag.update(comb_fit_rows=int(fit_mask.sum()), comb_window_rows=int(len(dw)), comb_gated=int(gg.sum()),
                comb_ll_all=float(A.per_match_ll(P, yw).mean()),
                comb_ll_note="round3b open_confirm COMB-HYB was 0.97918 on 12,640 rows",
                comb_groups={k: int(v) for k, v in Counter(grp).items()},
                cache_last_kickoff=_iso(float(ko.max())))
    return new, comb, diag


# ───────────────────────────── price basis (mirror of _load_today_from_db) ──────────
def price_basis(opens: list[dict], allowed=None) -> dict[str, dict]:
    """Per match: best publishable OPENING price per selection after the
    ODDS-OUTLIER-FILTER (anchor = Pinnacle's opening price, else median of >= 3
    publishable books, Marathonbet counted once when 1xBet is present)."""
    from statistics import median
    by = defaultdict(lambda: defaultdict(list))          # mid -> sel -> [(book, odds, ts)]
    for r in opens:
        by[r["match_id"]][r["selection"]].append((r["bookmaker"], r["odds"], r["ts"]))
    out = {}
    for mid, sels in by.items():
        m = {"best": {}, "book": {}, "ts": {}, "pin_open": {}, "earliest": {}, "soft": {}}
        for sel, offers in sels.items():
            m["earliest"][sel] = min(offers, key=lambda x: x[2])      # compute_odds_movement anchor
            for b, o, t in offers:
                if b == "Pinnacle":
                    m["pin_open"][sel] = (o, t)
                if b in SOFT_BMS:
                    m["soft"].setdefault(sel, []).append(1.0 / o)
            pub = []
            for b, o, t in offers:
                if not is_publishable_book(b):
                    continue
                if b == "Marathonbet" and any(x[0] == "1xBet" for x in pub):
                    continue
                if b == "1xBet":
                    pub = [x for x in pub if x[0] != "Marathonbet"]
                pub.append((b, o, t))
            pin = next((o for b, o, _ in pub if b == "Pinnacle"), None)
            anchor = pin if pin is not None else (median(o for _, o, _ in pub) if len(pub) >= OUTLIER_MIN_BOOKS else None)
            if anchor is None:
                continue
            # the price loop itself does NOT dedupe Marathonbet (only the anchor does)
            cand = [(b, o, t) for b, o, t in offers if is_publishable_book(b)
                    and (allowed is None or allowed(b))
                    and (b == "Pinnacle" or o <= anchor * OUTLIER_MULT_1X2)]
            if not cand:
                continue
            b, o, t = max(cand, key=lambda x: x[1])
            m["best"][sel], m["book"][sel], m["ts"][sel] = o, b, t
        out[mid] = m
    return out


def pinnacle_implied(pin_open: dict) -> dict:
    """match_signals pinnacle_implied_{sel}: raw 1/odds / sum over available selections."""
    raws = {s: 1.0 / o for s, (o, _) in pin_open.items()}
    tot = sum(raws.values())
    return {s: v / tot for s, v in raws.items()} if tot > 0 else {}


def sharp_consensus_home(m: dict) -> float | None:
    pin = m["pin_open"].get("home")
    soft = m["soft"].get("home", [])
    if pin is None or len(soft) < 2:
        return None
    return round(1.0 / pin[0] - sum(soft) / len(soft), 5)


# ───────────────────────────── as-of shims for the imported helpers ─────────────────
class Ctx:
    mid: str = ""
    t_dec: float = 0.0
    earliest: dict = {}
    news: list = []
    lineups_ep: float | None = None
    sharp_home: float | None = None
    pin_home: float | None = None


CTX = Ctx()
UNEXPECTED_SQL: Counter = Counter()


def _fake_execute_query(sql: str, params=None) -> list[dict]:
    """Serves improvements.py's per-candidate reads from AS-OF data (<= decision time)."""
    s = " ".join(sql.split())
    if "FROM odds_snapshots" in s:
        sel = params[2]
        e = CTX.earliest.get(sel)
        if not e:
            return []
        row = {"odds": e[1], "timestamp": datetime.fromtimestamp(e[2], timezone.utc), "minutes_to_kickoff": None}
        return [row, row]            # only [0] and len>=2 are read
    if "FROM news_events" in s:
        return [n for n in CTX.news if n["ts"] is not None and n["ts"] <= CTX.t_dec]
    if "lineups_fetched_at" in s:
        return [{"id": CTX.mid}] if CTX.lineups_ep is not None and CTX.lineups_ep <= CTX.t_dec else []
    if "sharp_consensus_home" in s:
        return [] if CTX.sharp_home is None else [{"signal_value": CTX.sharp_home}]
    if "pinnacle_implied_home" in s:
        return [] if CTX.pin_home is None else [{"signal_value": CTX.pin_home}]
    UNEXPECTED_SQL[s[:80]] += 1
    return []


class CalibrationAsOf:
    """Sets improvements' Platt / shrinkage caches to the fits that existed at t."""

    def __init__(self, fits):
        self.fits = fits
        self._key = None

    def set(self, t: float) -> None:
        idx = int(np.searchsorted([f[0] for f in self.fits], t, side="right"))
        if idx == self._key:
            return
        self._key = idx
        platt, alpha = {}, {}
        for ts, mkt, a, b, c in self.fits[:idx]:        # ascending -> later overwrites earlier
            if mkt.startswith("shrinkage_alpha_"):
                alpha[mkt] = a
            else:
                platt[mkt] = (a, b, c)
        IMP._platt_params = platt
        IMP._shrinkage_alphas = alpha


# ───────────────────────────── the funnel ─────────────────────────────
def run_funnel(matches, basis, base_preds, new, comb, eff, news, calib):
    IMP.execute_query = _fake_execute_query
    SC.get_conn = _ro_get_conn
    picks, funnel = [], {b: Counter() for b in BOTS}
    null_rows = []
    for mid, mt in matches.items():
        if mt["status"] != "finished" or mt["gh"] is None or mt["ga"] is None:
            continue
        m = basis.get(mid)
        if not m or not m["best"]:
            continue
        tier = int(mt["raw_tier"] or 1)
        country, league_name = mt["country"] or "", mt["league_name"] or ""
        t_dec = max(m["ts"].values())
        pin_imp = pinnacle_implied(m["pin_open"])
        CTX.mid, CTX.t_dec, CTX.earliest = mid, t_dec, m["earliest"]
        CTX.news, CTX.lineups_ep = news.get(mid, []), mt["lineups_ep"]
        CTX.sharp_home, CTX.pin_home = sharp_consensus_home(m), pin_imp.get("home")
        calib.set(t_dec)
        e_rows = [v for ts, v in sorted(eff.get(mid, [])) if ts <= t_dec]
        league_eff = e_rows[-1] if e_rows else None
        result = "home" if mt["gh"] > mt["ga"] else ("draw" if mt["gh"] == mt["ga"] else "away")
        pc = m.get("pin_close")
        for sel in SEL:
            if sel in m["best"] and 1.30 <= m["best"][sel] <= 4.50:
                null_rows.append((m["best"][sel], m["best"][sel] * pc[SEL.index(sel)] - 1 if pc else None))
        if country == "Scotland" and league_name == "Premiership":
            for b in BOTS:
                funnel[b]["skip_scottish_prem"] += 1
            continue
        bp = base_preds.get(mid)
        data_tier = bp["data_tier"] if bp else None
        for bot in BOTS:
            cfg = BOTS_CONFIG[bot]
            fn = funnel[bot]
            rating_bot = cfg.get("prob_source") in ("rating_1x2", "combined_1x2")
            if rating_bot:
                rr = (comb if cfg["prob_source"] == "combined_1x2" else new).get(mid)
                if rr is None:
                    fn["drop_no_rating"] += 1
                    continue
                p1x2 = {"home": rr[0], "draw": rr[1], "away": rr[2]}
            else:
                if not bp or any(bp[f"{s}_prob"] is None for s in SEL):
                    fn["drop_no_stored_prediction"] += 1
                    continue
                p1x2 = {s: bp[f"{s}_prob"] for s in SEL}
            fn["matches_evaluated"] += 1
            thresholds = dict(cfg["edge_thresholds"].get(tier, {}))
            if tier >= 3 and thresholds:
                thresholds = {k: v + (0.05 if k.startswith("1x2") else 0.03) for k, v in thresholds.items()}
            odds_min, odds_max = cfg["odds_range"]
            edge_bump = DATA_TIER_EDGE_BUMP.get(data_tier or "A", 0.0)
            for sel in SEL:
                odds = m["best"].get(sel, 0)
                if not odds or odds <= 0:
                    continue
                if sel == "home":
                    base_thr = thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08)
                else:
                    base_thr = thresholds.get("1x2_long", 0.08)
                fn["candidates"] += 1
                raw = p1x2[sel]
                ip = 1 / odds
                if raw is None or math.isnan(raw):
                    fn["drop_nan_raw"] += 1
                    continue
                pin_anchor = pin_imp.get(sel)
                cal = raw if rating_bot else calibrate_prob(raw, ip, tier=tier, market=f"1x2_{sel}",
                                                            anchor_implied=pin_anchor, odds=odds)
                if math.isnan(cal):
                    fn["drop_nan_cal"] += 1
                    continue
                edge = cal - ip
                me = base_thr + (0.0 if rating_bot else edge_bump)
                if edge < me or odds < odds_min or odds > odds_max or cal < cfg["min_prob"]:
                    fn["drop_edge" if edge < me else "drop_odds_too_low" if odds < odds_min
                       else "drop_odds_too_high" if odds > odds_max else "drop_min_prob"] += 1
                    continue
                veto_anchor = pin_anchor if pin_anchor is not None else ip
                gap = cal - veto_anchor
                if (os.getenv("ANCHOR_GAP_MID_BAND_ENABLED", "true").lower() != "false"
                        and pin_anchor is not None and 0.06 <= gap < 0.10 and edge < me + 0.02):
                    fn["drop_pin_mid_band"] += 1
                    continue
                if gap > PINNACLE_VETO_GAP:
                    fn["drop_pin_veto"] += 1
                    continue
                if sel == "home" and CTX.sharp_home is not None and CTX.sharp_home < -0.02:
                    fn["drop_sharp_gate"] += 1
                    continue
                mv = compute_odds_movement(mid, "1x2", sel, odds)
                if mv["veto"]:
                    fn["drop_odds_mv"] += 1
                    continue
                kelly = compute_kelly(cal, odds)
                if kelly <= 0:
                    fn["drop_kelly_zero"] += 1
                    continue
                aln = compute_alignment(mid, sel.capitalize(), mv, {"tier": tier})
                aln_bump = ALN_BUMP.get(aln["alignment_class"], 0.0)
                eff_bump = 0.0
                if os.getenv("LEAGUE_EFF_EDGE_BUMP_ENABLED", "false").lower() in ("true", "1", "yes") \
                        and league_eff is not None:
                    eff_bump = -0.01 if league_eff >= 0.02 else (0.01 if league_eff <= -0.01 else 0.0)
                if edge < me + aln_bump + eff_bump:
                    fn["drop_league_eff_edge" if eff_bump > 0 and edge >= me + aln_bump else "drop_aln1"] += 1
                    continue
                stake_checked = data_tier is not None
                stake = compute_stake(kelly, BANKROLL, data_tier or "A", odds_penalty=mv.get("penalty", 0.0))
                if stake_checked and stake < 1.0:
                    fn["drop_stake_low"] += 1
                    continue
                if not meta_b_ml3.should_fire(None):          # gate OFF in production -> always fires
                    fn["drop_meta_b_ml3"] += 1
                    continue
                veto = SC._bet_veto_reason(mid, edge)
                if veto:
                    fn["drop_store_veto_" + veto.split("(")[0]] += 1
                    continue
                fn["accepted"] += 1
                won = result == sel
                p_close = pc[SEL.index(sel)] if pc else None
                picks.append({
                    "bot": bot, "label": LABEL[bot], "match_id": mid,
                    "kickoff_utc": _iso(mt["ko"]), "decision_time_utc": _iso(t_dec),
                    "league": f"{country} / {league_name}", "tier": tier,
                    "home_team": mt["home_team"], "away_team": mt["away_team"],
                    "selection": sel, "odds": odds, "bookmaker": m["book"][sel],
                    "price_ts_utc": _iso(m["ts"][sel]),
                    "raw_prob": round(raw, 5), "cal_prob": round(cal, 5), "edge": round(edge, 5),
                    "threshold": round(me + aln_bump + eff_bump, 4),
                    "pin_open_implied": None if pin_anchor is None else round(pin_anchor, 5),
                    "kelly": round(kelly, 6), "pipeline_stake_at_1000": stake if stake_checked else None,
                    "stake_gate_evaluated": stake_checked, "data_tier": data_tier,
                    "alignment_class": aln["alignment_class"], "league_clv_eff": league_eff,
                    "baseline_version": bp["version"] if bp else None,
                    "in_pipeline_universe": bp is not None,
                    "result": "win" if won else "loss",
                    "pnl_flat": round((odds - 1) * FLAT_STAKE if won else -FLAT_STAKE, 4),
                    "pin_close_p_devig": None if p_close is None else round(p_close, 5),
                    "clv_pin_devig": None if p_close is None else round(odds * p_close - 1, 5),
                })
    return picks, funnel, null_rows


# ───────────────────────────── statistics ─────────────────────────────
def _boot(x: np.ndarray, seed: int = 0) -> tuple[float, float, float] | None:
    if len(x) == 0:
        return None
    rng = np.random.default_rng(seed)
    bs = x[rng.integers(0, len(x), (N_BOOT, len(x)))].mean(1)
    return float(x.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def summarise(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    pnl = df["pnl_flat"].to_numpy(float)
    clv = df["clv_pin_devig"].dropna().to_numpy(float)
    roi = _boot(pnl)
    c = _boot(clv)
    return {"n": int(len(df)), "hit_rate": float((df.result == "win").mean()),
            "mean_odds": float(df.odds.mean()),
            "roi_flat": roi[0], "roi_ci95": [roi[1], roi[2]],
            "clv_n": int(len(clv)),
            "clv_mean": None if c is None else c[0], "clv_ci95": None if c is None else [c[1], c[2]],
            "clv_pos_share": None if len(clv) == 0 else float((clv > 0).mean())}


def breakdown(df: pd.DataFrame) -> dict:
    band = pd.cut(df.odds, [1.299, 1.999, 2.999, 4.5001], labels=["1.30-1.99", "2.00-2.99", "3.00-4.50"])
    out = {"all": summarise(df)}
    out["by_odds_band"] = {str(k): summarise(df[band == k]) for k in band.cat.categories}
    out["fav_vs_long"] = {"fav (odds<2.0)": summarise(df[df.odds < 2.0]),
                          "long (odds>=2.0)": summarise(df[df.odds >= 2.0])}
    out["by_selection"] = {s: summarise(df[df.selection == s]) for s in SEL}
    return out


NOT_REPLICATED = [
    {"gate": "meta gate (B-ML3 score_bet / should_fire)",
     "status": "not replicated as scoring; replicated as its production effect",
     "reason": "META_B_ML3_ENABLED=false on the VPS (checked 2026-09-24), so should_fire() returns True "
               "for every pick; the score itself needs MFV *_at_t6h columns that are NULL for any fixture "
               "that has not kicked off (META-SERVING-SKEW), so score_bet returns None live anyway."},
    {"gate": "odds staleness guard (ODDS_MAX_LAG_HOURS / ODDS_MAX_AGE_HOURS)",
     "status": "not replicated",
     "reason": "it acts on the LATEST quote per book at run time; the pre-registered bet price is the "
               "opening quote, for which 'age relative to peers' has no equivalent."},
    {"gate": "exposure cap (3rd+ bet in a league per run -> stake halved)",
     "status": "not replicated", "reason": "stake-only; results are reported at a flat 1-unit stake."},
    {"gate": "running bankroll", "status": "approximated",
     "reason": "the stake-floor gate (compute_stake < 1.0 -> drop) is evaluated at a constant 1000 bankroll; "
               "live it uses bots.current_bankroll net of the run's earlier stakes."},
    {"gate": "stake-floor gate for NEW / NEW+ on matches with no stored production prediction",
     "status": "not evaluable", "reason": "compute_stake needs the pipeline's data_tier, which only exists "
               "where the live pipeline processed the match; those picks carry stake_gate_evaluated=false."},
    {"gate": "timing (morning + :05/:35 refresh cohorts, one bet per bot/match/selection)",
     "status": "replaced by the pre-registered rule",
     "reason": "each match is evaluated ONCE at its opening prices; the live bots first see a fixture on "
               "match day and price the latest quote. Signals are taken as of the decision time "
               "(latest opening quote used for the match)."},
    {"gate": "PIN-CROSS-DRIFT veto", "status": "not applicable", "reason": "non-1X2 markets only."},
    {"gate": "ELITE_LEAGUE_FILTER, CAL_ALPHA_ODDS_V2", "status": "off in production (env unset) — off here", "reason": ""},
    {"gate": "date_disputed_at / status='scheduled' filters", "status": "replaced",
     "reason": "only finished matches with a score are settleable; postponed/void fixtures are excluded."},
    {"gate": "baseline probability timing", "status": "approximated",
     "reason": "predictions rows are upserted by every refresh, so the stored ensemble probability is the "
               "LAST pre-kickoff write, not the value at the opening price. Still pre-kickoff information."},
]


def load_everything() -> dict:
    """Shared loaders for B and B2 (read-only)."""
    global CONN
    CONN = _conn()
    # Belt and braces: anything else that reaches the shared pool helpers goes
    # through the read-only connection too.
    import workers.api_clients.db as DB
    DB.get_conn = _ro_get_conn
    DB.execute_query = _q
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"window {WINDOW_START}..{WINDOW_END} (kickoff, UTC)")
    matches = load_matches()
    ids = list(matches)
    print(f"  {len(ids):,} matches in window, {sum(m['status'] == 'finished' for m in matches.values()):,} finished")
    opens, closes = load_odds(ids)
    print(f"  {len(opens):,} opening legs, {len(closes):,} Pinnacle close legs")
    basis = price_basis(opens)
    pcl = defaultdict(dict)
    for r in closes:
        pcl[r["match_id"]][r["selection"]] = r["odds"]
    for mid, m in basis.items():
        c = pcl.get(mid, {})
        m["pin_close"] = devig([c["home"], c["draw"], c["away"]]) if all(s in c for s in SEL) else None
    base_preds = load_baseline_preds(ids)
    eff, news = load_asof_side_data(ids)
    calib = CalibrationAsOf(load_calibration())
    print(f"  {len(base_preds):,} matches with a stored production ensemble 1X2 prediction")
    new, comb, diag = walk_forward_probs()
    print(f"  walk-forward: NEW {len(new):,} gated / NEW+ {len(comb):,} gated; {diag}")
    open_flag_share = float(np.mean([bool(r["is_opening"]) for r in opens])) if opens else None
    return dict(matches=matches, basis=basis, base_preds=base_preds, eff=eff, news=news, calib=calib,
                new=new, comb=comb, diag=diag, open_flag_share=open_flag_share, opens=opens, pcl=pcl)


def main() -> int:
    L = load_everything()
    matches, basis, base_preds, eff, news, calib = (L[k] for k in ("matches", "basis", "base_preds", "eff", "news", "calib"))
    new, comb, diag, open_flag_share = L["new"], L["comb"], L["diag"], L["open_flag_share"]

    picks, funnel, null_rows = run_funnel(matches, basis, base_preds, new, comb, eff, news, calib)
    df = pd.DataFrame(picks)
    df.to_csv(OUT_DIR / "backtest_1x2_new_bots_picks.csv", index=False)

    # common universe: matches where all three bots had a probability
    ok = {b: set() for b in BOTS}
    for mid in matches:
        if base_preds.get(mid) and all(base_preds[mid][f"{s}_prob"] is not None for s in SEL):
            ok["bot_v10_1x2"].add(mid)
        if mid in new:
            ok["bot_rating_1x2_v1"].add(mid)
        if mid in comb:
            ok["bot_combined_1x2_v1"].add(mid)
    common = set.intersection(*ok.values())
    nul = np.array([c for _, c in null_rows if c is not None], float)
    nb = _boot(nul)
    nodds = np.array([o for o, c in null_rows if c is not None], float)
    null_bands = {}
    for tag, lo, hi in (("1.30-1.99", 1.30, 2.0), ("2.00-2.99", 2.0, 3.0), ("3.00-4.50", 3.0, 4.5001)):
        bb = _boot(nul[(nodds >= lo) & (nodds < hi)])
        null_bands[tag] = None if bb is None else {"n": int(((nodds >= lo) & (nodds < hi)).sum()),
                                                    "clv_mean": bb[0], "clv_ci95": [bb[1], bb[2]]}

    live = _q("""SELECT b.name bot, sb.match_id::text match_id, lower(sb.selection) selection,
                        sb.odds_at_pick::float8 odds
                   FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id JOIN matches m ON m.id = sb.match_id
                  WHERE b.name = ANY(%s) AND m.date >= %s AND m.date < %s""",
              (list(BOTS), f"{WINDOW_START}T00:00:00Z", _iso(WINDOW_END_EPOCH)))
    live_keys = {(r["bot"], r["match_id"], r["selection"]) for r in live}
    bt_keys = {(p["bot"], p["match_id"], p["selection"]) for p in picks}

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Backtest (simulated) — NOT live results",
        "window": {"start": WINDOW_START, "end_inclusive": WINDOW_END, "basis": "kickoff date UTC"},
        "rules": {"bet_price": "earliest pre-kickoff non-live 1X2 quote per book (opening), best across "
                               "is_publishable_book() books after the ODDS-OUTLIER-FILTER (x1.25 of the Pinnacle "
                               "opening price, else of the median of >= 3 publishable books)",
                  "clv": "odds * p_close - 1, p_close = Shin de-vig of Pinnacle's last pre-kickoff non-live 1X2 triple",
                  "stake": "flat 1 unit", "prod_env": PROD_ENV_SET,
                  "model_new": "D8+ logit, gated rows 2022-07-01 .. 2026-08-30 (research cache features)",
                  "model_new_plus": "combined_1x2.fit on 2026-05-01..2026-08-30 OPEN legs, rating input fitted <= 2026-04-30",
                  "model_baseline": "predictions source='ensemble' production (non-shadow) row created before kickoff, "
                                    "through calibrate_prob with model_calibration fits as of the decision time"},
        "readout_warning": "ROI over ~3.5 weeks is noise (a 3.0-odds pick has a per-bet sd of ~1.4 units); "
                           "read CLV first, and read it against the null baseline in the same odds band (every "
                           "selection at its best opening price), not against zero. A few large CLVs come from a "
                           "soft book's OPENING quote sitting far above Pinnacle's; whether such a quote was "
                           "actually takeable cannot be verified from stored rows.",
        "null_baseline_every_selection_at_best_open_1.30_4.50": {
            "n": int(len(nul)), "clv_mean": None if nb is None else nb[0],
            "clv_ci95": None if nb is None else [nb[1], nb[2]], "by_odds_band": null_bands},
        "universes": {"matches_in_window": len(matches), "with_opening_price": len(basis),
                      "baseline_has_prediction": len(ok["bot_v10_1x2"]), "new_gated": len(ok["bot_rating_1x2_v1"]),
                      "new_plus_gated": len(ok["bot_combined_1x2_v1"]), "common_all_three": len(common)},
        "per_bot": {}, "per_bot_common_universe": {}, "per_bot_pipeline_universe": {},
        "funnel": {b: dict(funnel[b]) for b in BOTS},
        "not_replicated": NOT_REPLICATED,
        "diagnostics": {**diag, "opening_legs_flagged_is_opening_share": open_flag_share,
                        "baseline_065_unswap": SWAP_STATS,
                        "unexpected_sql_in_shims": dict(UNEXPECTED_SQL),
                        "live_simulated_bets_in_window": {b: sum(r["bot"] == b for r in live) for b in BOTS},
                        "live_picks_also_in_backtest": {b: len({k for k in live_keys if k[0] == b} & bt_keys) for b in BOTS}},
    }
    for b in BOTS:
        sub = df[df.bot == b] if not df.empty else df
        summary["per_bot"][b] = {"label": LABEL[b], **breakdown(sub)} if len(sub) else {"label": LABEL[b], "all": {"n": 0}}
        sc = sub[sub.match_id.isin(common)] if len(sub) else sub
        summary["per_bot_common_universe"][b] = summarise(sc) if len(sc) else {"n": 0}
        sp = sub[sub.in_pipeline_universe] if len(sub) else sub
        summary["per_bot_pipeline_universe"][b] = summarise(sp) if len(sp) else {"n": 0}
    (OUT_DIR / "backtest_1x2_new_bots_summary.json").write_text(json.dumps(summary, indent=1, default=str))

    def line(tag, s):
        if not s or s.get("n", 0) == 0:
            return f"  {tag:34s} n=0"
        out = (f"  {tag:34s} n={s['n']:4d} hit {s['hit_rate']:.3f} odds {s['mean_odds']:.2f} "
               f"ROI {s['roi_flat']:+.3f} [{s['roi_ci95'][0]:+.3f},{s['roi_ci95'][1]:+.3f}]")
        if s["clv_mean"] is not None:
            out += (f"  CLV {s['clv_mean']:+.4f} [{s['clv_ci95'][0]:+.4f},{s['clv_ci95'][1]:+.4f}]"
                    f" (n_clv {s['clv_n']})")
        return out
    print("\nBACKTEST (simulated) — flat 1u, opening prices, CLV vs Pinnacle close de-vigged")
    print(f"  null: every selection at best open 1.30-4.50: n={len(nul):,} CLV {nb[0]:+.4f} [{nb[1]:+.4f},{nb[2]:+.4f}]")
    for k, v in null_bands.items():
        if v:
            print(f"     null {k}: n={v['n']:,} CLV {v['clv_mean']:+.4f} [{v['clv_ci95'][0]:+.4f},{v['clv_ci95'][1]:+.4f}]")
    for b in BOTS:
        pb = summary["per_bot"][b]
        print(line(f"{LABEL[b]} ({b})", pb["all"]))
        for k in ("by_odds_band", "fav_vs_long"):
            for kk, s in pb.get(k, {}).items():
                print(line(f"   {kk}", s))
        print(line("   [common universe]", summary["per_bot_common_universe"][b]))
        print(line("   [pipeline universe]", summary["per_bot_pipeline_universe"][b]))
    print(f"\nfunnel: {json.dumps(summary['funnel'], indent=0)}")
    print(f"unexpected SQL in shims: {dict(UNEXPECTED_SQL)}")
    print(f"wrote {OUT_DIR}")
    CONN.close()
    return 0


# ═════════════════════════════ BACKTEST B2 (NEW+ only) ═════════════════════════════
# Pre-registered in dev/active/1x2-model-rebuild-plan.md, "Pre-registration — BACKTEST B2"
# (2026-09-24 ~19:20 UTC, BEFORE the run). Arms and thresholds are FIXED there; do not
# tune them on this output. Same window B already looked at, so NOT a fresh OOS test.
#
# Rules common to all arms: NEW+ probability (as in B); edge = p*odds - 1 (EV);
# Pinnacle opening triple REQUIRED at decision time; no min_prob; no tier bump; all
# publishable books; opening price bounded timestamp < kickoff; ONE pick per match (the
# best-EV selection among those passing every gate); all other B gates unchanged.
#
# Where a B gate is written in probability points against `me`, it is applied here with
# the EV threshold in place of `me` (mid-band: EV < thr + 0.02; ALN-1 / league-eff bumps
# added to thr). The store-time veto (edge cap 0.25) keeps its production unit, the
# probability-point edge cal - 1/odds, because that is what store_bet derives and checks.
B2_ARMS = {
    "N1": {"ev_min": 0.03, "odds_range": (1.30, 6.00)},
    "N2": {"ev_min": 0.05, "odds_range": (1.30, 6.00)},
    "N3": {"ev_min": 0.08, "odds_range": (1.30, 6.00)},
    "N4": {"ev_min": 0.05, "odds_range": (2.00, 6.00)},
}
B2_HOLM_M = 4
B2_ALPHA = 0.05
B2_N_BOOT = 10000
B2_SEED = 20260924
B2_BANDS = (("1.30-1.99", 1.30, 2.0), ("2.00-2.99", 2.0, 3.0), ("3.00-6.00", 3.0, 6.0001))
B2_NULL_RANGE = (1.30, 6.00)
DIRECT_SWEEPER_BOOKS = frozenset({"Coolbet", "Unibet-Site", "Epicbet", "Tonybet"})   # everything else = API-Football feed


def run_b2_arm(arm: str, L: dict, cfg: dict | None = None, sels: tuple = SEL,
               exclude: set | None = None) -> tuple[list[dict], Counter]:
    """B2 arm. `cfg` / `sels` / `exclude` let the LANES test (scripts/backtest_1x2_lanes.py)
    reuse the SAME gate stack: a custom {ev_min, odds_range[, max_exclusive]}, a selection
    scope, and a set of (match_id, selection) the VIP bot holds, dropped BEFORE the
    one-pick-per-match choice. Defaults reproduce B2 exactly."""
    cfg = cfg or B2_ARMS[arm]
    thr, (omin, omax) = cfg["ev_min"], cfg["odds_range"]
    IMP.execute_query = _fake_execute_query
    SC.get_conn = _ro_get_conn
    matches, basis, base_preds, eff, news, calib, comb = (
        L[k] for k in ("matches", "basis", "base_preds", "eff", "news", "calib", "comb"))
    picks, fn = [], Counter()
    for mid, mt in matches.items():
        if mt["status"] != "finished" or mt["gh"] is None or mt["ga"] is None:
            continue
        m = basis.get(mid)
        if not m or not m["best"]:
            continue
        country, league_name = mt["country"] or "", mt["league_name"] or ""
        if country == "Scotland" and league_name == "Premiership":
            fn["skip_scottish_prem"] += 1
            continue
        rr = comb.get(mid)
        if rr is None:
            fn["drop_no_rating"] += 1
            continue
        if not all(s in m["pin_open"] for s in SEL):
            fn["drop_no_pinnacle"] += 1
            continue
        # decision time: every quote used (best prices AND Pinnacle's reference) exists
        t_dec = max(list(m["ts"].values()) + [m["pin_open"][s][1] for s in SEL])
        tier = int(mt["raw_tier"] or 1)
        pin_imp = pinnacle_implied(m["pin_open"])
        CTX.mid, CTX.t_dec, CTX.earliest = mid, t_dec, m["earliest"]
        CTX.news, CTX.lineups_ep = news.get(mid, []), mt["lineups_ep"]
        CTX.sharp_home, CTX.pin_home = sharp_consensus_home(m), pin_imp.get("home")
        calib.set(t_dec)
        e_rows = [v for ts, v in sorted(eff.get(mid, [])) if ts <= t_dec]
        league_eff = e_rows[-1] if e_rows else None
        bp = base_preds.get(mid)
        data_tier = bp["data_tier"] if bp else None
        fn["matches_evaluated"] += 1
        p1x2 = dict(zip(SEL, rr))
        accepted = []
        for sel in sels:
            odds = m["best"].get(sel, 0)
            if not odds or odds <= 0:
                continue
            fn["candidates"] += 1
            if exclude and (mid, sel) in exclude:
                fn["drop_vip_holds"] += 1
                continue
            p = p1x2[sel]
            if p is None or math.isnan(p):
                fn["drop_nan_raw"] += 1
                continue
            ev = p * odds - 1
            if ev < thr or odds < omin or odds > omax or (cfg.get("max_exclusive") and odds >= omax):
                fn["drop_ev" if ev < thr else "drop_odds_too_low" if odds < omin else "drop_odds_too_high"] += 1
                continue
            gap = p - pin_imp[sel]
            if (os.getenv("ANCHOR_GAP_MID_BAND_ENABLED", "true").lower() != "false"
                    and 0.06 <= gap < 0.10 and ev < thr + 0.02):
                fn["drop_pin_mid_band"] += 1
                continue
            if gap > PINNACLE_VETO_GAP:
                fn["drop_pin_veto"] += 1
                continue
            if sel == "home" and CTX.sharp_home is not None and CTX.sharp_home < -0.02:
                fn["drop_sharp_gate"] += 1
                continue
            mv = compute_odds_movement(mid, "1x2", sel, odds)
            if mv["veto"]:
                fn["drop_odds_mv"] += 1
                continue
            kelly = compute_kelly(p, odds)
            if kelly <= 0:
                fn["drop_kelly_zero"] += 1
                continue
            aln = compute_alignment(mid, sel.capitalize(), mv, {"tier": tier})
            aln_bump = ALN_BUMP.get(aln["alignment_class"], 0.0)
            eff_bump = 0.0
            if os.getenv("LEAGUE_EFF_EDGE_BUMP_ENABLED", "false").lower() in ("true", "1", "yes") \
                    and league_eff is not None:
                eff_bump = -0.01 if league_eff >= 0.02 else (0.01 if league_eff <= -0.01 else 0.0)
            if ev < thr + aln_bump + eff_bump:
                fn["drop_league_eff_edge" if eff_bump > 0 and ev >= thr + aln_bump else "drop_aln1"] += 1
                continue
            stake_checked = data_tier is not None
            stake = compute_stake(kelly, BANKROLL, data_tier or "A", odds_penalty=mv.get("penalty", 0.0))
            if stake_checked and stake < 1.0:
                fn["drop_stake_low"] += 1
                continue
            if not meta_b_ml3.should_fire(None):
                fn["drop_meta_b_ml3"] += 1
                continue
            veto = SC._bet_veto_reason(mid, p - 1 / odds)
            if veto:
                fn["drop_store_veto_" + veto.split("(")[0]] += 1
                continue
            accepted.append((ev, sel, odds, p, kelly, aln["alignment_class"], stake if stake_checked else None,
                             aln_bump + eff_bump))
        if not accepted:
            continue
        if len(accepted) > 1:
            fn["extra_selections_dropped_one_per_match"] += len(accepted) - 1
        ev, sel, odds, p, kelly, aln_cls, stake, bump = max(accepted)
        fn["accepted"] += 1
        pc = m.get("pin_close")
        p_close = pc[SEL.index(sel)] if pc else None
        won = (("home" if mt["gh"] > mt["ga"] else "draw" if mt["gh"] == mt["ga"] else "away") == sel)
        book = m["book"][sel]
        picks.append({
            "arm": arm, "match_id": mid, "kickoff_utc": _iso(mt["ko"]), "decision_time_utc": _iso(t_dec),
            "league": f"{country} / {league_name}", "tier": tier,
            "home_team": mt["home_team"], "away_team": mt["away_team"],
            "selection": sel, "odds": odds, "bookmaker": book,
            "book_source": "direct_sweeper" if book in DIRECT_SWEEPER_BOOKS else "api_football",
            "price_ts_utc": _iso(m["ts"][sel]), "prob": round(p, 5), "ev": round(ev, 5),
            "threshold": round(thr + bump, 4), "pin_open_implied": round(pin_imp[sel], 5),
            "kelly": round(kelly, 6), "pipeline_stake_at_1000": stake, "stake_gate_evaluated": stake is not None,
            "alignment_class": aln_cls, "league_clv_eff": league_eff,
            "result": "win" if won else "loss",
            "pnl_flat": round((odds - 1) * FLAT_STAKE if won else -FLAT_STAKE, 4),
            "pin_close_p_devig": None if p_close is None else round(p_close, 5),
            "clv_pin_devig": None if p_close is None else round(odds * p_close - 1, 5),
        })
    return picks, fn


def _p_one_sided(x: np.ndarray) -> float | None:
    """One-sided bootstrap p for H0: mean CLV <= 0 — share of resampled means <= 0."""
    if len(x) == 0:
        return None
    rng = np.random.default_rng(B2_SEED)
    bs = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(B2_N_BOOT)])
    return float((bs <= 0).mean())


def _holm(ps: dict[str, float | None]) -> dict[str, float | None]:
    items = sorted(((p, k) for k, p in ps.items() if p is not None))
    out, running = {k: None for k in ps}, 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (B2_HOLM_M - i) * p))
        out[k] = running
    return out


def _b2_summ(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    s = summarise(df)
    s["median_odds"] = float(df.odds.median())
    return s


def main_b2() -> int:
    L = load_everything()
    all_picks, arms, funnels = [], {}, {}
    for arm in B2_ARMS:
        picks, fn = run_b2_arm(arm, L)
        all_picks += picks
        funnels[arm] = dict(fn)
        df = pd.DataFrame(picks)
        clv = df["clv_pin_devig"].dropna().to_numpy(float) if len(df) else np.array([])
        band = {}
        for tag, lo, hi in B2_BANDS:
            band[tag] = _b2_summ(df[(df.odds >= lo) & (df.odds < hi)]) if len(df) else {"n": 0}
        books = df.groupby("bookmaker").size().sort_values(ascending=False).to_dict() if len(df) else {}
        src = {k: _b2_summ(df[df.book_source == k]) for k in ("api_football", "direct_sweeper")} if len(df) else {}
        arms[arm] = {"rule": {"ev_min": B2_ARMS[arm]["ev_min"], "odds_range": list(B2_ARMS[arm]["odds_range"])},
                     "all": _b2_summ(df), "p_one_sided": _p_one_sided(clv), "by_odds_band": band,
                     "by_book": books, "by_book_source": src}
    holm = _holm({a: arms[a]["p_one_sided"] for a in arms})
    for a in arms:
        arms[a]["p_holm"] = holm[a]
        arms[a]["verdict"] = ("PASS" if holm[a] is not None and holm[a] < B2_ALPHA
                              and (arms[a]["all"].get("clv_mean") or 0) > 0 else "FAIL")
    # null: every selection at its best opening price, 1.30-6.00, on matches with a Pinnacle triple
    nul = []
    for mid, mt in L["matches"].items():
        m = L["basis"].get(mid)
        if mt["status"] != "finished" or not m or not m.get("pin_close") or not all(s in m["pin_open"] for s in SEL):
            continue
        for sel in SEL:
            o = m["best"].get(sel)
            if o and B2_NULL_RANGE[0] <= o <= B2_NULL_RANGE[1]:
                nul.append((o, o * m["pin_close"][SEL.index(sel)] - 1))
    nul = np.array(nul, float).reshape(-1, 2)
    null_bands = {}
    for tag, lo, hi in B2_BANDS:
        x = nul[(nul[:, 0] >= lo) & (nul[:, 0] < hi), 1]
        b = _boot(x)
        null_bands[tag] = None if b is None else {"n": int(len(x)), "clv_mean": b[0], "clv_ci95": [b[1], b[2]]}
    ball = _boot(nul[:, 1])
    pd.DataFrame(all_picks).to_csv(OUT_DIR / "backtest_1x2_new_bots_picks_b2.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Backtest (simulated) B2 — NEW+ only, outlier-style rules — NOT live results",
        "preregistration": "dev/active/1x2-model-rebuild-plan.md — 'Pre-registration — BACKTEST B2'",
        "window": {"start": WINDOW_START, "end_inclusive": WINDOW_END,
                   "note": "same window B already looked at: NOT a fresh out-of-sample test"},
        "primary": f"mean CLV vs de-vigged Pinnacle close > 0; one-sided bootstrap p ({B2_N_BOOT} resamples, "
                   f"seed {B2_SEED}); Holm m={B2_HOLM_M}; PASS at adjusted p < {B2_ALPHA}",
        "gate_unit_notes": "B gates written in probability points vs `me` use the EV threshold in its place; "
                           "store veto edge cap keeps the production unit (cal - 1/odds).",
        "arms": arms, "funnel": funnels,
        "null_best_open_1.30_6.00": {"n": int(len(nul)), "clv_mean": None if ball is None else ball[0],
                                    "clv_ci95": None if ball is None else [ball[1], ball[2]],
                                    "by_odds_band": null_bands},
        "direct_sweeper_books": sorted(DIRECT_SWEEPER_BOOKS),
        "unexpected_sql_in_shims": dict(UNEXPECTED_SQL),
        "not_replicated": NOT_REPLICATED,
    }
    (OUT_DIR / "backtest_1x2_new_bots_summary_b2.json").write_text(json.dumps(summary, indent=1, default=str))

    def fmt(s):
        if not s or s.get("n", 0) == 0:
            return "n=0"
        c = (f"CLV {s['clv_mean']:+.4f} [{s['clv_ci95'][0]:+.4f},{s['clv_ci95'][1]:+.4f}] (n_clv {s['clv_n']})"
             if s.get("clv_mean") is not None else "CLV n/a")
        return (f"n={s['n']:4d} {c} ROI {s['roi_flat']:+.3f} [{s['roi_ci95'][0]:+.3f},{s['roi_ci95'][1]:+.3f}] "
                f"hit {s['hit_rate']:.3f} med odds {s['median_odds']:.2f}")
    print("\nBACKTEST B2 (simulated) — NEW+ only, EV rules, opening prices")
    for a, r in arms.items():
        pr = "n/a" if r["p_one_sided"] is None else f"{r['p_one_sided']:.4f}"
        ph = "n/a" if r["p_holm"] is None else f"{r['p_holm']:.4f}"
        print(f"  {a} EV>={r['rule']['ev_min']:.2f} odds {r['rule']['odds_range']}: {fmt(r['all'])} "
              f"p={pr} holm={ph} {r['verdict']}")
        for k, v in r["by_odds_band"].items():
            print(f"      {k}: {fmt(v)}")
        for k, v in r["by_book_source"].items():
            print(f"      [{k}] {fmt(v)}")
        print(f"      books: {r['by_book']}")
    print(f"  null 1.30-6.00 (Pinnacle-priced matches): n={len(nul):,} CLV {ball[0]:+.4f}")
    for k, v in null_bands.items():
        if v:
            print(f"      null {k}: n={v['n']:,} CLV {v['clv_mean']:+.4f} [{v['clv_ci95'][0]:+.4f},{v['clv_ci95'][1]:+.4f}]")
    print(f"funnel: {json.dumps(funnels)}")
    print(f"unexpected SQL in shims: {dict(UNEXPECTED_SQL)}")
    CONN.close()
    return 0


# ═════════════════════════════ BACKTEST B3 (configuration grid) ═════════════════════════════
# Pre-registered in dev/active/1x2-model-rebuild-plan.md, "Pre-registration — BACKTEST B3"
# (2026-09-24 ~19:25 UTC, BEFORE the run). EXPLORATORY: its honesty is the split
# selection/confirmation (Holm m=10) and Hansen's SPA, not the best row. No config is
# promoted from this run.
#
# Implementation: ONE candidate table (match x book set x selection, opening prices
# bounded timestamp < kickoff, every live gate pre-computed as a flag / bump), then all
# configs evaluated on it with numpy. Interpretation of "all other B gates as live",
# fixed before the first run: everything B expressed as an EDGE THRESHOLD (the tier
# table, the fav/long split, the T3+ bump, the baseline's data-tier bump) is REPLACED by
# the grid threshold; every other gate is kept exactly as in B — min_prob 0.30, Pinnacle
# veto (gap > 0.12), mid-band (0.06 <= gap < 0.10 and edge < thr + 0.02, only with a
# Pinnacle anchor), sharp gate, odds-movement veto, ALN-1 and league-efficiency bumps
# (added to thr in the config's unit, as in B2), stake floor, store vetoes (tier > 3,
# probability-point edge > 0.25), Scottish Premiership skip.
B3_MODELS = ("baseline", "new", "newplus")
B3_THRESH = {"pp": (0.02, 0.04, 0.06, 0.08, 0.10, 0.12), "ev": (0.02, 0.04, 0.06, 0.08, 0.12, 0.16)}
B3_ODDS_MIN = (1.30, 1.60, 2.00, 2.50)
B3_ODDS_MAX = (3.00, 4.50, 6.00, 10.00)
B3_SELECTIONS = ("all", "home", "draw", "away")
B3_PIN_REQUIRED = (True, False)
B3_BOOKSETS = ("all", "af", "direct")
B3_SPLIT_EPOCH = 1789257600.0            # 2026-09-13T00:00Z: select on 08-31..09-12, confirm on 09-13..09-24
B3_MIN_CLV_N = 30
B3_TOP_K = 10
B3_HOLM_M = 10
B3_ALPHA = 0.05
B3_N_BOOT = 10000
B3_SPA_N_BOOT = 2000
B3_SPA_MEAN_BLOCK_DAYS = 5.0              # stationary bootstrap mean block length (dates)
B3_SEED = 20260924
MIN_PROB = 0.30
EDGE_CAP = 0.25                           # VETO_EDGE_CAP, probability-point edge
TIER_VETO_MAX = 3                         # VETO_TIER_MAX


def _bookset_pred(bs: str):
    if bs == "all":
        return None
    if bs == "af":
        return lambda b: b not in DIRECT_SWEEPER_BOOKS
    return lambda b: b in DIRECT_SWEEPER_BOOKS


def build_candidate_table(L: dict) -> pd.DataFrame:
    IMP.execute_query = _fake_execute_query
    SC.get_conn = _ro_get_conn
    matches, base_preds, eff, news, calib = (L[k] for k in ("matches", "base_preds", "eff", "news", "calib"))
    new, comb = L["new"], L["comb"]
    pin_close = {mid: m.get("pin_close") for mid, m in L["basis"].items()}
    bases = {bs: price_basis(L["opens"], _bookset_pred(bs)) for bs in B3_BOOKSETS}
    rows = []
    for mid, mt in matches.items():
        if mt["status"] != "finished" or mt["gh"] is None or mt["ga"] is None:
            continue
        if (mt["country"] or "") == "Scotland" and (mt["league_name"] or "") == "Premiership":
            continue
        tier = int(mt["raw_tier"] or 1)
        tier_veto = mt["raw_tier"] is not None and int(mt["raw_tier"]) > TIER_VETO_MAX
        result = "home" if mt["gh"] > mt["ga"] else ("draw" if mt["gh"] == mt["ga"] else "away")
        bp = base_preds.get(mid)
        data_tier = bp["data_tier"] if bp else None
        probs = {"baseline": ({s: bp[f"{s}_prob"] for s in SEL} if bp and all(bp[f"{s}_prob"] is not None for s in SEL) else None),
                 "new": dict(zip(SEL, new[mid])) if mid in new else None,
                 "newplus": dict(zip(SEL, comb[mid])) if mid in comb else None}
        pc = pin_close.get(mid)
        for bs in B3_BOOKSETS:
            m = bases[bs].get(mid)
            if not m or not m["best"]:
                continue
            pin_triple = all(s in m["pin_open"] for s in SEL)
            ts = list(m["ts"].values()) + ([m["pin_open"][s][1] for s in SEL if s in m["pin_open"]])
            t_dec = max(ts)
            pin_imp = pinnacle_implied(m["pin_open"])
            CTX.mid, CTX.t_dec, CTX.earliest = mid, t_dec, m["earliest"]
            CTX.news, CTX.lineups_ep = news.get(mid, []), mt["lineups_ep"]
            CTX.sharp_home, CTX.pin_home = sharp_consensus_home(m), pin_imp.get("home")
            calib.set(t_dec)
            e_rows = [v for t, v in sorted(eff.get(mid, [])) if t <= t_dec]
            league_eff = e_rows[-1] if e_rows else None
            eff_bump = 0.0 if league_eff is None else (-0.01 if league_eff >= 0.02 else (0.01 if league_eff <= -0.01 else 0.0))
            for sel in SEL:
                odds = m["best"].get(sel)
                if not odds:
                    continue
                ip = 1 / odds
                mv = compute_odds_movement(mid, "1x2", sel, odds)
                aln = compute_alignment(mid, sel.capitalize(), mv, {"tier": tier})
                anchor = pin_imp.get(sel)
                r = {"match_id": mid, "ko": mt["ko"], "day": int((mt["ko"] - WINDOW_START_EPOCH) // 86400),
                     "bookset": bs, "selection": sel, "odds": odds, "bookmaker": m["book"][sel],
                     "direct": m["book"][sel] in DIRECT_SWEEPER_BOOKS, "pin_triple": pin_triple,
                     "has_anchor": anchor is not None, "tier": tier, "static_ok": (not tier_veto) and not mv["veto"]
                     and not (sel == "home" and CTX.sharp_home is not None and CTX.sharp_home < -0.02),
                     "bump": ALN_BUMP.get(aln["alignment_class"], 0.0) + eff_bump,
                     "win": result == sel,
                     "clv": np.nan if pc is None else odds * pc[SEL.index(sel)] - 1}
                for mod in B3_MODELS:
                    pr = probs[mod]
                    if pr is None or pr[sel] is None or math.isnan(pr[sel]):
                        r[f"{mod}_p"] = np.nan
                        continue
                    p = calibrate_prob(pr[sel], ip, tier=tier, market=f"1x2_{sel}", anchor_implied=anchor, odds=odds) \
                        if mod == "baseline" else pr[sel]
                    gap = p - (anchor if anchor is not None else ip)
                    kelly = compute_kelly(p, odds)
                    stake_ok = data_tier is None or kelly <= 0 or \
                        compute_stake(kelly, BANKROLL, data_tier, odds_penalty=mv.get("penalty", 0.0)) >= 1.0
                    r[f"{mod}_p"] = p
                    r[f"{mod}_gap"] = gap
                    r[f"{mod}_ok"] = bool(p >= MIN_PROB and gap <= PINNACLE_VETO_GAP and stake_ok
                                          and (p - ip) <= EDGE_CAP)
                rows.append(r)
    t = pd.DataFrame(rows)
    for mod in B3_MODELS:
        for c in (f"{mod}_gap",):
            if c not in t:
                t[c] = np.nan
        t[f"{mod}_ok"] = t[f"{mod}_ok"].fillna(False).astype(bool) if f"{mod}_ok" in t else False
        t[f"{mod}_pp"] = t[f"{mod}_p"] - 1 / t["odds"]
        t[f"{mod}_ev"] = t[f"{mod}_p"] * t["odds"] - 1
    return t


def _configs():
    for mod in B3_MODELS:
        for unit in ("pp", "ev"):
            for thr in B3_THRESH[unit]:
                for omin in B3_ODDS_MIN:
                    for omax in B3_ODDS_MAX:
                        if omin >= omax:
                            continue
                        for sel in B3_SELECTIONS:
                            for pin in B3_PIN_REQUIRED:
                                for bs in B3_BOOKSETS:
                                    yield mod, unit, thr, omin, omax, sel, pin, bs


def evaluate_grid(t: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, dict]:
    """Returns (config table, per-day CLV sums D [K, n_days], per-day CLV counts N, null means)."""
    n_days = int(round((WINDOW_END_EPOCH - WINDOW_START_EPOCH) / 86400))
    mcode = pd.factorize(t["match_id"])[0]
    odds, sel_a, day = t["odds"].to_numpy(), t["selection"].to_numpy(), t["day"].to_numpy()
    clv, win = t["clv"].to_numpy(float), t["win"].to_numpy(bool)
    first_half = t["ko"].to_numpy() < B3_SPLIT_EPOCH
    bump, static_ok = t["bump"].to_numpy(), t["static_ok"].to_numpy(bool)
    pin_triple, has_anchor = t["pin_triple"].to_numpy(bool), t["has_anchor"].to_numpy(bool)
    bsa = t["bookset"].to_numpy()
    # null: every selection at best open price, same odds range + book set
    null = {}
    for bs in B3_BOOKSETS:
        for omin in B3_ODDS_MIN:
            for omax in B3_ODDS_MAX:
                if omin < omax:
                    mk = (bsa == bs) & (odds >= omin) & (odds <= omax) & ~np.isnan(clv)
                    null[(bs, omin, omax)] = (float(clv[mk].mean()) if mk.any() else np.nan, int(mk.sum()))
    out, D, N = [], [], []
    cache = {}
    for mod, unit, thr, omin, omax, sel, pin, bs in _configs():
        key = (mod, unit, bs)
        if key not in cache:
            edge = t[f"{mod}_{unit}"].to_numpy(float)
            base = (bsa == bs) & static_ok & t[f"{mod}_ok"].to_numpy(bool) & ~np.isnan(edge)
            gap = t[f"{mod}_gap"].to_numpy(float)
            midband = has_anchor & (gap >= 0.06) & (gap < 0.10)
            order = np.lexsort((-np.nan_to_num(edge, nan=-9), mcode))     # by match, best edge first
            cache[key] = (edge, base, midband, order)
        edge, base, midband, order = cache[key]
        thr_eff = thr + bump
        mk = base & (edge >= thr_eff) & ~(midband & (edge < thr + 0.02)) & (odds >= omin) & (odds <= omax)
        if sel != "all":
            mk &= sel_a == sel
        if pin:
            mk &= pin_triple
        o = order[mk[order]]
        if len(o):
            _, first = np.unique(mcode[o], return_index=True)
            idx = o[first]
        else:
            idx = o
        c, w, od, h = clv[idx], win[idx], odds[idx], first_half[idx]
        pnl = np.where(w, od - 1, -1.0)
        cm = ~np.isnan(c)
        nm, nn = null[(bs, omin, omax)]

        def mets(sub, tag):
            cs = c[sub & cm]
            return {f"{tag}_n": int(sub.sum()), f"{tag}_n_clv": int(len(cs)),
                    f"{tag}_clv": float(cs.mean()) if len(cs) else np.nan,
                    f"{tag}_roi": float(pnl[sub].mean()) if sub.any() else np.nan,
                    f"{tag}_hit": float(w[sub].mean()) if sub.any() else np.nan}
        allm = np.ones(len(idx), bool)
        row = {"model": mod, "unit": unit, "threshold": thr, "odds_min": omin, "odds_max": omax,
               "selection": sel, "pin_required": pin, "bookset": bs,
               **mets(allm, "full"), **mets(h, "sel"), **mets(~h, "conf"),
               "full_mean_odds": float(od.mean()) if len(od) else np.nan,
               "full_direct_share": float(t["direct"].to_numpy()[idx].mean()) if len(idx) else np.nan,
               "null_clv": nm, "null_n": nn}
        out.append(row)
        dd = np.zeros(n_days); nn_ = np.zeros(n_days)
        if cm.any():
            np.add.at(dd, day[idx][cm], c[cm] - nm)
            np.add.at(nn_, day[idx][cm], 1)
        D.append(dd); N.append(nn_)
    return pd.DataFrame(out), np.array(D), np.array(N), null


def _pick_idx_for(t: pd.DataFrame, cfg: dict) -> np.ndarray:
    """Re-derive one config's picks (for the confirmation bootstrap)."""
    mcode = pd.factorize(t["match_id"])[0]
    mod, unit = cfg["model"], cfg["unit"]
    edge = t[f"{mod}_{unit}"].to_numpy(float)
    gap = t[f"{mod}_gap"].to_numpy(float)
    mk = ((t["bookset"].to_numpy() == cfg["bookset"]) & t["static_ok"].to_numpy(bool) & t[f"{mod}_ok"].to_numpy(bool)
          & ~np.isnan(edge) & (edge >= cfg["threshold"] + t["bump"].to_numpy())
          & ~(t["has_anchor"].to_numpy(bool) & (gap >= 0.06) & (gap < 0.10) & (edge < cfg["threshold"] + 0.02))
          & (t["odds"].to_numpy() >= cfg["odds_min"]) & (t["odds"].to_numpy() <= cfg["odds_max"]))
    if cfg["selection"] != "all":
        mk &= t["selection"].to_numpy() == cfg["selection"]
    if cfg["pin_required"]:
        mk &= t["pin_triple"].to_numpy(bool)
    order = np.lexsort((-np.nan_to_num(edge, nan=-9), mcode))
    o = order[mk[order]]
    if not len(o):
        return o
    _, first = np.unique(mcode[o], return_index=True)
    return o[first]


def _holm_m(ps: list[float | None], m: int) -> list[float | None]:
    order = sorted((p, i) for i, p in enumerate(ps) if p is not None)
    out, running = [None] * len(ps), 0.0
    for j, (p, i) in enumerate(order):
        running = max(running, min(1.0, (m - j) * p))
        out[i] = running
    return out


def _stationary_counts(n_days: int, B: int, mean_block: float, rng) -> np.ndarray:
    """B x n_days matrix: how many times each date is drawn in a stationary bootstrap."""
    C = np.zeros((B, n_days))
    q = 1.0 / mean_block
    for b in range(B):
        pos = rng.integers(n_days)
        for _ in range(n_days):
            C[b, pos] += 1
            pos = rng.integers(n_days) if rng.random() < q else (pos + 1) % n_days
    return C


def hansen_spa(D: np.ndarray, N: np.ndarray, keep: np.ndarray) -> dict:
    """Hansen (2005) SPA, consistent (c) and upper (u) p-values, on per-config mean
    excess CLV over the same-odds-range null. Statistic per config: f = sum_t D / sum_t N
    (mean excess CLV per pick), studentised by its bootstrap sd; resampling = stationary
    bootstrap over kickoff DATES."""
    D, N = D[keep], N[keep]
    n_days = D.shape[1]
    f = D.sum(1) / N.sum(1)
    rng = np.random.default_rng(B3_SEED)
    C = _stationary_counts(n_days, B3_SPA_N_BOOT, B3_SPA_MEAN_BLOCK_DAYS, rng)
    num, den = C @ D.T, C @ N.T
    with np.errstate(invalid="ignore", divide="ignore"):
        fs = np.where(den > 0, num / den, 0.0)
    om = fs.std(0)
    om = np.where(om > 0, om, np.inf)
    T = max(0.0, float(np.max(f / om)))
    A = om * math.sqrt(2 * math.log(math.log(n_days)))
    g_c = np.where(f >= -A, f, 0.0)
    Zc = (fs - g_c) / om
    Zu = (fs - f) / om
    Tc = np.maximum(0.0, Zc.max(1))
    Tu = np.maximum(0.0, Zu.max(1))
    best = int(np.argmax(f / om))
    return {"n_configs": int(keep.sum()), "n_dates": n_days, "resamples": B3_SPA_N_BOOT,
            "mean_block_days": B3_SPA_MEAN_BLOCK_DAYS, "seed": B3_SEED, "T": T,
            "p_spa_c": float((Tc >= T).mean()), "p_spa_u": float((Tu >= T).mean()),
            "best_idx_within_kept": best, "best_mean_excess_clv": float(f[best])}


def main_b3() -> int:
    L = load_everything()
    print(f"  baseline #065 un-swap: {SWAP_STATS}")
    t = build_candidate_table(L)
    print(f"  candidate table: {len(t):,} rows (match x book set x selection)")
    cfg, D, N, null = evaluate_grid(t)
    print(f"  {len(cfg):,} configs evaluated")
    # 1. split selection / confirmation
    elig = cfg[cfg.sel_n_clv >= B3_MIN_CLV_N].sort_values("sel_clv", ascending=False)
    top = elig.head(B3_TOP_K).copy()
    ps, cmeans, cn, ci = [], [], [], []
    rng_seed = B3_SEED
    for _, r in top.iterrows():
        idx = _pick_idx_for(t, r.to_dict())
        conf = idx[t["ko"].to_numpy()[idx] >= B3_SPLIT_EPOCH]
        x = t["clv"].to_numpy(float)[conf]
        x = x[~np.isnan(x)]
        if len(x) == 0:
            ps.append(None); cmeans.append(np.nan); cn.append(0); ci.append(None)
            continue
        rng = np.random.default_rng(rng_seed)
        bs = x[rng.integers(0, len(x), (B3_N_BOOT, len(x)))].mean(1)
        ps.append(float((bs <= 0).mean())); cmeans.append(float(x.mean())); cn.append(int(len(x)))
        ci.append([float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))])
    holm = _holm_m(ps, B3_HOLM_M)
    top["conf_clv_boot"] = cmeans
    top["conf_n_clv_boot"] = cn
    top["conf_clv_ci95"] = ci
    top["p_one_sided"] = ps
    top["p_holm"] = holm
    top["verdict"] = ["PASS" if (h is not None and h < B3_ALPHA and m > 0) else "FAIL" for h, m in zip(holm, cmeans)]
    # 2. SPA on the full window
    keep = (cfg.full_n_clv >= B3_MIN_CLV_N).to_numpy() & ~cfg.null_clv.isna().to_numpy()
    spa = hansen_spa(D, N, keep)
    best_cfg = cfg[keep].iloc[spa["best_idx_within_kept"]].to_dict()
    spa["best_config"] = {k: best_cfg[k] for k in ("model", "unit", "threshold", "odds_min", "odds_max", "selection",
                                                   "pin_required", "bookset", "full_n_clv", "full_clv", "null_clv")}
    # 2b. SUPPLEMENTARY, NOT pre-registered: the same SPA against a zero-CLV benchmark.
    # The pre-registered null (every selection at best open, same odds range) sits at
    # -1.5..-5% because it is dominated by high-margin longshot quotes, so beating it is
    # easy; "better than Pinnacle's close" (CLV > 0) is the harder, more useful bar.
    D0 = D + N * cfg["null_clv"].fillna(0).to_numpy()[:, None]
    spa0 = hansen_spa(D0, N, (cfg.full_n_clv >= B3_MIN_CLV_N).to_numpy())
    spa["supplementary_vs_zero_clv_not_preregistered"] = {k: spa0[k] for k in ("n_configs", "T", "p_spa_c", "p_spa_u",
                                                                                "best_mean_excess_clv")}
    # 3. descriptive
    k30 = cfg[cfg.full_n_clv >= B3_MIN_CLV_N]
    share = {m: {"configs_with_ge30_clv": int((k30.model == m).sum()),
                 "share_clv_gt_0": float((k30[k30.model == m].full_clv > 0).mean()) if (k30.model == m).any() else None,
                 "share_clv_gt_null": float((k30[k30.model == m].full_clv > k30[k30.model == m].null_clv).mean())
                 if (k30.model == m).any() else None}
             for m in B3_MODELS}
    share["null"] = {"cells": len(null), "share_clv_gt_0": float(np.mean([v[0] > 0 for v in null.values() if not np.isnan(v[0])]))}
    surf_slice = cfg[(cfg.selection == "all") & (~cfg.pin_required) & (cfg.bookset == "all")]
    surfaces = {}
    for m in B3_MODELS:
        for u in ("pp", "ev"):
            s = surf_slice[(surf_slice.model == m) & (surf_slice.unit == u)]
            surfaces[f"{m}_{u}"] = {f"{r.odds_min:.2f}-{r.odds_max:.2f}|thr={r.threshold}":
                                    {"n_clv": int(r.full_n_clv), "clv": None if np.isnan(r.full_clv) else round(r.full_clv, 4),
                                     "roi": None if np.isnan(r.full_roi) else round(r.full_roi, 3),
                                     "null": None if np.isnan(r.null_clv) else round(r.null_clv, 4)}
                                    for r in s.itertuples()}
    cfg.to_csv(OUT_DIR / "backtest_1x2_grid_configs_b3.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "Backtest (simulated) B3 — EXPLORATORY configuration grid — NOT live results; no config promoted",
        "preregistration": "dev/active/1x2-model-rebuild-plan.md — 'Pre-registration — BACKTEST B3'",
        "window": {"start": WINDOW_START, "end_inclusive": WINDOW_END, "selection_half": "2026-08-31..2026-09-12",
                   "confirmation_half": "2026-09-13..2026-09-24"},
        "gate_interpretation": "edge-threshold structure of B (tier table, fav/long split, T3+ bump, data-tier bump) "
                               "replaced by the grid threshold; all other B gates kept incl. min_prob 0.30; mid-band "
                               "and ALN/league bumps applied in the config's unit; null = every selection at best "
                               "open price in the same odds range AND book set.",
        "baseline_065_unswap": SWAP_STATS,
        "grid": {"models": B3_MODELS, "thresholds": B3_THRESH, "odds_min": B3_ODDS_MIN, "odds_max": B3_ODDS_MAX,
                 "selections": B3_SELECTIONS, "pin_required": B3_PIN_REQUIRED, "booksets": B3_BOOKSETS,
                 "n_configs": int(len(cfg))},
        "candidate_rows": int(len(t)),
        "selection_confirmation": {"min_clv_n_in_selection_half": B3_MIN_CLV_N, "eligible": int(len(elig)),
                                   "holm_m": B3_HOLM_M, "resamples": B3_N_BOOT, "seed": B3_SEED,
                                   "top10": json.loads(top.to_json(orient="records"))},
        "spa": spa, "share_configs": share, "surfaces_sel_all_pin_no_books_all": surfaces,
        "unexpected_sql_in_shims": dict(UNEXPECTED_SQL),
    }
    (OUT_DIR / "backtest_1x2_grid_summary_b3.json").write_text(json.dumps(summary, indent=1, default=str))
    print("\nB3 top-10 (ranked on 08-31..09-12, confirmed on 09-13..09-24):")
    cols = ["model", "unit", "threshold", "odds_min", "odds_max", "selection", "pin_required", "bookset",
            "sel_n_clv", "sel_clv", "conf_n_clv_boot", "conf_clv_boot", "p_one_sided", "p_holm", "verdict"]
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(top[cols].to_string(index=False))
    print(f"\nSPA: {json.dumps(spa, default=str)}")
    print(f"share of configs (>=30 CLV picks): {json.dumps(share)}")
    CONN.close()
    return 0


if __name__ == "__main__":
    _args = sys.argv[1:]
    raise SystemExit(main_b3() if "--b3" in _args else main_b2() if "--b2" in _args else main())
