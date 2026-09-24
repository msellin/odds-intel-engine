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


def _load(conn) -> pd.DataFrame:
    m = pd.read_sql("""
        SELECT m.id::text match_id, m.date kickoff, m.league_id::text league_id,
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
    m["kickoff"] = pd.to_datetime(m["kickoff"], utc=True)
    m.loc[m.upcoming, ["gh", "ga", "hh", "ha"]] = np.nan
    m["extra"] = False

    h = pd.read_sql("""SELECT af_fixture_id, af_league_id, kickoff, home_af, away_af, gh, ga, hh, ha
                         FROM rating_history_results WHERE kickoff < now()""", conn)
    if len(h):
        h["kickoff"] = pd.to_datetime(h["kickoff"], utc=True)
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


def run() -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from psycopg2.extras import execute_values

    with get_conn() as conn:
        m = _load(conn)
    n_hist = int(m.extra.sum())
    f = build_features(m, None, TUNED_PARAMS)
    f["dp_logodds"] = np.log(f.dp_ph.clip(1e-6) / f.dp_pa.clip(1e-6))
    f = f[~f.extra.astype(bool)]
    fin = f[~f.upcoming.astype(bool)]
    tr = fin[(fin.kickoff >= TRAIN_FROM) & gated(fin)]
    up = f[f.upcoming.astype(bool)]
    if up.empty:
        console.print("rating_1x2_shadow: no upcoming fixtures in the next 2 days")
        return {"written": 0}

    med = tr[D8PLUS_FEATURES].median()
    mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    mdl.fit(tr[D8PLUS_FEATURES].fillna(med), tr.y)
    P = mdl.predict_proba(up[D8PLUS_FEATURES].fillna(med))
    cls = list(mdl.classes_)
    ih, idr, ia = cls.index(0), cls.index(1), cls.index(2)
    g = gated(up).to_numpy()

    rows = [(mid, MODEL_VERSION, round(float(p[ih]), 5), round(float(p[idr]), 5), round(float(p[ia]), 5),
             int(nh), int(na), bool(gg))
            for mid, p, nh, na, gg in zip(up.match_id, P, up.n_home, up.n_away, g)]
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO rating_1x2_predictions
                    (match_id, model_version, p_home, p_draw, p_away, n_home, n_away, gated)
                VALUES %s
                ON CONFLICT (match_id, model_version) DO UPDATE SET
                    p_home = EXCLUDED.p_home, p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away,
                    n_home = EXCLUDED.n_home, n_away = EXCLUDED.n_away, gated = EXCLUDED.gated,
                    updated_at = now()""", rows)
        conn.commit()
    out = {"written": len(rows), "gated": int(g.sum()), "train_rows": len(tr), "history_rows": n_hist,
           "min_history": MIN_HISTORY}
    console.print(f"rating_1x2_shadow ({MODEL_VERSION}): {out}")
    return out


if __name__ == "__main__":
    run()
