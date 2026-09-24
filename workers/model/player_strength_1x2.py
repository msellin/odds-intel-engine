"""PLAYER / LINEUP STRENGTH for 1X2 ([[#141]] round 3c).

Pre-registered in dev/active/1x2-model-rebuild-plan.md ("ROUND 3c"). Evidence: team +
player ratings beat either alone (Arntzen & Hvattum 2021); a player-rating model
was profitable against bookmaker 1X2 prices (Holmes & McHale 2024).

Per player: a minutes-weighted, exponentially decayed mean of API-Football's
per-match rating (appearances of >= 20 minutes), shrunk toward PRIOR with the
weight of K matches — a new player reads as average, not as zero.
Per fixture and side:
    xi_*        mean rating of the starting XI            (ACTUAL: the confirmed XI)
    prev_*      the same for the team's previous XI       (PREV: what a morning prediction sees)
    xi_delta_*  this XI minus the team's own recent XI average (rotation / missing regulars)
LEAK GUARD: processed one UTC day at a time; a day's features are read before any of
that day's appearances update a player. Times are epoch seconds (no tz-aware
datetimes — pandas 3.0.4, RELIABILITY_LEDGER #26).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

PRIOR = 6.8
K = 3.0
MIN_MINUTES = 20
TEAM_ALPHA = 0.2


def _xi_lists(lineups: pd.DataFrame) -> dict:
    out = {}
    for fid, tid, xi in lineups[["fixture_id", "team_id", "xi"]].itertuples(index=False):
        if isinstance(xi, str) and xi:
            ids = [int(x) for x in xi.split(",") if x and x != "None"]
            if len(ids) >= 9:
                out[(int(fid), int(tid))] = ids
    return out


def _appearances(players: pd.DataFrame) -> dict:
    p = players[(players.minutes.fillna(0) >= MIN_MINUTES) & players.rating.notna() & players.player_id.notna()]
    out = defaultdict(list)
    for fid, pid, mins, r in p[["fixture_id", "player_id", "minutes", "rating"]].itertuples(index=False):
        out[int(fid)].append((int(pid), float(mins), float(r)))
    return out


def build_xi_features(fixtures: pd.DataFrame, players: pd.DataFrame, lineups: pd.DataFrame,
                      half_life_days: float = 365.0) -> pd.DataFrame:
    """fixtures: fixture_id, kickoff (epoch s), home_af, away_af. Returns one row per fixture."""
    fx = fixtures.dropna(subset=["home_af", "away_af"]).copy()
    fx["day"] = (fx.kickoff.astype(float) // 86400).astype(np.int64)
    fx = fx.sort_values(["kickoff", "fixture_id"], kind="mergesort")
    xis, apps = _xi_lists(lineups), _appearances(players)
    ps = {}                      # pid -> [decayed rating*minutes sum, decayed minutes/90 weight, last day]
    team_avg, last_xi = {}, {}
    lam = np.log(2) / half_life_days

    def est(pid, day):
        v = ps.get(pid)
        if v is None:
            return PRIOR
        d = np.exp(-lam * (day - v[2]))
        return (v[0] * d + K * PRIOR) / (v[1] * d + K)

    def strength(ids, day):
        return float(np.mean([est(p, day) for p in ids])) if ids else np.nan

    rows = []
    for day, grp in fx.groupby("day", sort=True):
        for fid, h, a in grp[["fixture_id", "home_af", "away_af"]].itertuples(index=False):
            fid, h, a = int(fid), int(h), int(a)
            r = {"fixture_id": fid}
            for side, t in (("h", h), ("a", a)):
                cur = xis.get((fid, t))
                r[f"xi_{side}"] = strength(cur, day)
                r[f"prev_{side}"] = strength(last_xi.get(t), day)
                ta = team_avg.get(t)
                r[f"xi_delta_{side}"] = (r[f"xi_{side}"] - ta) if (ta is not None and cur) else np.nan
                r[f"prev_delta_{side}"] = (r[f"prev_{side}"] - ta) if (ta is not None and t in last_xi) else np.nan
            rows.append(r)
        # ── update after the whole day has been read ──
        for fid, h, a in grp[["fixture_id", "home_af", "away_af"]].itertuples(index=False):
            fid = int(fid)
            for pid, mins, rating in apps.get(fid, ()):
                v = ps.get(pid)
                w = mins / 90.0
                if v is None:
                    ps[pid] = [rating * w, w, day]
                else:
                    d = np.exp(-lam * (day - v[2]))
                    ps[pid] = [v[0] * d + rating * w, v[1] * d + w, day]
            for t in (int(h), int(a)):
                cur = xis.get((fid, t))
                if cur:
                    s = strength(cur, day)
                    team_avg[t] = s if t not in team_avg else (1 - TEAM_ALPHA) * team_avg[t] + TEAM_ALPHA * s
                    last_xi[t] = cur
    out = pd.DataFrame(rows)
    out["xi_diff"] = out.xi_h - out.xi_a
    out["prev_diff"] = out.prev_h - out.prev_a
    return out
