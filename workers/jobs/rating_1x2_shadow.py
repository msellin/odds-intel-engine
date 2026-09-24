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
COMB_TRAIN_FROM_EPOCH = 1777593600.0     # 2026-05-01: multi-book history starts here


DRY_RUN = False     # --dry-run: compute everything, write nothing


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
    console.print(f"combined_1x2 refresh ({COMB_VERSION}): {out}")
    return out


if __name__ == "__main__":
    import sys as _sys
    DRY_RUN = "--dry-run" in _sys.argv
    refresh() if "--refresh" in _sys.argv else run()
