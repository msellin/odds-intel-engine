"""RATING-1X2-SHADOW ([[#141]]) — daily forward predictions from the 1X2 rating model.

What it does, once a day before the morning pipeline:
  1. loads every finished match since 2022 plus the prior-season results in
     `rating_history_results` (history-only rows: they update ratings, are never
     trained on),
  2. runs the walk-forward rating pass (workers/model/ratings_1x2.py) through
     today, with the next ~2 days of scheduled fixtures appended so they receive
     features without updating state,
  3. fits the D8+ multinomial logit on gated finished rows since 2022-07-01,
  4. writes a 3-way probability per upcoming fixture to `rating_1x2_predictions`.

SHADOW ONLY. Nothing that stakes or publishes reads `rating_1x2_predictions`.
It exists to build a genuinely unseen forward record before the owner decides
whether the rating model replaces the XGBoost head in the served 1X2 blend
(dev/active/1x2-model-rebuild-plan.md). Measured on the 2026-08-31.. holdout it
beat the shipped head by ~0.06 log-loss; it does NOT beat Pinnacle's closing line.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from rich.console import Console

from workers.api_clients.db import get_conn
from workers.model.ratings_1x2 import (
    TUNED_PARAMS, D8_FEATURES, MIN_HISTORY, build_features, gated,
)

console = Console()
MODEL_VERSION = "r1x2_d8plus_v1"
D8PLUS_FEATURES = D8_FEATURES + ["dp_logodds", "dp_pd"]
TRAIN_FROM = "2022-07-01"
TRAIN_FROM_EPOCH = 1656633600.0          # 2022-07-01T00:00:00Z


def _load(conn) -> pd.DataFrame:
    m = pd.read_sql("""
        SELECT m.id::text match_id, extract(epoch FROM m.date)::float8 kickoff, m.league_id::text league_id,
               m.home_team_id::text home, m.away_team_id::text away,
               m.score_home gh, m.score_away ga, m.ht_score_home hh, m.ht_score_away ha,
               m.api_football_id af_fixture_id, m.home_team_api_id home_af,
               m.away_team_api_id away_af, l.api_football_id af_league_id,
               (m.status <> 'finished') AS upcoming
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.date >= '2022-01-01'
           AND ((m.status = 'finished' AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL)
                OR (m.status = 'scheduled' AND m.date < now() + interval '2 days'))
    """, conn)
    # `kickoff` stays EPOCH SECONDS end to end. pandas 3.0.4 on the VPS segfaults on
    # any take/filter/merge over a tz-aware datetime column (reproduced in isolation
    # 2026-09-24 on the first production run; 3.0.2 is fine), and this job runs
    # inside a subprocess of the scheduler precisely so that a native crash cannot
    # take the scheduler down. build_features() accepts epoch seconds natively.
    m.loc[m.upcoming, ["gh", "ga", "hh", "ha"]] = np.nan
    m["extra"] = False

    h = pd.read_sql("""SELECT af_fixture_id, af_league_id, extract(epoch FROM kickoff)::float8 kickoff,
                              home_af, away_af, gh, ga, hh, ha
                         FROM rating_history_results WHERE kickoff < now()""", conn)
    if len(h):
        h = h[~h.af_fixture_id.isin(set(m.af_fixture_id.dropna().astype(int)))]
        team = {}
        for side in ("home", "away"):
            t = m[[f"{side}_af", side, "kickoff"]].dropna().sort_values("kickoff")
            team.update(dict(zip(t[f"{side}_af"].astype(int), t[side])))
        lg = m.dropna(subset=["af_league_id"]).drop_duplicates("af_league_id").set_index("af_league_id")["league_id"]
        h = pd.DataFrame({
            "match_id": "af:" + h.af_fixture_id.astype(str), "kickoff": h.kickoff,
            "league_id": h.af_league_id.map(lg).fillna("afl:" + h.af_league_id.astype(str)),
            "home": h.home_af.map(team).fillna("aft:" + h.home_af.astype(str)),
            "away": h.away_af.map(team).fillna("aft:" + h.away_af.astype(str)),
            "gh": h.gh, "ga": h.ga, "hh": h.hh, "ha": h.ha, "upcoming": False, "extra": True,
        })
        m = pd.concat([m, h], ignore_index=True)
    return m


COMB_VERSION = "r1x2_comb_v1"
# [[#176]] The pipeline reads a combined (NEW+ 1X2 / combined O/U) row only if it was written at
# most this long ago. The refresh runs chained at the start of run_betting (seconds before the
# read), so a row older than this means the refresh failed and the row predates the odds.
SERVED_P_MAX_AGE_MIN = 20
COMB_TRAIN_FROM_EPOCH = 1777593600.0     # 2026-05-01: multi-book history starts here


DRY_RUN = False     # --dry-run: compute everything, write nothing


# ── #191 APPEND-ONLY HISTORY (migration 479) ─────────────────────────────────────────
# The two prediction tables below keep only the LATEST value per key and are overwritten every
# 30 min, so the value a bot saw at T-3h is gone by settlement. Every write therefore ALSO appends
# to model_prediction_history, inside the same transaction, with two filters done in SQL (so the
# DB clock and the matches row decide, not the caller):
#   * PRE-KICK-OFF ONLY — m.date > now() AND m.status = 'scheduled'; a post-kick-off refresh
#     never lands in the history, whatever the prediction table does;
#   * ONLY ON CHANGE — skipped when `probs` equals the latest history row for the same
#     (match, model_version, market), which keeps volume to real re-prices.
HISTORY_SQL = """
    INSERT INTO model_prediction_history
        (match_id, model_version, market, probs, grp, minutes_to_kickoff)
    SELECT m.id, v.model_version, v.market, v.probs::jsonb, v.grp,
           round((extract(epoch FROM m.date - now()) / 60.0)::numeric, 1)
      FROM (VALUES %s) AS v(match_id, model_version, market, probs, grp)
      JOIN matches m ON m.id = v.match_id::uuid
     WHERE m.date > now() AND m.status = 'scheduled'
       AND NOT EXISTS (
           SELECT 1 FROM (SELECT h.probs FROM model_prediction_history h
                           WHERE h.match_id = m.id AND h.model_version = v.model_version
                             AND h.market = v.market
                           ORDER BY h.written_at DESC LIMIT 1) last
            WHERE last.probs = v.probs::jsonb)"""


def _append_history(cur, rows: list[tuple]) -> int:
    """rows: (match_id, model_version, market, probs_dict, grp). Returns rows appended."""
    if not rows:
        return 0
    import json
    from psycopg2.extras import execute_values
    vals = [(str(mid), ver, mk, json.dumps(pr, sort_keys=True), grp) for mid, ver, mk, pr, grp in rows]
    n = 0
    for i in range(0, len(vals), 1000):   # execute_values pages; count each page
        execute_values(cur, HISTORY_SQL, vals[i:i + 1000], page_size=1000)
        n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return n


# ── COMBINED O/U ([[#152]]) — rides on the same walk-forward rating pass ─────────────
def _write_ou(d: pd.DataFrame, p_comb, grp) -> int:
    from workers.model.combined_ou import MODEL_VERSION as OU_VER, served_over
    served = served_over(d, p_comb)
    rows = [(mid, mk, OU_VER, round(float(s), 5), round(float(c), 5),
             (round(float(pp), 5) if pd.notna(pp) else None), str(g),
             (int(nb) if pd.notna(nb) else None), (round(float(lam), 4) if pd.notna(lam) else None))
            for mid, mk, s, c, pp, g, nb, lam in zip(d.match_id, d.market, served, p_comb, d.pin_over,
                                                     grp, d.n_books, d.lam)]
    if DRY_RUN or not rows:
        return len(rows)
    from psycopg2.extras import execute_values
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO ou_model_predictions
                    (match_id, market, model_version, p_over, p_comb, p_pin, grp, n_books, lam_total)
                VALUES %s
                ON CONFLICT (match_id, market, model_version) DO UPDATE SET
                    p_over = EXCLUDED.p_over, p_comb = EXCLUDED.p_comb, p_pin = EXCLUDED.p_pin,
                    grp = EXCLUDED.grp, n_books = EXCLUDED.n_books, lam_total = EXCLUDED.lam_total,
                    updated_at = now()""", rows)
            _append_history(cur, [(r[0], r[2], r[1], {"over": r[3], "comb": r[4], "pin": r[5]}, r[6])
                                  for r in rows])
        conn.commit()
    return len(rows)


def _ou_run(fin: pd.DataFrame, up: pd.DataFrame) -> dict:
    """Fit the O/U combiner on finished matches since 2026-05-01 and price the upcoming ones."""
    import json
    from workers.model import combined_ou as OU
    hist = fin[fin.kickoff >= COMB_TRAIN_FROM_EPOCH]
    hm = pd.DataFrame({"match_id": hist.match_id, "lam": (hist.dp_lh + hist.dp_la).to_numpy(),
                       "total": (hist.gh + hist.ga).to_numpy()})
    um = pd.DataFrame({"match_id": up.match_id, "lam": (up.dp_lh + up.dp_la).to_numpy()})
    with get_conn() as conn:
        legs = OU.fetch_legs(conn, hm.match_id.tolist() + um.match_id.tolist())
    cons = OU.consensus(legs)
    tr = OU.frame(hm.dropna(subset=["lam"]), cons)
    params = OU.fit(tr)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if not DRY_RUN:
                cur.execute("INSERT INTO combiner_ou_params (model_version, params, n_train) VALUES (%s, %s::jsonb, %s)",
                            (OU.MODEL_VERSION, json.dumps(params), int(len(hm))))
        conn.commit()
    te = OU.frame(um, cons)
    P, g = OU.predict(te, params)
    return {"ou_written": _write_ou(te, P, g), "ou_train": int(len(hm)),
            "ou_groups": {mk: {G: v["n"] for G, v in prm.items()} for mk, prm in params.items()}}


def ou_refresh() -> dict:
    """Every 30 min: re-apply the latest O/U combiner to CURRENT prices (lam from the last fit)."""
    import json
    from workers.model import combined_ou as OU
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT params FROM combiner_ou_params WHERE model_version = %s
                            ORDER BY fitted_at DESC LIMIT 1""", (OU.MODEL_VERSION,))
            row = cur.fetchone()
        if not row:
            return {"ou_written": 0}
        params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        um = pd.read_sql("""
            SELECT DISTINCT ON (p.match_id) p.match_id::text match_id, p.lam_total::float8 lam
              FROM ou_model_predictions p JOIN matches m ON m.id = p.match_id
             WHERE p.model_version = %(v)s AND m.status = 'scheduled'
               AND m.date > now() AND m.date < now() + interval '2 days'""",
                         conn, params={"v": OU.MODEL_VERSION})
        if um.empty:
            return {"ou_written": 0}
        legs = OU.fetch_legs(conn, um.match_id.tolist())
    te = OU.frame(um, OU.consensus(legs))
    P, g = OU.predict(te, params)
    return {"ou_written": _write_ou(te, P, g)}


def _write(rows: list[tuple]) -> None:
    if DRY_RUN:
        return
    from psycopg2.extras import execute_values
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO rating_1x2_predictions
                    (match_id, model_version, p_home, p_draw, p_away, n_home, n_away, gated, sources)
                VALUES %s
                ON CONFLICT (match_id, model_version) DO UPDATE SET
                    p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away,
                    n_home = EXCLUDED.n_home, n_away = EXCLUDED.n_away, gated = EXCLUDED.gated,
                    sources = EXCLUDED.sources, updated_at = now()""", rows)
            _append_history(cur, [(r[0], r[1], "1x2", {"home": r[2], "draw": r[3], "away": r[4]}, r[8])
                                  for r in rows])
        conn.commit()


def _market_and_af(d: pd.DataFrame) -> pd.DataFrame:
    """Attach consensus / Pinnacle (latest pre-kickoff price) and API-Football's prediction."""
    from workers.model.market_consensus_1x2 import fetch_legs, consensus
    ids = d.match_id.tolist()
    with get_conn() as conn:
        legs = pd.concat([fetch_legs(conn, ids[i:i + 3000], "close") for i in range(0, len(ids), 3000)]
                         or [pd.DataFrame()], ignore_index=True)
        af = pd.read_sql("""
            SELECT id::text match_id,
                   af_prediction->'predictions'->'percent'->>'home' af_h,
                   af_prediction->'predictions'->'percent'->>'draw' af_d,
                   af_prediction->'predictions'->'percent'->>'away' af_a,
                   af_prediction->'comparison'->'total'->>'home' af_th
              FROM matches WHERE id::text = ANY(%(ids)s) AND af_prediction IS NOT NULL""",
                         conn, params={"ids": ids})
    for k in ("af_h", "af_d", "af_a", "af_th"):
        af[k] = pd.to_numeric(af[k].str.rstrip("%"), errors="coerce") / 100
    d = d.merge(consensus(legs), left_on="match_id", right_index=True, how="left")
    return d.merge(af, on="match_id", how="left")


def _comb_rows(d: pd.DataFrame, params: dict) -> list[tuple]:
    from workers.model.combined_1x2 import predict
    P, grp = predict(d, params)
    gg = gated(d).to_numpy() | (grp != "none")
    return [(mid, COMB_VERSION, round(float(p[0]), 5), round(float(p[1]), 5), round(float(p[2]), 5),
             int(nh), int(na), bool(g), str(gr))
            for mid, p, nh, na, g, gr in zip(d.match_id, P, d.n_home, d.n_away, gg, grp)]


def run() -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from workers.model.combined_1x2 import fit as comb_fit

    with get_conn() as conn:
        m = _load(conn)
    n_hist = int(m.extra.sum())
    f = build_features(m, None, TUNED_PARAMS)
    f["dp_logodds"] = np.log(f.dp_ph.clip(1e-6) / f.dp_pa.clip(1e-6))
    f = f[~f.extra.astype(bool)]
    fin = f[~f.upcoming.astype(bool)]
    tr = fin[(fin.kickoff >= TRAIN_FROM_EPOCH) & gated(fin)]
    up = f[f.upcoming.astype(bool)].copy()
    if up.empty:
        console.print("rating_1x2_shadow: no upcoming fixtures in the next 2 days")
        return {"written": 0}

    med = tr[D8PLUS_FEATURES].median()
    mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    mdl.fit(tr[D8PLUS_FEATURES].fillna(med), tr.y)
    cls = list(mdl.classes_)
    order = [cls.index(0), cls.index(1), cls.index(2)]

    def rate(d):
        return mdl.predict_proba(d[D8PLUS_FEATURES].fillna(med))[:, order]

    up[["r_h", "r_d", "r_a"]] = rate(up)
    g = gated(up).to_numpy()
    _write([(mid, MODEL_VERSION, round(float(p[0]), 5), round(float(p[1]), 5), round(float(p[2]), 5),
             int(nh), int(na), bool(gg), None)
            for mid, p, nh, na, gg in zip(up.match_id, up[["r_h", "r_d", "r_a"]].to_numpy(),
                                          up.n_home, up.n_away, g)])
    out = {"written": len(up), "gated": int(g.sum()), "train_rows": len(tr), "history_rows": n_hist,
           "min_history": MIN_HISTORY}

    # ── COMBINED model (round 3b): refit the per-group combiner on finished matches
    # since 2026-05-01 (ratings from the fit above — mildly in-sample for these rows,
    # 10 coefficients on ~140k rows), store it, and apply it to the upcoming fixtures.
    hist = fin[fin.kickoff >= COMB_TRAIN_FROM_EPOCH].copy()
    hist[["r_h", "r_d", "r_a"]] = rate(hist)
    hist = _market_and_af(hist)
    params = comb_fit(hist)
    import json
    with get_conn() as conn:
        with conn.cursor() as cur:
            if not DRY_RUN:
                cur.execute("INSERT INTO combiner_1x2_params (model_version, params, n_train) VALUES (%s, %s::jsonb, %s)",
                            (COMB_VERSION, json.dumps(params), int(len(hist))))
        conn.commit()
    rows = _comb_rows(_market_and_af(up), params)
    _write(rows)
    out.update(comb_written=len(rows), comb_train=len(hist),
               comb_groups={k: v["n"] for k, v in params.items()})
    try:
        out.update(_ou_run(fin, up))          # [[#152]] combined O/U model
    except Exception as e:                    # never lose the 1X2 write to an O/U failure
        console.print(f"[yellow]combined O/U fit failed: {e}[/yellow]")
        out["ou_error"] = str(e)[:300]
    console.print(f"rating_1x2_shadow ({MODEL_VERSION} + {COMB_VERSION}): {out}")
    return out


def refresh() -> dict:
    """Every 30 min: re-apply the latest stored combiner to CURRENT prices for fixtures
    that already carry a rating prediction. Cheap — no rating pass, no refit."""
    import json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT params FROM combiner_1x2_params WHERE model_version = %s
                            ORDER BY fitted_at DESC LIMIT 1""", (COMB_VERSION,))
            row = cur.fetchone()
        if not row:
            console.print("combined_1x2 refresh: no fitted combiner yet")
            return {"written": 0}
        params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        d = pd.read_sql("""
            SELECT r.match_id::text match_id, r.p_home::float8 r_h, r.p_draw::float8 r_d,
                   r.p_away::float8 r_a, r.n_home, r.n_away
              FROM rating_1x2_predictions r JOIN matches m ON m.id = r.match_id
             WHERE r.model_version = %(v)s AND m.status = 'scheduled'
               AND m.date > now() AND m.date < now() + interval '2 days'""",
                        conn, params={"v": MODEL_VERSION})
    if d.empty:
        return {"written": 0}
    rows = _comb_rows(_market_and_af(d), params)
    _write(rows)
    out = {"written": len(rows), "groups": pd.Series([r[-1] for r in rows]).value_counts().to_dict()}
    try:
        out.update(ou_refresh())              # [[#152]] combined O/U at current prices
    except Exception as e:
        console.print(f"[yellow]combined O/U refresh failed: {e}[/yellow]")
    console.print(f"combined_1x2 refresh ({COMB_VERSION}): {out}")
    return out


if __name__ == "__main__":
    import sys as _sys
    DRY_RUN = "--dry-run" in _sys.argv
    refresh() if "--refresh" in _sys.argv else run()
