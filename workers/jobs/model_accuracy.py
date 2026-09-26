"""MODEL ACCURACY ([[#153]], migration 466) — how good is every production probability source, right now?

WHY. The owner asked for one admin view of "models, their performance and which bot has which model".
The holdout numbers in MODEL_WHITEPAPER are a snapshot of 08-31..09-24; nothing measured the models
FORWARD on settled matches, so a model could decay (or a served blend be worse than guessing, as the
old 1X2 ensemble turned out to be) without any page saying so. This job scores each source daily
into a private table; /admin/models reads it (no heavy SQL in a web request).

WHAT IS SCORED. Per model and market (1X2, or one O/U line), per window of settled kickoffs
(7 / 30 / 90 days): only probabilities written BEFORE kickoff (created_at / updated_at < match date).
  * n, mean log-loss, Brier (multi-class sum of squares for 1X2), and the BASE-RATE log-loss of the
    same rows (the window's own outcome frequencies — the "guessing" line a model must beat);
  * the honest benchmark: Pinnacle's de-vigged latest pre-kickoff price (devig.fair_prob — Shin
    3-way, power 2-way) scored on the SAME rows (pin_n, logloss_pin_rows = the model on those rows,
    pin_logloss = Pinnacle on those rows). A model is only interesting where it beats that.
Sources: `predictions` source='ensemble' (old 1X2 / O/U, per model_version), `rating_1x2_predictions`
(r1x2_d8plus_v1 NEW, r1x2_comb_v1 NEW+, gated rows), `ou_model_predictions` ou_comb_v1 (p_comb = the
model, p_over = SERVED: Pinnacle where priced else combined), and Pinnacle itself as a row. Since
[[#144]] also API-Football /predictions (1X2) and Tonybet's Sportradar fair probabilities (1X2 + O/U).

Epoch-free: no pandas (RELIABILITY_LEDGER #26). Read-only except its own table.

    python3 -m workers.jobs.model_accuracy            # compute + write
    python3 -m workers.jobs.model_accuracy --dry-run  # print, write nothing
"""
from __future__ import annotations

import argparse
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

WINDOWS = {"7d": 7, "30d": 30, "90d": 90}
OU_LINES = {"over_under_15": 1.5, "over_under_25": 2.5, "over_under_35": 3.5}
ENS_OU_MARKET = {"over15": "over_under_15", "over25": "over_under_25", "over35": "over_under_35"}
EPS = 1e-12
CHUNK = 2000


def _ll(p: float) -> float:
    return -math.log(min(max(p, EPS), 1.0))


def _outcomes(since: datetime) -> dict[str, dict]:
    """Settled matches in the widest window: {match_id: {kickoff, res (0/1/2), goals}}."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT id::text AS id, date, score_home, score_away FROM matches
            WHERE status = 'finished' AND score_home IS NOT NULL AND score_away IS NOT NULL
              AND date >= %s AND date < now()""", (since,)) or []
    out = {}
    for r in rows:
        h, a = int(r["score_home"]), int(r["score_away"])
        out[r["id"]] = {"kickoff": r["date"], "res": 0 if h > a else 1 if h == a else 2, "goals": h + a}
    return out


def _chunks(ids: list[str]):
    for i in range(0, len(ids), CHUNK):
        yield ids[i:i + CHUNK]


def _load_ensemble(ids: list[str]) -> tuple[dict, dict]:
    """Old ensemble: {(model, market): {mid: probs}} for 1X2 (per model_version) and O/U lines."""
    from workers.api_clients.db import execute_query
    tri: dict = defaultdict(dict)
    ou: dict = defaultdict(dict)
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT p.match_id::text AS mid, p.market, p.model_version, p.model_probability::float AS p
                     FROM predictions p JOIN matches m ON m.id = p.match_id
                    WHERE p.match_id = ANY(%s::uuid[]) AND p.source = 'ensemble' AND p.created_at < m.date
                      AND p.market IN ('1x2_home','1x2_draw','1x2_away','over15','over25','over35')""",
                (ch,)) or []:
            ver = r["model_version"] or "unversioned"
            if r["market"].startswith("1x2_"):
                tri[(f"ensemble 1X2 {ver}", r["mid"])][r["market"]] = r["p"]
            else:
                ou[(f"ensemble O/U {ver}", ENS_OU_MARKET[r["market"]])][r["mid"]] = r["p"]
    one = defaultdict(dict)
    for (model, mid), d in tri.items():
        if all(k in d for k in ("1x2_home", "1x2_draw", "1x2_away")):
            s = d["1x2_home"] + d["1x2_draw"] + d["1x2_away"]
            if s > 0:
                one[(model, "1x2")][mid] = (d["1x2_home"] / s, d["1x2_draw"] / s, d["1x2_away"] / s)
    return dict(one), dict(ou)


def _load_rating(ids: list[str]) -> dict:
    from workers.api_clients.db import execute_query
    out: dict = defaultdict(dict)
    names = {"r1x2_d8plus_v1": "NEW ratings r1x2_d8plus_v1", "r1x2_comb_v1": "NEW+ combined r1x2_comb_v1"}
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT r.match_id::text AS mid, r.model_version, r.p_home::float h, r.p_draw::float d,
                          r.p_away::float a
                     FROM rating_1x2_predictions r JOIN matches m ON m.id = r.match_id
                    WHERE r.match_id = ANY(%s::uuid[]) AND r.gated AND r.updated_at < m.date
                      AND r.model_version = ANY(%s)""", (ch, list(names))) or []:
            out[(names[r["model_version"]], "1x2")][r["mid"]] = (r["h"], r["d"], r["a"])
    return dict(out)


def _load_ou_comb(ids: list[str]) -> dict:
    from workers.api_clients.db import execute_query
    out: dict = defaultdict(dict)
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT o.match_id::text AS mid, o.market, o.p_comb::float pc, o.p_over::float ps
                     FROM ou_model_predictions o JOIN matches m ON m.id = o.match_id
                    WHERE o.match_id = ANY(%s::uuid[]) AND o.model_version = 'ou_comb_v1'
                      AND o.updated_at < m.date AND o.market = ANY(%s)""", (ch, list(OU_LINES))) or []:
            if r["pc"] is not None:
                out[("O/U combined ou_comb_v1 (model)", r["market"])][r["mid"]] = r["pc"]
            if r["ps"] is not None:
                out[("O/U combined ou_comb_v1 (served)", r["market"])][r["mid"]] = r["ps"]
    return dict(out)


def _pct(v) -> float | None:
    try:
        return float(str(v).rstrip("%")) / 100.0
    except (TypeError, ValueError):
        return None


def _load_af(ids: list[str]) -> dict:
    """[[#144]] API-Football /predictions 1X2 (5% steps, the Tier C fallback). ⚠️ No fetch timestamp is
    stored on matches.af_prediction, so 'written before kickoff' cannot be enforced — it is fetched in
    the pre-match enrichment, but a late refetch cannot be ruled out; read it as an upper bound."""
    from workers.api_clients.db import execute_query
    out: dict = {}
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT id::text AS mid, af_prediction->'predictions'->'percent'->>'home' h,
                          af_prediction->'predictions'->'percent'->>'draw' d,
                          af_prediction->'predictions'->'percent'->>'away' a
                     FROM matches WHERE id = ANY(%s::uuid[]) AND af_prediction IS NOT NULL""", (ch,)) or []:
            p = [_pct(r["h"]), _pct(r["d"]), _pct(r["a"])]
            if None in p or sum(p) <= 0:
                continue
            s = sum(p)
            out[r["mid"]] = tuple(x / s for x in p)
    return {("API-Football predictions", "1x2"): out} if out else {}


def _load_tonybet_fair(ids: list[str]) -> dict:
    """[[#144]] Tonybet's Sportradar fair probabilities (book_fair_probs, since 2026-09-23; one row per
    selection, latest only — updated_at < kickoff keeps it pre-match)."""
    from workers.api_clients.db import execute_query
    raw: dict = defaultdict(dict)
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT b.match_id::text AS mid, b.market, b.selection, b.fair_prob::float AS p
                     FROM book_fair_probs b JOIN matches m ON m.id = b.match_id
                    WHERE b.match_id = ANY(%s::uuid[]) AND b.bookmaker = 'Tonybet'
                      AND b.updated_at < m.date
                      AND (b.market = '1x2' AND b.handicap_line IS NULL
                           OR b.market = ANY(%s) AND b.handicap_line = (right(b.market, 2)::numeric / 10))""",
                (ch, list(OU_LINES))) or []:
            raw[(r["mid"], r["market"])][r["selection"]] = r["p"]
    out: dict = defaultdict(dict)
    for (mid, mk), q in raw.items():
        if mk == "1x2" and all(k in q for k in ("home", "draw", "away")):
            s = q["home"] + q["draw"] + q["away"]
            if s > 0:
                out[("Tonybet fair (Sportradar)", "1x2")][mid] = (q["home"] / s, q["draw"] / s, q["away"] / s)
        elif mk in OU_LINES and "over" in q and "under" in q and q["over"] + q["under"] > 0:
            out[("Tonybet fair (Sportradar)", mk)][mid] = q["over"] / (q["over"] + q["under"])
    return dict(out)


def _load_pinnacle(ids: list[str]) -> dict:
    """Pinnacle's latest pre-kickoff complete set, de-vigged: {market: {mid: probs}}."""
    from workers.api_clients.db import execute_query
    from workers.model.devig import fair_prob
    sides = {"1x2": ("home", "draw", "away"), **{m: ("over", "under") for m in OU_LINES}}
    raw: dict = defaultdict(dict)
    for ch in _chunks(ids):
        for r in execute_query(
                """SELECT DISTINCT ON (o.match_id, o.market, o.selection)
                          o.match_id::text AS mid, o.market, o.selection, o.odds::float AS odds
                     FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                    WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = 'Pinnacle'
                      AND o.market = ANY(%s) AND o.is_live IS NOT TRUE AND o."timestamp" < m.date
                    ORDER BY o.match_id, o.market, o.selection, o."timestamp" DESC""",
                (ch, list(sides))) or []:
            raw[(r["mid"], r["market"])][r["selection"]] = r["odds"]
    out: dict = defaultdict(dict)
    for (mid, mk), q in raw.items():
        if all(s in q for s in sides[mk]):
            p = fair_prob([q[s] for s in sides[mk]])
            if p:
                out[mk][mid] = tuple(p) if mk == "1x2" else p[0]
    return dict(out)


def _score(market: str, probs: dict, outcomes: dict, mids) -> tuple[float, float, int]:
    """(sum log-loss, sum Brier, n) over `mids`."""
    ll = br = 0.0
    n = 0
    for mid in mids:
        o = outcomes[mid]
        p = probs[mid]
        if market == "1x2":
            y = o["res"]
            ll += _ll(p[y])
            br += sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in range(3))
        else:
            y = 1.0 if o["goals"] > OU_LINES[market] else 0.0
            ll += _ll(p if y else 1 - p)
            br += (p - y) ** 2
        n += 1
    return ll, br, n


def _base_ll(market: str, outcomes: dict, mids) -> float:
    mids = list(mids)
    if not mids:
        return float("nan")
    if market == "1x2":
        c = [0, 0, 0]
        for m in mids:
            c[outcomes[m]["res"]] += 1
        f = [x / len(mids) for x in c]
        return sum(_ll(f[outcomes[m]["res"]]) for m in mids) / len(mids)
    y = [1.0 if outcomes[m]["goals"] > OU_LINES[market] else 0.0 for m in mids]
    f = sum(y) / len(y)
    return sum(_ll(f if v else 1 - f) for v in y) / len(y)


def compute(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=max(WINDOWS.values()))
    outcomes = _outcomes(since)
    ids = list(outcomes)
    models: dict = {}
    ens1, ensou = _load_ensemble(ids)
    for part in (ens1, ensou, _load_rating(ids), _load_ou_comb(ids), _load_af(ids), _load_tonybet_fair(ids)):
        models.update(part)
    pin = _load_pinnacle(ids)
    for mk, probs in pin.items():
        models[("Pinnacle (de-vigged close)", mk)] = probs
    rows = []
    for (model, market), probs in models.items():
        for win, days in WINDOWS.items():
            lo = now - timedelta(days=days)
            mids = [m for m in probs if m in outcomes and outcomes[m]["kickoff"] >= lo]
            if not mids:
                continue
            ll, br, n = _score(market, probs, outcomes, mids)
            pmap = pin.get(market, {})
            both = [m for m in mids if m in pmap]
            row = {"model": model, "market": market, "win": win, "n": n,
                   "logloss": ll / n, "brier": br / n, "base_logloss": _base_ll(market, outcomes, mids),
                   "pin_n": len(both), "logloss_pin_rows": None, "pin_logloss": None,
                   "first_kickoff": min(outcomes[m]["kickoff"] for m in mids),
                   "last_kickoff": max(outcomes[m]["kickoff"] for m in mids)}
            if both and not model.startswith("Pinnacle"):
                row["logloss_pin_rows"] = _score(market, probs, outcomes, both)[0] / len(both)
                row["pin_logloss"] = _score(market, pmap, outcomes, both)[0] / len(both)
            rows.append(row)
    return rows


_UPSERT = """INSERT INTO model_accuracy (model, market, win, n, logloss, brier, base_logloss, pin_n,
                  logloss_pin_rows, pin_logloss, first_kickoff, last_kickoff, computed_at)
   VALUES (%(model)s, %(market)s, %(win)s, %(n)s, %(logloss)s, %(brier)s, %(base_logloss)s, %(pin_n)s,
           %(logloss_pin_rows)s, %(pin_logloss)s, %(first_kickoff)s, %(last_kickoff)s, now())
   ON CONFLICT (model, market, win) DO UPDATE SET
     n = EXCLUDED.n, logloss = EXCLUDED.logloss, brier = EXCLUDED.brier,
     base_logloss = EXCLUDED.base_logloss, pin_n = EXCLUDED.pin_n,
     logloss_pin_rows = EXCLUDED.logloss_pin_rows, pin_logloss = EXCLUDED.pin_logloss,
     first_kickoff = EXCLUDED.first_kickoff, last_kickoff = EXCLUDED.last_kickoff,
     computed_at = EXCLUDED.computed_at"""


def run(dry_run: bool = False) -> dict:
    rows = compute()
    if dry_run:
        for r in sorted(rows, key=lambda r: (r["market"], r["win"], r["logloss"])):
            if r["win"] == "30d":
                pin = (f"  vs Pinnacle on {r['pin_n']}: {r['logloss_pin_rows']:.4f} / {r['pin_logloss']:.4f}"
                       if r["pin_logloss"] is not None else "")
                print(f"{r['market']:14} {r['model']:40} n={r['n']:5d} ll={r['logloss']:.4f} "
                      f"base={r['base_logloss']:.4f}{pin}")
        return {"rows": len(rows), "written": 0}
    from workers.api_clients.db import execute_write
    for r in rows:
        execute_write(_UPSERT, r)
    # a model that stopped writing keeps its last row; its computed_at shows how stale it is
    return {"rows": len(rows), "written": len(rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    print(run(dry_run=ap.parse_args().dry_run))


if __name__ == "__main__":
    main()
