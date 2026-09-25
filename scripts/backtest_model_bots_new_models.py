#!/usr/bin/env python3
"""#152 step 3 — the existing MODEL bots on the new models: per-bot config family, split test.

Pre-registration (FIXED, written before any run): dev/active/model-bots-new-models-plan.md,
"Pre-registration — step 3". Implemented exactly; nothing below may be tuned on its output.

  * window kickoffs 2026-08-31..2026-09-24, OPEN prices (earliest pre-kickoff quote per book,
    timestamp < kickoff), as backtests B / B3; select half 08-31..09-12, confirm 09-13..09-24;
  * metric: mean CLV vs Pinnacle's POWER-de-vigged last pre-kickoff price, same market/line.
    Per ANALYSIS_GOTCHAS #83 a close only counts when it is a SEPARATE, later row than
    Pinnacle's opening quote (otherwise CLV is circular) — picks without one carry no CLV;
  * probabilities: old = stored pre-kickoff `predictions` ensemble (1X2 with the #065 XGB-leg
    un-swap; O/U over25/under25/over35 rows, source='ensemble', production version), put
    through the bot's own live calibration; new = NEW+ walk-forward OPEN (1X2, as B/B2) and the
    SERVED O/U probability at OPEN (Pinnacle where it prices the line, else the combined model;
    workers.model.combined_ou.served_over), used as is;
  * per bot, identity fixed (market, selection scope, league filter, book set, odds floor):
    model {old, new} x edge unit {own pp rule, EV} x threshold {own, one step lower, one step
    higher} (pp steps +-2pp applied to the whole rule; EV in {3%, 5%, 8%}) = 12 configs;
    every other live gate as in the B3 replication;
  * PUBLIC-bot constraint (bot_v10_1x2, bot_high_roi_global_v2, bot_v10_ou): a candidate the
    VIP bot of that market would hold is skipped — 1X2: the NEW+ EV >= 5% bot's picks (B2 arm
    N2, same match+selection); O/U: the EARLY bot's picks (EV vs Pinnacle 5-15%, quote >= 12 h
    before kickoff, one per match+line, same match+line+side);
  * procedure: on the select half take the best mean-CLV config among those with >= 20 CLV
    picks; score it ONCE on the confirm half against the bot's CURRENT config (old model, own
    rule) on the same half — one-sided bootstrap of the difference in mean CLV (10k), Holm
    m = 7 (bots); SWITCH only if adj p < 0.05 AND the selected config's confirm CLV > 0.

Read-only against the DB. Outputs (gitignored): data/models/_research/market2/
model_bots_new_models_configs.csv + model_bots_new_models_summary.json.

    python3 scripts/backtest_model_bots_new_models.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import backtest_1x2_new_bots as BT  # noqa: E402  (prod env, read-only DB helpers, 1X2 loaders)
import backtest_ou_comb_bots as OUB  # noqa: E402  (O/U caches + pin close + O2 quotes)
from workers.model.devig import power_devig as power_devig_n  # noqa: E402

# ── pre-registered design constants ──────────────────────────────────────────
WINDOW_START, WINDOW_END = BT.WINDOW_START, BT.WINDOW_END      # 2026-08-31 .. 2026-09-24
SPLIT_EPOCH = 1789257600.0            # 2026-09-13T00:00Z: select 08-31..09-12, confirm 09-13..09-24
MODELS = ("old", "new")
UNITS = ("pp", "ev")
PP_STEPS = (0.0, -0.02, 0.02)          # own, one step lower, one step higher
EV_THRESHOLDS = (0.05, 0.03, 0.08)     # own (5%), lower, higher
FAMILY_SIZE = len(MODELS) * len(UNITS) * 3   # = 12 (the plan text says "<= 9"; its own product is 12)
MIN_CLV_PICKS_SELECT = 20
HOLM_M = 7
ALPHA = 0.05
N_BOOT = 10000
SEED = 20260925
VIP_1X2_ARM = "N2"                     # B2: NEW+ EV >= 5%, Pinnacle required, best-EV per match
VIP_OU_EV = (0.05, 0.15)
VIP_OU_MIN_HOURS = 12.0
OUT_DIR = ROOT / "data" / "models" / "_research" / "market2"
SEL3 = ("home", "draw", "away")
DIRECT = BT.DIRECT_SWEEPER_BOOKS

# ── the seven bots: identity + current rule, read from the live code (2026-09-25) ──
#   kind "pipeline": daily_pipeline_v2 funnel (B3 gate set: min_prob, Pinnacle veto, mid-band,
#                    sharp gate, odds-movement veto, ALN-1 / league-eff bumps, stake floor,
#                    store vetoes; data-tier bump for the OLD model only, as the live rating twins)
#   kind "generator": pick_generator / ou35 job: edge floor + odds floor only
V10_TABLE = {1: {"1x2_fav": 0.08, "1x2_long": 0.12, "ou": 0.08}, 2: {"1x2_fav": 0.05, "1x2_long": 0.08, "ou": 0.06},
             3: {"1x2_fav": 0.04, "1x2_long": 0.06, "ou": 0.05}, 4: {"1x2_fav": 0.03, "1x2_long": 0.05, "ou": 0.04}}
HRG_TABLE = {1: {"1x2_fav": 0.06, "1x2_long": 0.09}, 2: {"1x2_fav": 0.05, "1x2_long": 0.08},
             3: {"1x2_fav": 0.05, "1x2_long": 0.08}}
BOTS = {
    "bot_v10_1x2": dict(market="1x2", kind="pipeline", public=True, sels=SEL3, leagues=None,
                        books="all", odds=(1.30, 4.50), min_prob=0.30, table=V10_TABLE, flat=None),
    "bot_high_roi_global_v2": dict(market="1x2", kind="pipeline", public=True, sels=("home", "away"),
                                   leagues=("Spain", "Australia", "Iceland"), books="all", odds=(1.50, 5.50),
                                   min_prob=0.28, table=HRG_TABLE, flat=None),
    "bot_coolbet_1x2_model_v1": dict(market="1x2", kind="generator", public=False, sels=("home",), leagues=None,
                                     books="coolbet", odds=(2.80, 1e9), min_prob=None, table=None, flat=0.10),
    "bot_unified_gate_1x2_paper_v1": dict(market="1x2", kind="generator", public=False, sels=SEL3, leagues=None,
                                          books="placeable", odds=(2.80, 1e9), min_prob=None, table=None, flat=0.10),
    "bot_coolbet_ou_model_v1": dict(market="over_under_25", kind="generator", public=False, sels=("over", "under"),
                                    leagues=None, books="coolbet", odds=(1.80, 1e9), min_prob=None, table=None,
                                    flat=0.08),
    "bot_ou35_model_v1": dict(market="over_under_35", kind="generator", public=False, sels=("over", "under"),
                              leagues=None, books="coolbet", odds=(1.01, 1e9), min_prob=None, table=None,
                              flat=0.08),
    "bot_v10_ou": dict(market="over_under_25", kind="pipeline", public=True, sels=("over", "under"), leagues=None,
                       books="all", odds=(1.30, 4.50), min_prob=0.30, table=V10_TABLE, flat=None),
}
BOOKSETS = {"all": None, "coolbet": lambda b: b == "Coolbet",
            "placeable": lambda b: b in ("Coolbet", "Unibet-Site")}
NOTES = [
    "bot_coolbet_1x2_model_v1 / bot_coolbet_ou_model_v1 take candidates LIVE only from calibrated pipeline picks "
    "(simulated_bets); not replicated — every modelled match is a candidate here, for both models alike.",
    "bot_coolbet_* priced at Coolbet's own price as the plan's identity table says; the live BotConfig also lets "
    "Unibet-Site compete (PLACEABLE_BOOKS).",
    "bot_coolbet_ou_model_v1: O/U 2.5 per the plan; its live convert() also admits 3.5.",
    "bot_ou35_model_v1: the live job has NO odds floor (the plan table says >= 1.80) — the live rule is used. Its "
    "old probability = isotonic on raw over35 (as the job), fitted once on matches settled before 2026-08-31.",
    "bot_unified_gate_1x2_paper_v1: live probability is pick_triggers._fit_calibrator on raw predictions; the "
    "pipeline's calibrate_prob (as-of) is used for 'old' here, as for the other 1X2 bots.",
    "bot_v10_ou: O/U 2.5 only (1.5/3.5 are not a dimension of the 12-config family).",
    "EV-unit configs are flat (no tier table, no T3+ / data-tier bump), as in B2/B3; mid-band and ALN/league bumps "
    "are applied in the config's unit for pipeline bots.",
    "O/U book set 'all' = the O/U research leg cache (consensus books + Pinnacle; SBO absent), publishable only, "
    "with the pipeline's OU-PIN-REQUIRED, 2x-Pinnacle cap and implied-sum sanity gates.",
]


# ═════════════════════════════ loaders ═════════════════════════════
def _pin_close_1x2(ids: list[str]) -> dict:
    """Pinnacle last pre-KO 1X2 triple, power de-vigged, only if it is a later row than
    Pinnacle's opening quote (GOTCHAS #83)."""
    rows = []
    for i in range(0, len(ids), 2000):
        rows += BT._q("""
            SELECT o.match_id::text match_id, o.selection,
                   (array_agg(o.odds::float8 ORDER BY o."timestamp" DESC))[1] c,
                   max(extract(epoch FROM o."timestamp"))::float8 tmax,
                   min(extract(epoch FROM o."timestamp"))::float8 tmin
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = '1x2' AND o.bookmaker = 'Pinnacle'
               AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND o."timestamp" < m.date
             GROUP BY 1, 2""", (ids[i:i + 2000],))
    by = defaultdict(dict)
    for r in rows:
        by[r["match_id"]][r["selection"]] = r
    out, circular = {}, 0
    for mid, s in by.items():
        if not all(k in s for k in SEL3):
            continue
        if min(s[k]["tmax"] for k in SEL3) <= max(s[k]["tmin"] for k in SEL3):
            circular += 1
            continue
        p = power_devig_n([s[k]["c"] for k in SEL3])
        if p:
            out[mid] = p
    return out, circular


def _ou_pin_close() -> tuple[pd.DataFrame, int]:
    """Power-de-vigged Pinnacle O/U close per (match, market), guarded as above."""
    cl = pd.read_parquet(OUB.OUT / "ou_legs_close.parquet")
    op = pd.read_parquet(OUB.OUT / "ou_legs_open.parquet")
    pc = OUB.pin_close()
    tc = cl[cl.bookmaker == "Pinnacle"].groupby(["match_id", "market"]).ts.min().rename("tc")
    to = op[op.bookmaker == "Pinnacle"].groupby(["match_id", "market"]).ts.max().rename("to")
    pc = pc.merge(tc, on=["match_id", "market"], how="left").merge(to, on=["match_id", "market"], how="left")
    ok = pc.tc > pc.to
    return pc[ok][["match_id", "market", "pin_close_over"]], int((~ok).sum())


def _load_ou_old(ids: list[str]) -> dict:
    """Stored pre-kickoff ensemble O/U rows (production version = latest first write)."""
    rows = BT._q("""
        SELECT p.match_id::text match_id, p.market, p.model_version, p.model_probability::float8 p,
               p.reasoning, extract(epoch FROM p.created_at)::float8 created
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.match_id = ANY(%s::uuid[]) AND p.source = 'ensemble'
           AND p.market IN ('over25','under25','over35','under35')
           AND coalesce(p.reasoning, '') NOT LIKE '%%shadow=%%'
           AND p.created_at < m.date""", (ids,))
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by[r["match_id"]][r["model_version"]][r["market"]] = r
    out = {}
    for mid, vers in by.items():
        ver, mk = max(vers.items(), key=lambda kv: max(x["created"] for x in kv[1].values()))
        out[mid] = {k: v["p"] for k, v in mk.items()} | {"version": ver}
    return out


def _ou35_isotonic():
    """bot_ou35_model_v1's own calibration, fitted once on matches settled before the window."""
    from sklearn.isotonic import IsotonicRegression
    rows = BT._q("""
        SELECT po.p, ((m.score_home + m.score_away) > 3.5)::int y
          FROM matches m
          JOIN LATERAL (SELECT model_probability::float8 p FROM predictions
                         WHERE match_id = m.id AND market = 'over35' AND created_at < m.date
                         ORDER BY model_version DESC LIMIT 1) po ON true
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL AND m.date < to_timestamp(%s)""",
                 (BT.WINDOW_START_EPOCH,))
    iso = IsotonicRegression(out_of_bounds="clip").fit([r["p"] for r in rows], [r["y"] for r in rows])
    return iso, len(rows)


def _full_calibration() -> list:
    rows = BT._q("""SELECT market, platt_a::float8 a, platt_b::float8 b, platt_c::float8 c,
                           extract(epoch FROM fitted_at)::float8 ts
                      FROM model_calibration ORDER BY fitted_at""")
    return [(r["ts"], r["market"], r["a"], r["b"], r["c"]) for r in rows]


def _vip_1x2(L: dict) -> set:
    picks, _ = BT.run_b2_arm(VIP_1X2_ARM, L)
    return {(p["match_id"], p["selection"]) for p in picks}


def _vip_ou() -> set:
    q = OUB.o2_quotes()
    q = q[(q.ev >= VIP_OU_EV[0]) & (q.ev <= VIP_OU_EV[1]) & (q.h_before >= VIP_OU_MIN_HOURS)]
    q = q.sort_values("ev", ascending=False).drop_duplicates(["match_id", "market"])
    return set(zip(q.match_id, q.market, q.selection))


# ═════════════════════════════ candidate tables ═════════════════════════════
def _set_ctx(mid, t_dec, earliest, mt, news, sharp_home, pin_home):
    C = BT.CTX
    C.mid, C.t_dec, C.earliest = mid, t_dec, earliest
    C.news, C.lineups_ep = news.get(mid, []), mt["lineups_ep"]
    C.sharp_home, C.pin_home = sharp_home, pin_home


def _league_eff_bump(eff, mid, t_dec):
    e = [v for t, v in sorted(eff.get(mid, [])) if t <= t_dec]
    if not e:
        return 0.0
    return -0.01 if e[-1] >= 0.02 else (0.01 if e[-1] <= -0.01 else 0.0)


def table_1x2(L: dict, pin_close: dict, calib) -> pd.DataFrame:
    BT.IMP.execute_query = BT._fake_execute_query
    matches, base_preds, eff, news, comb = (L[k] for k in ("matches", "base_preds", "eff", "news", "comb"))
    bases = {bs: BT.price_basis(L["opens"], f) for bs, f in BOOKSETS.items()}
    rows = []
    for mid, mt in matches.items():
        if mt["status"] != "finished" or mt["gh"] is None:
            continue
        if (mt["country"] or "") == "Scotland" and (mt["league_name"] or "") == "Premiership":
            continue
        tier = int(mt["raw_tier"] or 1)
        tier_veto = mt["raw_tier"] is not None and int(mt["raw_tier"]) > 3
        res = "home" if mt["gh"] > mt["ga"] else ("draw" if mt["gh"] == mt["ga"] else "away")
        bp = base_preds.get(mid)
        old = {s: bp[f"{s}_prob"] for s in SEL3} if bp and all(bp[f"{s}_prob"] is not None for s in SEL3) else None
        new = dict(zip(SEL3, comb[mid])) if mid in comb else None
        pc = pin_close.get(mid)
        for bs, B in bases.items():
            m = B.get(mid)
            if not m or not m["best"]:
                continue
            ts = list(m["ts"].values()) + [m["pin_open"][s][1] for s in SEL3 if s in m["pin_open"]]
            t_dec = max(ts)
            pin_imp = BT.pinnacle_implied(m["pin_open"])
            sharp = BT.sharp_consensus_home(m)
            _set_ctx(mid, t_dec, m["earliest"], mt, news, sharp, pin_imp.get("home"))
            calib.set(t_dec)
            effb = _league_eff_bump(eff, mid, t_dec)
            for sel in SEL3:
                odds = m["best"].get(sel)
                if not odds:
                    continue
                ip = 1 / odds
                mv = BT.compute_odds_movement(mid, "1x2", sel, odds)
                aln = BT.compute_alignment(mid, sel.capitalize(), mv, {"tier": tier})
                anchor = pin_imp.get(sel)
                r = {"match_id": mid, "market": "1x2", "ko": mt["ko"], "bookset": bs, "selection": sel,
                     "odds": odds, "bookmaker": m["book"][sel], "tier": tier, "country": mt["country"] or "",
                     "data_tier": bp["data_tier"] if bp else None, "has_anchor": anchor is not None,
                     "pin_triple": all(s in m["pin_open"] for s in SEL3),
                     "static_ok": (not tier_veto) and not mv["veto"]
                     and not (sel == "home" and sharp is not None and sharp < -0.02),
                     "bump": BT.ALN_BUMP.get(aln["alignment_class"], 0.0) + effb,
                     "win": res == sel, "clv": np.nan if pc is None else odds * pc[SEL3.index(sel)] - 1,
                     "pin_open_odds": m["pin_open"][sel][0] if sel in m["pin_open"] else np.nan}
                for mod, pr in (("old", old), ("new", new)):
                    if pr is None:
                        r[f"{mod}_p"] = np.nan
                        continue
                    p = BT.calibrate_prob(pr[sel], ip, tier=tier, market=f"1x2_{sel}", anchor_implied=anchor,
                                          odds=odds) if mod == "old" else pr[sel]
                    kelly = BT.compute_kelly(p, odds)
                    r[f"{mod}_p"] = p
                    r[f"{mod}_gap"] = p - (anchor if anchor is not None else ip)
                    r[f"{mod}_stake_ok"] = bool(r["data_tier"] is None or kelly <= 0 or BT.compute_stake(
                        kelly, BT.BANKROLL, r["data_tier"], odds_penalty=mv.get("penalty", 0.0)) >= 1.0)
                rows.append(r)
    return pd.DataFrame(rows)


def table_ou(L: dict, calib) -> pd.DataFrame:
    """O/U 2.5 / 3.5 candidates at OPEN for book sets 'all' (pipeline OU price rules) and 'coolbet'."""
    BT.IMP.execute_query = BT._fake_execute_query
    matches, eff, news, base_preds = L["matches"], L["eff"], L["news"], L["base_preds"]
    legs = pd.read_parquet(OUB.OUT / "ou_legs_open.parquet")
    legs = legs[legs.market.isin(["over_under_25", "over_under_35"]) & legs.selection.isin(["over", "under"])]
    pred = pd.read_parquet(OUB.OUT / "ou_comb_test_open.parquet")
    pred = pred[pred.market.isin(["over_under_25", "over_under_35"])]
    served = dict(zip(zip(pred.match_id, pred.market),
                      np.where(pred.pin_over.notna(), pred.pin_over, pred.p_comb)))
    ids = list(matches)
    old_ou = _load_ou_old(ids)
    iso, n_iso = _ou35_isotonic()
    pcl, n_circ = _ou_pin_close()
    pclose = dict(zip(zip(pcl.match_id, pcl.market), pcl.pin_close_over))
    rows = []
    for (mid, mk), g in legs.groupby(["match_id", "market"]):
        mt = matches.get(mid)
        if not mt or mt["status"] != "finished" or mt["gh"] is None:
            continue
        if (mt["country"] or "") == "Scotland" and (mt["league_name"] or "") == "Premiership":
            continue
        line = 2.5 if mk == "over_under_25" else 3.5
        tot = mt["gh"] + mt["ga"]
        tier = int(mt["raw_tier"] or 1)
        tier_veto = mt["raw_tier"] is not None and int(mt["raw_tier"]) > 3
        pin = {s: (o, t) for b, s, o, t in zip(g.bookmaker, g.selection, g.odds, g.ts) if b == "Pinnacle"}
        earliest = {}
        for s, gs in g.groupby("selection"):
            j = int(np.argmin(gs.ts.to_numpy()))
            earliest[s] = (gs.bookmaker.iloc[j], float(gs.odds.iloc[j]), float(gs.ts.iloc[j]))
        pin_imp = {}
        if "over" in pin and "under" in pin:
            ro, ru = 1 / pin["over"][0], 1 / pin["under"][0]
            pin_imp = {"over": ro / (ro + ru), "under": ru / (ro + ru)}   # match_signals: proportional
        bp = base_preds.get(mid)
        data_tier = bp["data_tier"] if bp else None
        oo = old_ou.get(mid, {})
        key = "25" if mk == "over_under_25" else "35"
        p_close_over = pclose.get((mid, mk))
        p_new_over = served.get((mid, mk))
        for bs in ("all", "coolbet"):
            best, book, bts = {}, {}, {}
            for s in ("over", "under"):
                gs = g[g.selection == s]
                if bs == "coolbet":
                    gs = gs[gs.bookmaker == "Coolbet"]
                else:
                    gs = gs[gs.bookmaker.map(BT.is_publishable_book)]
                    if s not in pin:
                        continue                                       # OU-PIN-REQUIRED
                    gs = gs[(gs.bookmaker == "Pinnacle") | (gs.odds <= 2.0 * pin[s][0])]   # OU-PINNACLE-CAP
                if not len(gs):
                    continue
                j = int(np.argmax(gs.odds.to_numpy()))
                best[s], book[s], bts[s] = float(gs.odds.iloc[j]), gs.bookmaker.iloc[j], float(gs.ts.iloc[j])
            if bs == "all" and "over" in best and "under" in best and 1 / best["over"] + 1 / best["under"] < 1.02:
                continue                                               # implied-sum sanity
            if not best:
                continue
            t_dec = max(list(bts.values()) + [t for _, t in pin.values()])
            _set_ctx(mid, t_dec, earliest, mt, news, None, None)
            calib.set(t_dec)
            effb = _league_eff_bump(eff, mid, t_dec)
            for s, odds in best.items():
                ip = 1 / odds
                mv = BT.compute_odds_movement(mid, mk, s, odds)
                aln = BT.compute_alignment(mid, f"{s.capitalize()} {line}", mv, {"tier": tier})
                anchor = pin_imp.get(s)
                win = tot > line if s == "over" else tot < line
                r = {"match_id": mid, "market": mk, "ko": mt["ko"], "bookset": bs, "selection": s, "odds": odds,
                     "bookmaker": book[s], "tier": tier, "country": mt["country"] or "", "data_tier": data_tier,
                     "has_anchor": anchor is not None, "pin_triple": len(pin) == 2,
                     "static_ok": (not tier_veto) and not mv["veto"],
                     "bump": BT.ALN_BUMP.get(aln["alignment_class"], 0.0) + effb, "win": bool(win),
                     "clv": np.nan if p_close_over is None else
                     odds * (p_close_over if s == "over" else 1 - p_close_over) - 1,
                     "h_before": (mt["ko"] - bts[s]) / 3600,
                     "pin_open_odds": pin[s][0] if s in pin else np.nan}
                # OLD
                raw = oo.get(f"{s}{key}")
                if mk == "over_under_35":
                    ro_ = oo.get("over35")
                    p_old = None if ro_ is None else (float(iso.predict([ro_])[0]) if s == "over"
                                                      else 1 - float(iso.predict([ro_])[0]))
                elif raw is not None:
                    p_old = BT.calibrate_prob(raw, ip, tier=tier, market=f"over_under_25_{s}",
                                              anchor_implied=anchor, odds=odds)
                else:
                    p_old = None
                p_new = None if p_new_over is None else (p_new_over if s == "over" else 1 - p_new_over)
                for mod, p in (("old", p_old), ("new", p_new)):
                    if p is None or (isinstance(p, float) and math.isnan(p)):
                        r[f"{mod}_p"] = np.nan
                        continue
                    kelly = BT.compute_kelly(p, odds)
                    r[f"{mod}_p"] = p
                    r[f"{mod}_gap"] = p - (anchor if anchor is not None else ip)
                    r[f"{mod}_stake_ok"] = bool(data_tier is None or kelly <= 0 or BT.compute_stake(
                        kelly, BT.BANKROLL, data_tier, odds_penalty=mv.get("penalty", 0.0)) >= 1.0)
                rows.append(r)
    t = pd.DataFrame(rows)
    t.attrs.update(n_iso=n_iso, n_circular_close=n_circ)
    return t


# ═════════════════════════════ configs ═════════════════════════════
def family(bot: str) -> list[dict]:
    out = []
    for mod in MODELS:
        for step in PP_STEPS:
            out.append({"bot": bot, "model": mod, "unit": "pp", "step": step, "ev_thr": None,
                        "current": mod == "old" and step == 0.0})
        for thr in EV_THRESHOLDS:
            out.append({"bot": bot, "model": mod, "unit": "ev", "step": None, "ev_thr": thr, "current": False})
    assert len(out) == FAMILY_SIZE
    return out


def _pp_threshold(spec: dict, t: pd.DataFrame) -> np.ndarray:
    """The bot's own pp rule, per row (tier table incl. fav/long split for HOME only and the
    T3+ bump, exactly as run_morning; or a flat floor)."""
    if spec["flat"] is not None:
        return np.full(len(t), spec["flat"])
    thr = np.empty(len(t))
    for i, (tier, sel, odds) in enumerate(zip(t.tier, t.selection, t.odds)):
        th = dict(spec["table"].get(tier, {}))
        if tier >= 3 and th:
            th = {k: v + (0.05 if k.startswith("1x2") else 0.03) for k, v in th.items()}
        if spec["market"] == "1x2":
            thr[i] = (th.get("1x2_fav", 0.05) if odds < 2.0 else th.get("1x2_long", 0.08)) if sel == "home" \
                else th.get("1x2_long", 0.08)
        else:
            thr[i] = th.get("ou", 0.05)
    return thr


def picks_for(cfg: dict, t: pd.DataFrame, vip: set) -> pd.DataFrame:
    spec = BOTS[cfg["bot"]]
    d = t[(t.market == spec["market"]) & (t.bookset == spec["books"]) & t.selection.isin(spec["sels"])]
    if spec["leagues"]:
        d = d[d.country.isin(spec["leagues"])]
    d = d[(d.odds >= spec["odds"][0]) & (d.odds <= spec["odds"][1])]
    mod = cfg["model"]
    d = d[d[f"{mod}_p"].notna()].copy()
    if d.empty:
        return d
    p = d[f"{mod}_p"].to_numpy(float)
    edge = p - 1 / d.odds.to_numpy() if cfg["unit"] == "pp" else p * d.odds.to_numpy() - 1
    if cfg["unit"] == "pp":
        thr = _pp_threshold(spec, d) + cfg["step"]
        if spec["kind"] == "pipeline" and mod == "old":           # data-tier edge bump (non-rating path)
            thr = thr + d.data_tier.map(BT.DATA_TIER_EDGE_BUMP).fillna(0.0).to_numpy()
    else:
        thr = np.full(len(d), cfg["ev_thr"])
    ok = np.ones(len(d), bool)
    if spec["kind"] == "pipeline":
        gap = d[f"{mod}_gap"].to_numpy(float)
        ok &= d.static_ok.to_numpy(bool) & d[f"{mod}_stake_ok"].to_numpy(bool)
        ok &= p >= spec["min_prob"]
        ok &= gap <= BT.PINNACLE_VETO_GAP
        ok &= ~(d.has_anchor.to_numpy(bool) & (gap >= 0.06) & (gap < 0.10) & (edge < thr + 0.02))
        ok &= (p - 1 / d.odds.to_numpy()) <= BT.EDGE_CAP
        ok &= edge >= thr + d.bump.to_numpy()
    else:
        ok &= edge >= thr
    d = d[ok].assign(edge=edge[ok])
    if spec["public"] and len(d):
        keys = (list(zip(d.match_id, d.selection)) if spec["market"] == "1x2"
                else list(zip(d.match_id, d.market, d.selection)))
        d = d[[k not in vip for k in keys]]
    d = d.sort_values("edge", ascending=False).drop_duplicates(["match_id", "market"])
    d["pnl"] = np.where(d.win, d.odds - 1, -1.0)
    d["half"] = np.where(d.ko < SPLIT_EPOCH, "select", "confirm")
    return d


def _m(d: pd.DataFrame, half: str) -> dict:
    s = d[d.half == half] if len(d) else d
    c = s.clv.dropna() if len(s) else pd.Series(dtype=float)
    return {f"{half}_n": int(len(s)), f"{half}_n_clv": int(len(c)),
            f"{half}_clv": float(c.mean()) if len(c) else np.nan,
            f"{half}_roi": float(s.pnl.mean()) if len(s) else np.nan,
            f"{half}_hit": float(s.win.mean()) if len(s) else np.nan}


def _p_diff(a: np.ndarray, b: np.ndarray) -> float | None:
    """One-sided bootstrap p for H0: mean(a) - mean(b) <= 0 (independent resampling)."""
    if len(a) < 2 or len(b) < 2:
        return None
    rng = np.random.default_rng(SEED)
    da = a[rng.integers(0, len(a), (N_BOOT, len(a)))].mean(1)
    db = b[rng.integers(0, len(b), (N_BOOT, len(b)))].mean(1)
    return float(((da - db) <= 0).mean())


def main() -> int:
    L = BT.load_everything()
    calib = BT.CalibrationAsOf(_full_calibration())
    L["calib"] = calib
    ids = list(L["matches"])
    pc1, circ1 = _pin_close_1x2(ids)
    print(f"  1X2 Pinnacle closes: {len(pc1):,} usable, {circ1:,} dropped as circular (close = open row)")
    vip1, vipou = _vip_1x2(L), _vip_ou()
    print(f"  VIP holdings: 1X2 {len(vip1):,} (match, selection) · O/U {len(vipou):,} (match, line, side)")
    t1 = table_1x2(L, pc1, calib)
    tou = table_ou(L, calib)
    print(f"  candidate rows: 1X2 {len(t1):,} · O/U {len(tou):,} (ou35 isotonic fit n={tou.attrs['n_iso']:,}; "
          f"O/U closes dropped as circular {tou.attrs['n_circular_close']:,})")
    rows, picks_by = [], {}
    for bot, spec in BOTS.items():
        t = t1 if spec["market"] == "1x2" else tou
        vip = vip1 if spec["market"] == "1x2" else vipou
        for cfg in family(bot):
            d = picks_for(cfg, t, vip)
            name = f"{cfg['model']}|{cfg['unit']}|" + (f"{cfg['step']:+.2f}" if cfg["unit"] == "pp" else f"ev{cfg['ev_thr']:.2f}")
            picks_by[(bot, name)] = d
            rows.append({**cfg, "config": name, **_m(d, "select"), **_m(d, "confirm"),
                         "direct_share": float(d.bookmaker.isin(DIRECT).mean()) if len(d) else np.nan})
    ct = pd.DataFrame(rows)
    verdicts, ps = {}, {}
    for bot in BOTS:
        c = ct[ct.bot == bot]
        cur = c[c.current].iloc[0]
        elig = c[c.select_n_clv >= MIN_CLV_PICKS_SELECT].sort_values("select_clv", ascending=False)
        v = {"current_config": cur.config,
             "current": {k: cur[k] for k in cur.index if k.startswith(("select_", "confirm_"))}}
        if elig.empty:
            v.update(selected=None, reason=f"no config with >= {MIN_CLV_PICKS_SELECT} CLV picks on the select half")
            verdicts[bot] = v
            continue
        sel = elig.iloc[0]
        v["selected_config"] = sel.config
        v["selected"] = {k: sel[k] for k in sel.index if k.startswith(("select_", "confirm_"))}
        a = picks_by[(bot, sel.config)]; a = a[a.half == "confirm"].clv.dropna().to_numpy(float)
        b = picks_by[(bot, cur.config)]; b = b[b.half == "confirm"].clv.dropna().to_numpy(float)
        v["confirm_diff"] = float(a.mean() - b.mean()) if len(a) and len(b) else None
        # descriptive fragility flags (do not change the verdict): small confirm n, and picks whose
        # quote sits > 1.25x Pinnacle's opening price (phantom candidates; the generator bots have
        # no O/U outlier guard live either)
        sc = picks_by[(bot, sel.config)]; sc = sc[sc.half == "confirm"]
        v["confirm_n_clv_selected"], v["confirm_n_clv_current"] = int(len(a)), int(len(b))
        v["confirm_picks_quote_gt_1.25x_pin_open"] = int((sc.odds > 1.25 * sc.pin_open_odds).sum())
        v["confirm_clv_median_selected"] = float(np.median(a)) if len(a) else None
        v["fragile"] = bool(len(a) < MIN_CLV_PICKS_SELECT)
        if sel.config == cur.config:
            v["p_raw"] = None
            v["reason"] = "the current config is already the best on the select half"
        else:
            v["p_raw"] = _p_diff(a, b)
            if v["p_raw"] is not None:
                ps[bot] = v["p_raw"]
        verdicts[bot] = v
    # Holm m = 7 (bots); a bot with no test contributes p = 1 to the family
    full = {b: (ps[b] if b in ps else 1.0) for b in BOTS}
    order = sorted(full, key=full.get)
    run, adj = 0.0, {}
    for i, b in enumerate(order):
        run = max(run, min(1.0, (HOLM_M - i) * full[b])); adj[b] = run
    for bot, v in verdicts.items():
        v["p_holm"] = adj[bot] if bot in ps else None
        conf = (v.get("selected") or {}).get("confirm_clv")
        sw = bot in ps and adj[bot] < ALPHA and conf is not None and not np.isnan(conf) and conf > 0
        v["verdict"] = "SWITCH" if sw else "NO SWITCH"
        if "reason" not in v:
            if sw:
                v["reason"] = "selected config beats the current one on the confirm half at Holm-adjusted p < 0.05"
            elif conf is None or np.isnan(conf):
                v["reason"] = "selected config has no CLV picks on the confirm half"
            elif conf <= 0:
                v["reason"] = "selected config's confirm-half CLV is not positive"
            else:
                v["reason"] = "difference vs the current config not significant after Holm (m=7)"
        if bot == "bot_v10_ou":
            v["note"] = "un-retired on its best new-model config regardless (owner); published as TESTING; " \
                        "backtest shown only if it passes"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ct.to_csv(OUT_DIR / "model_bots_new_models_configs.csv", index=False)
    summ = {"generated_at": datetime.now(timezone.utc).isoformat(),
            "label": "Backtest (simulated) — #152 step 3, per-bot config family; NOT live results",
            "preregistration": "dev/active/model-bots-new-models-plan.md — 'Pre-registration — step 3'",
            "window": {"start": WINDOW_START, "end_inclusive": WINDOW_END, "select": "2026-08-31..2026-09-12",
                       "confirm": "2026-09-13..2026-09-24"},
            "design": {"family_size": FAMILY_SIZE, "pp_steps": PP_STEPS, "ev_thresholds": EV_THRESHOLDS,
                       "min_clv_picks_select": MIN_CLV_PICKS_SELECT, "holm_m": HOLM_M, "n_boot": N_BOOT,
                       "seed": SEED, "clv": "power de-vig of Pinnacle's last pre-KO price, separate-row guard"},
            "bots": {b: {k: (list(v) if isinstance(v, tuple) else v) for k, v in s.items() if k != "table"}
                     for b, s in BOTS.items()},
            "vip": {"1x2_holdings": len(vip1), "ou_holdings": len(vipou)},
            "verdicts": verdicts, "notes": NOTES,
            "diagnostics": {"pin_close_1x2_circular_dropped": circ1, "unswap": BT.SWAP_STATS,
                            "ou_circular_dropped": tou.attrs["n_circular_close"], "ou35_iso_n": tou.attrs["n_iso"],
                            "unexpected_sql_in_shims": dict(BT.UNEXPECTED_SQL)}}
    (OUT_DIR / "model_bots_new_models_summary.json").write_text(json.dumps(summ, indent=1, default=float))
    pd.set_option("display.width", 250)
    print(ct[["bot", "config", "select_n", "select_n_clv", "select_clv", "select_roi", "confirm_n", "confirm_n_clv",
              "confirm_clv", "confirm_roi"]].to_string(index=False))
    for b, v in verdicts.items():
        print(b, json.dumps(v, default=float))
    BT.CONN.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
