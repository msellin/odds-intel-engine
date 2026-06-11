"""
CS2 sneak-peek v14 — adds LAN-vs-Online and home-region features.

Background: the match-details parser already classifies every HLTV match as
`stage='LAN'` or `stage='Online'` (see cs2_hltv_match_details.py:441). Some
teams over- or under-perform at LAN vs online (jet-lag, crowd pressure,
hardware on-stage). Similarly, regional events favour the home-region teams
(travel cost, schedule, crowd).

For each upcoming match between team1 and team2, compute as of kickoff:

  is_lan_event              — 1 if stage='LAN' else 0
  team1_lan_winrate_diff    = team1_lan_wr − team2_lan_wr, PIT-correct
                              (each team's historical match-win% on LAN-stage
                              matches before kickoff; 0 when either team has
                              <3 prior LAN matches)
  team1_region / team2_region — derived from cs2_hltv_matches.event_name via
                              a city/country regex map. Fallback "ONLINE" if
                              stage='Online' and no region match, else
                              "UNKNOWN".
  region_match_team1 / team2 — does the team's home region (its most-frequent
                              event region in PIT-correct HLTV history) match
                              the current event's region?
  region_advantage_diff     = region_match_team1 − region_match_team2
                              (in {-1, 0, +1})

PIT-correct: every aggregate uses only cs2_hltv_matches rows with match_date
strictly less than the eval row's kickoff_ts. Team→home-region is rebuilt at
each match by streaming the team's historical region appearances; we cache
last-known counts so this stays O(n log n) overall.

Compares (walk-forward, 70/30 split like v13):
  baseline (hltv_v1 direct)
  v8 reference                       — v8 stacked logistic
  v14: v8 + is_lan_event + lan_wr_diff
  v14: v8 + is_lan_event + lan_wr_diff + region_advantage_diff
  v14 features alone (sanity)

Run:
    python3 scripts/esports/cs2_sneak_peek_v14_lan_region.py [--since 2026-04-01]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from cs2_sneak_peek_v5 import (  # type: ignore
    load_matches_with_features, load_team_map, _logit,
)
from cs2_sneak_peek_v6 import load_team_kd_map  # type: ignore
from cs2_sneak_peek_v7 import load_pistol_map, load_tier_map  # type: ignore
from cs2_sneak_peek_v8 import load_team_stats_direct  # type: ignore

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


# Team-name normaliser (same shape as v13 — strips bo3gg's " Team"/" Esports"
# suffixes so HLTV names line up). Without this, region lookups cover <5% of
# rows because bo3gg writes "1win Team" where HLTV stores "1win".
_SUFFIX_RE = re.compile(
    r"\s+(team|esports|esport|gaming|gamingclub|club|pro|academy)$",
    re.IGNORECASE,
)


def _norm_team(name: str | None) -> str:
    """Return a stripped, lowercase, suffix-free key for join purposes."""
    if not name:
        return ""
    s = name.strip().lower()
    for _ in range(2):
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return re.sub(r"[^a-z0-9]", "", s)


# Region detection from event_name. Order matters — CIS is checked before EU
# because CIS LAN events in Russia/Belarus would otherwise grab the Europe
# regex via "European" lookups.
#
# Coverage on April-2026+ events: ~67% explicit, remainder falls through to
# ONLINE (when stage='Online') or UNKNOWN (when stage='LAN' without a
# recognisable city). The team→home-region heuristic still bridges UNKNOWN
# matches because it accumulates over the team's full history.
REGION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CIS", re.compile(
        r"\b(CIS|Moscow|Saint\s+Petersburg|St\s+Petersburg|Kiev|Kyiv|Minsk|"
        r"Almaty|Astana|Tashkent|Atyrau|Russia|Russian|Ukrain\w*|Belarus\w*|"
        r"Kazakh\w*|Uzbek\w*|Winline|BetBoom|Betera|ALIKSON|Allur|"
        r"1xBet\s+FRAG|1xBet\s+Spring|1xBet\s+Winter|1xBet\s+Contest|"
        r"RUSH\s+B\s+Summit|Fonbet)\b",
        re.IGNORECASE,
    )),
    ("APAC", re.compile(
        r"\b(Asia|Asian|APAC|Shanghai|Tokyo|Bangkok|Manila|Seoul|Hong\s+Kong|"
        r"Singapore|Mumbai|Delhi|Jakarta|Beijing|Mongolia|Mongolian|India|"
        r"Indian|China|Chinese|Japan|Japanese|Korea|Korean|Thailand|Thai|"
        r"Vietnam|Indonesia|Malaysia|Filipinas|Philippine|Oceania|ANZ|ANZC|"
        r"Australia|Brisbane|Sydney|Melbourne|New\s+Zealand|TESFED|Yuqilin|"
        r"GangKui|XSE|Hero\s+Esports|NODWIN|Galaxy\s+Battle|Myskill|INUI|"
        r"eXTREMESLAND|CS\s+Asia|Perfect\s+World|A1\s+Gaming|Telkom)\b",
        re.IGNORECASE,
    )),
    ("SA", re.compile(
        r"\b(South\s+America|S(ã|a)o\s+Paulo|Rio|Buenos\s+Aires|Curitiba|"
        r"Recife|Cuiab(á|a)|Latam|Brasil|Brazil|Brazilian|Argentin\w*|"
        r"Chile|Chilean|Colombia\w*|Peru|Peruvian|Uruguay|Paraguay|"
        r"Liga\s+Gamers\s+Club|Gamers\s+Club|Aorus\s+League|CBCS|FERJEE|"
        r"MESA\s+Pro|Circuit\s+X|Dust2\.us)\b",
        re.IGNORECASE,
    )),
    ("NA", re.compile(
        r"\b(North\s+America|Dallas|New\s+York|Toronto|Vancouver|Miami|"
        r"Atlanta|Las\s+Vegas|Vegas|Los\s+Angeles|Chicago|Boston|Seattle|"
        r"Philadelphia|Philly|Fragadelphia|Fragville|Mexico|Canada|USA|"
        r"United\s+States|American|Canadian|Mexican)\b",
        re.IGNORECASE,
    )),
    ("EU", re.compile(
        r"\b(Europe|European|EU\b|Berlin|Cologne|Stockholm|Katowice|Krak(ó|o)w|"
        r"Malta|Copenhagen|Rotterdam|London|Paris|Madrid|Hamburg|Munich|"
        r"Belgrade|Bucharest|Cluj-Napoca|Bergen|Helsinki|Tallinn|Tartu|Riga|"
        r"Vilnius|Warsaw|Lisbon|Athens|Vienna|Prague|Budapest|Lviv|"
        r"Istanbul|IstanbuLAN|DACH|Nordic|Balkan|Baltic|Baltics|Mediterranean|"
        r"Iberian|France|French|Germany|German|Spain|Spanish|Italy|Italian|"
        r"Netherlands|Dutch|Poland|Polish|Portugal|Portuguese|Greek|"
        r"Austria|Austrian|Czech|Hungary|Hungarian|Estonia|Estonian|"
        r"Latvia|Latvian|Lithuania|Lithuanian|Romania|Romanian|Bulgaria|"
        r"Bulgarian|Serbia|Serbian|Croatia|Croatian|Norway|Norwegian|"
        r"Sweden|Swedish|Finland|Finnish|Denmark|Danish|Iceland|Switzerland|"
        r"Swiss|Belgium|Belgian|Ireland|Irish|Scotland|Scottish|Slovak\w*|"
        r"Sloven\w*|UKIC|Svenska|Esportligaen|Eliteserien|Bundesliga|Polska|"
        r"Mistrzostwa|Betclic|Birch|Parken|Esplay|Tipsport|MČR|kleverr|"
        r"Optibet|POWER\s+Ligaen|Comic\s+Con\s+Baltics|Bergen\s+Games|"
        r"Gamers\s+Assembly|Copenhagen\s+Gaming|IEM\s+Krak)\b",
        re.IGNORECASE,
    )),
]


def detect_event_region(event_name: str | None, stage: str | None) -> str:
    """Return one of EU/NA/SA/APAC/CIS/ONLINE/UNKNOWN.
    ONLINE when no explicit region word fires AND stage='Online' (so the
    feature still has a value); UNKNOWN otherwise (e.g. a LAN event whose
    name we don't recognise)."""
    if event_name:
        for region, pat in REGION_PATTERNS:
            if pat.search(event_name):
                return region
    if (stage or "").lower() == "online":
        return "ONLINE"
    return "UNKNOWN"


def load_hltv_event_history() -> tuple[list[dict], dict, dict]:
    """Pull every cs2_hltv_matches row once. Returns:

    1. `events` — list of dicts ordered by match_date asc, each with
       {hltv_match_id, match_date, team1_key, team2_key, t1_won, region, is_lan}
       (team keys are _norm_team-stripped). Used to build per-team LAN
       win-rate streams and team→region histograms via bisect.
    2. `hltv_match_idx` — {(t1_key, t2_key, date_iso_day): (hltv_match_id,
       orient)} so we can pull THIS match's stage + event_name when scoring.
       (Mirrors v13's join helper.)
    3. `events_by_id` — {hltv_match_id: event-dict} for O(1) match→stage
       lookup once the hltv_match_idx points at an id."""
    rows = execute_query(
        """
        SELECT hltv_match_id, match_date, team1_name, team2_name,
               winner_name, stage, event_name
        FROM cs2_hltv_matches
        WHERE match_date IS NOT NULL
          AND team1_name IS NOT NULL
          AND team2_name IS NOT NULL
        ORDER BY match_date
        """,
        None,
    )

    events: list[dict] = []
    idx: dict = {}
    events_by_id: dict = {}
    for r in rows:
        t1 = r["team1_name"]
        t2 = r["team2_name"]
        k1, k2 = _norm_team(t1), _norm_team(t2)
        if not (k1 and k2):
            continue
        stage = (r["stage"] or "").strip()
        is_lan = stage.lower() == "lan"
        region = detect_event_region(r["event_name"], stage)
        winner = r["winner_name"]
        t1_won = (winner == t1) if winner else None
        # If winner_name doesn't match either side exactly, treat as unknown
        # but still let it count toward region history.
        ev = {
            "hltv_match_id": r["hltv_match_id"],
            "match_date":   r["match_date"],
            "team1_key":    k1,
            "team2_key":    k2,
            "t1_won":       t1_won,
            "region":       region,
            "is_lan":       is_lan,
        }
        events.append(ev)
        events_by_id[r["hltv_match_id"]] = ev
        d = r["match_date"].date()
        idx[(k1, k2, d)] = (r["hltv_match_id"], "fwd")
        idx[(k2, k1, d)] = (r["hltv_match_id"], "rev")

    print(f"  HLTV events loaded: {len(events)} matches, "
          f"{len(idx) // 2} indexed")
    return events, idx, events_by_id


def build_team_streams(events: list[dict]) -> dict:
    """For each team, a sorted-by-date stream of
    (match_date, is_lan, won, region) tuples (one per match the team played).
    Use bisect to read PIT-correct slices."""
    streams: dict = defaultdict(list)
    for e in events:
        md, region, is_lan = e["match_date"], e["region"], e["is_lan"]
        # team1 perspective
        if e["t1_won"] is not None:
            streams[e["team1_key"]].append((md, is_lan, bool(e["t1_won"]), region))
            streams[e["team2_key"]].append((md, is_lan, not bool(e["t1_won"]), region))
        else:
            # winner unknown — still useful for region-frequency but not LAN-wr
            streams[e["team1_key"]].append((md, is_lan, None, region))
            streams[e["team2_key"]].append((md, is_lan, None, region))
    for k in streams:
        streams[k].sort(key=lambda x: x[0])
    return dict(streams)


def compute_lan_winrate(team_key: str, kickoff_ts,
                         streams: dict, min_n: int = 3
                         ) -> tuple[float | None, int]:
    """PIT-correct LAN match-win% for the team. Returns (winrate, n_lan_priors).
    None when fewer than min_n LAN matches before kickoff."""
    if not team_key or kickoff_ts is None:
        return None, 0
    stream = streams.get(team_key)
    if not stream:
        return None, 0
    dates = [t[0] for t in stream]
    hi = bisect.bisect_left(dates, kickoff_ts)
    if hi == 0:
        return None, 0
    wins = 0
    n = 0
    for md, is_lan, won, _r in stream[:hi]:
        if not is_lan or won is None:
            continue
        n += 1
        if won:
            wins += 1
    if n < min_n:
        return None, n
    return wins / n, n


def compute_team_home_region(team_key: str, kickoff_ts, streams: dict,
                              min_n: int = 5) -> str | None:
    """Return the team's most-frequent non-ONLINE/UNKNOWN event region from
    its history BEFORE kickoff_ts. None when fewer than min_n regional
    appearances. Ignores ONLINE/UNKNOWN so the home region reflects where
    the team actually travels for LAN events / where its regional online
    league sits — both signals of home base."""
    if not team_key or kickoff_ts is None:
        return None
    stream = streams.get(team_key)
    if not stream:
        return None
    dates = [t[0] for t in stream]
    hi = bisect.bisect_left(dates, kickoff_ts)
    if hi == 0:
        return None
    counts: dict[str, int] = defaultdict(int)
    for md, _is_lan, _won, region in stream[:hi]:
        if region in (None, "UNKNOWN", "ONLINE"):
            continue
        counts[region] += 1
    if not counts:
        return None
    # Pick the modal region, requiring min_n total regional appearances
    total = sum(counts.values())
    if total < min_n:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                events_idx, streams, hltv_match_idx):
    """events_idx is unused (kept signature symmetric with v13 — match-level
    region/stage flows through hltv_match_idx → events lookup below).

    For each match we look up its own HLTV row (stage, region) so that the
    is_lan_event and region_match_* features describe THIS event, not the
    aggregate."""
    out = []
    for m in matches:
        if m["win_prob1"] is None:
            continue
        y = 1 if m["winner"] == "team1" else 0
        saved = float(m["win_prob1"])

        # v5/v7/v8 base
        t1f = float(m["t1_form"]) if m["t1_form_n"] >= 3 else 0.5
        t2f = float(m["t2_form"]) if m["t2_form_n"] >= 3 else 0.5
        form_diff = t1f - t2f
        h2h_diff = (float(m["h2h_t1"]) - 0.5) if (m["h2h_n"] or 0) >= 2 else 0.0
        rest_diff = (min(float(m["t1_days_since"]), 30.0) - min(float(m["t2_days_since"]), 30.0)) / 30.0
        rank_diff = (
            float(m["hltv_rank2"] - m["hltv_rank1"]) / 100.0
            if (m["hltv_rank1"] and m["hltv_rank2"]) else 0.0
        )
        t1_tm, t2_tm = tm.get(m["team1"]), tm.get(m["team2"])
        tm_diff = (t1_tm - t2_tm) / 100.0 if (t1_tm is not None and t2_tm is not None) else 0.0
        bo_centered = float((m["best_of"] or 3) - 3)

        p1, p2 = pistol.get(m["team1"]), pistol.get(m["team2"])
        pistol_diff = 0.0
        if p1 and p2 and p1["n"] >= 50 and p2["n"] >= 50:
            pistol_diff = (p1["overall"] - p2["overall"]) / 100.0

        kdate = m["kickoff_time"].date() if m["kickoff_time"] else None
        tier = tier_map.get((m["team1"], m["team2"], kdate)) or tier_map.get((m["team2"], m["team1"], kdate))
        tier_s = 1.0 if tier == "s" else 0.0
        tier_a = 1.0 if tier == "a" else 0.0
        tier_b = 1.0 if tier == "b" else 0.0
        tier_c = 1.0 if tier == "c" else 0.0
        tier_d = 1.0 if tier == "d" else 0.0

        d1 = direct.get((m["team1"] or "").lower())
        d2 = direct.get((m["team2"] or "").lower())
        t1_kd = kd_map.get(m["team1"]) or (d1["kd"] if d1 and d1.get("maps", 0) >= 30 else None)
        t2_kd = kd_map.get(m["team2"]) or (d2["kd"] if d2 and d2.get("maps", 0) >= 30 else None)
        kd_diff = (t1_kd - t2_kd) if (t1_kd is not None and t2_kd is not None) else 0.0

        # NEW v14: pull the HLTV row corresponding to this match (for stage +
        # event_name). hltv_match_id was resolved upstream via
        # cs2_match_id_bridge — the old (k1,k2,date) exact-string join only
        # covered ~2% of rows.
        k1, k2 = _norm_team(m["team1"]), _norm_team(m["team2"])
        kickoff_ts = m["kickoff_time"]
        hltv_id = m.get("hltv_match_id")
        is_lan_event = 0
        event_region = "UNKNOWN"
        hltv_covered = 0
        if hltv_id is not None:
            ev = events_idx.get(hltv_id)
            if ev is not None:
                is_lan_event = 1 if ev["is_lan"] else 0
                event_region = ev["region"]
                hltv_covered = 1

        # PIT-correct LAN win-rate per team (using cs2_hltv_matches outcomes)
        wr1, n_lan1 = compute_lan_winrate(k1, kickoff_ts, streams)
        wr2, n_lan2 = compute_lan_winrate(k2, kickoff_ts, streams)
        team1_lan_wr = wr1 if wr1 is not None else 0.5
        team2_lan_wr = wr2 if wr2 is not None else 0.5
        team1_lan_winrate_diff = (
            float(team1_lan_wr - team2_lan_wr)
            if (wr1 is not None and wr2 is not None) else 0.0
        )
        lan_wr_covered = 1 if (wr1 is not None and wr2 is not None) else 0

        # Team→home-region (PIT-correct mode of historical event regions)
        team1_home = compute_team_home_region(k1, kickoff_ts, streams)
        team2_home = compute_team_home_region(k2, kickoff_ts, streams)
        region_match_team1 = (
            1 if (team1_home is not None
                  and event_region not in ("ONLINE", "UNKNOWN")
                  and team1_home == event_region)
            else 0
        )
        region_match_team2 = (
            1 if (team2_home is not None
                  and event_region not in ("ONLINE", "UNKNOWN")
                  and team2_home == event_region)
            else 0
        )
        region_advantage_diff = float(region_match_team1 - region_match_team2)
        region_covered = 1 if (
            team1_home is not None and team2_home is not None
            and event_region not in ("ONLINE", "UNKNOWN")
        ) else 0

        out.append({
            "kickoff": kickoff_ts, "y": y,
            "saved": saved, "logit_saved": _logit(saved),
            "form_diff": form_diff, "h2h_diff": h2h_diff,
            "rest_diff": rest_diff, "rank_diff": rank_diff,
            "tm_diff": tm_diff, "bo_centered": bo_centered,
            "pistol_diff": pistol_diff,
            "tier_s": tier_s, "tier_a": tier_a, "tier_b": tier_b,
            "tier_c": tier_c, "tier_d": tier_d,
            "kd_diff": kd_diff,
            # NEW v14
            "is_lan_event":            float(is_lan_event),
            "team1_lan_winrate_diff":  float(team1_lan_winrate_diff),
            "lan_wr_diff":             float(team1_lan_winrate_diff),  # alias
            "region_advantage_diff":   float(region_advantage_diff),
            "team1_region":            event_region,  # for sanity printing
            "team2_region":            event_region,
            "team1_home_region":       team1_home or "UNKNOWN",
            "team2_home_region":       team2_home or "UNKNOWN",
            "event_region":            event_region,
            "hltv_covered":            hltv_covered,
            "lan_wr_covered":          lan_wr_covered,
            "region_covered":          region_covered,
        })
    return out


def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
    }


def evaluate(rows, keys, name):
    cut = int(len(rows) * 0.7)
    if cut < 50:
        return {"skipped": True, "n": len(rows)}
    full_keys = ["logit_saved"] + keys
    X = np.array([[r[k] for k in full_keys] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    m = LogisticRegression(max_iter=2000)
    m.fit(X[:cut], y[:cut])
    p = m.predict_proba(X[cut:])[:, 1]
    return {
        "name": name, "n": len(rows), "n_train": cut, "n_test": len(rows) - cut,
        "coefs": dict(zip(full_keys, m.coef_[0].tolist())),
        "metrics": _metrics(y[cut:], p),
    }


def persist(name, n, m, since: date, keys=None, coefs=None, n_train=None):
    try:
        execute_write(
            """INSERT INTO cs2_model_backtest_history
                (run_id, feature_set, n_matches, n_train, n_test,
                 auc, logloss, brier, accuracy, since_date, feature_keys, coefs)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (RUN_ID, name, n, n_train, (n - (n_train or 0)) or None,
             m.get("auc"), m["logloss"], m["brier"], m["acc"], since,
             keys, json.dumps(coefs) if coefs else None),
        )
    except Exception as e:
        print(f"  [warn] persist failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    # v13 defaults to 2025-06-01 to get a comparable n=2300+ sample for the
    # v8-baseline regression. We do the same — HLTV-side coverage will be low
    # in the early months (HLTV table is dense only from April 2026), but the
    # PIT-correct lookups simply fall back to neutral for un-covered rows so
    # the regressor sees the LAN/region signal where it exists and learns
    # zero-coefficient noise elsewhere. Run with --since 2026-04-01 if you
    # specifically want the dense subset (n≈68 in current data — too small).
    ap.add_argument("--since", default="2025-06-01")
    args = ap.parse_args()
    since_d = date.fromisoformat(args.since)

    print("loading team_map…");     tm = load_team_map();          print(f"  {len(tm)} teams")
    print("loading pistol stats…"); pistol = load_pistol_map();    print(f"  {len(pistol)} teams")
    print("loading tier map…");     tier_map = load_tier_map();    print(f"  {len(tier_map) // 2} matches")
    print("loading kd_map…");       kd_map = load_team_kd_map()
    print("loading direct stats…"); direct = load_team_stats_direct()
    print("loading HLTV event history…")
    events, hltv_match_idx, events_idx = load_hltv_event_history()
    print(f"  events_idx size: {len(events_idx)}")

    print("building per-team streams…")
    streams = build_team_streams(events)
    print(f"  {len(streams)} teams streamed")

    # Sanity: region distribution across all events in the windowed period
    region_counts: dict[str, int] = defaultdict(int)
    for e in events:
        if e["match_date"].date() >= since_d:
            region_counts[e["region"]] += 1
    print("  event-region distribution (since "
          f"{since_d}): {dict(region_counts)}")

    print("loading matches + PIT features…")
    matches = load_matches_with_features(args.since)
    # Bridge: bo3gg_id -> hltv_match_id via cs2_match_id_bridge (replaces the
    # old (team1, team2, date) exact-string join that capped coverage at <3%).
    print("  enriching matches with hltv_match_id via cs2_match_id_bridge…")
    bridge = {r["bo3gg_id"]: r["hltv_match_id"] for r in execute_query(
        "SELECT bo3gg_id, hltv_match_id FROM cs2_match_id_bridge"
    )}
    matched = 0
    for m in matches:
        bid = m.get("bo3gg_id")
        m["hltv_match_id"] = bridge.get(str(bid)) if bid is not None else None
        if m["hltv_match_id"] is not None:
            matched += 1
    print(f"  bridge coverage: {matched}/{len(matches)} ({matched/max(len(matches),1):.1%})")
    rows = build_rows(matches, tm, pistol, tier_map, kd_map, direct,
                      events_idx, streams, hltv_match_idx)
    print(f"  {len(rows)} matches with saved_prob\n")

    cov_hltv = sum(1 for r in rows if r["hltv_covered"])
    cov_lan = sum(1 for r in rows if r["lan_wr_covered"])
    cov_region = sum(1 for r in rows if r["region_covered"])
    n_lan_event = sum(1 for r in rows if r["is_lan_event"] == 1.0)
    n_lan_nz = sum(1 for r in rows if r["team1_lan_winrate_diff"] != 0.0)
    n_region_nz = sum(1 for r in rows if r["region_advantage_diff"] != 0.0)

    # Region sanity check
    matched_regions = sorted({r["event_region"] for r in rows})
    n_unknown = sum(1 for r in rows if r["event_region"] == "UNKNOWN")
    n_online = sum(1 for r in rows if r["event_region"] == "ONLINE")
    print(f"  coverage:")
    print(f"    HLTV match joined (stage+event_name available): "
          f"{cov_hltv}/{len(rows)} ({cov_hltv/max(len(rows),1):.1%})")
    print(f"    is_lan_event=1:                                  {n_lan_event}/{len(rows)} ({n_lan_event/max(len(rows),1):.1%})")
    print(f"    lan_wr_diff   both teams have ≥3 LAN priors:     {cov_lan}/{len(rows)} ({cov_lan/max(len(rows),1):.1%})")
    print(f"    lan_wr_diff   non-zero:                           {n_lan_nz}/{len(rows)} ({n_lan_nz/max(len(rows),1):.1%})")
    print(f"    region_advantage_diff covered (both teams have home region + event has region): {cov_region}/{len(rows)} ({cov_region/max(len(rows),1):.1%})")
    print(f"    region_advantage_diff non-zero:                   {n_region_nz}/{len(rows)} ({n_region_nz/max(len(rows),1):.1%})")
    print(f"    distinct event_regions on backtest rows: {matched_regions}")
    print(f"    UNKNOWN/ONLINE on backtest rows: {n_unknown} UNKNOWN, {n_online} ONLINE\n")

    v8_keys = ["form_diff","h2h_diff","tm_diff","rest_diff","rank_diff","bo_centered",
               "pistol_diff","tier_s","tier_a","tier_b","tier_c","tier_d","kd_diff"]
    v14_lan_keys = v8_keys + ["is_lan_event","team1_lan_winrate_diff"]
    v14_full_keys = v8_keys + ["is_lan_event","team1_lan_winrate_diff","region_advantage_diff"]

    auc_track: dict = {}

    def run_battery(sample_rows, label_prefix):
        if len(sample_rows) < 80:
            print(f"  [skip] {label_prefix}: only {len(sample_rows)} rows")
            return
        cut = int(len(sample_rows) * 0.7)
        y_te = np.array([r["y"] for r in sample_rows[cut:]], dtype=int)
        p_base = np.array([r["saved"] for r in sample_rows[cut:]], dtype=float)
        m_base = _metrics(y_te, p_base)
        print(f"\n--- {label_prefix} (n={len(sample_rows)}, test={len(sample_rows)-cut}) ---")
        print(f"{'set':50} {'AUC':>6} {'LogL':>7} {'Brier':>7} {'Acc':>6}")
        print("-" * 83)
        print(f"{'baseline (hltv_v1 direct)':50} {m_base['auc'] or 0:>6.3f} {m_base['logloss']:>7.4f} {m_base['brier']:>7.4f} {m_base['acc']:>6.3f}")
        persist(f"v14-lan-region_{label_prefix}_baseline", len(sample_rows), m_base, since_d,
                keys=["win_prob1"], n_train=cut)

        for keys, lbl in [
            (v8_keys, "v8 reference"),
            (v14_lan_keys, "v14: v8 + is_lan_event + lan_wr_diff"),
            (v14_full_keys, "v14: v8 + LAN + region_advantage_diff"),
            (["is_lan_event","team1_lan_winrate_diff","region_advantage_diff"], "v14 features alone"),
        ]:
            r = evaluate(sample_rows, keys, lbl)
            if r.get("skipped"):
                print(f"{lbl:50}  (skipped)")
                continue
            mm = r["metrics"]
            delta = (mm["auc"] - m_base["auc"]) if (mm["auc"] and m_base["auc"]) else 0
            marker = "*" if abs(delta) >= 0.005 else " "
            print(f"{lbl:50} {mm['auc'] or 0:>6.3f}{marker}{mm['logloss']:>6.4f} {mm['brier']:>7.4f} {mm['acc']:>6.3f}")
            persist(f"v14-lan-region_{label_prefix}_{lbl}", r["n"], mm, since_d,
                    keys=["logit_saved"] + keys, coefs=r["coefs"], n_train=r.get("n_train"))
            if label_prefix == "full":
                auc_track[lbl] = mm["auc"]

    run_battery(rows, "full")
    covered = [r for r in rows if r["hltv_covered"]]
    if len(covered) != len(rows) and len(covered) >= 80:
        run_battery(covered, "hltv-covered")

    # PROMOTE decision: v8 reference vs strongest v14 variant, full sample.
    base_auc = auc_track.get("v8 reference")
    v14a_auc = auc_track.get("v14: v8 + is_lan_event + lan_wr_diff")
    v14b_auc = auc_track.get("v14: v8 + LAN + region_advantage_diff")
    best_v14 = max(filter(lambda x: x is not None, [v14a_auc, v14b_auc]), default=None)
    cov_pct = (cov_hltv / len(rows)) if rows else 0.0

    print("\n" + "=" * 83)
    print("PROMOTE DECISION")
    print("=" * 83)
    if base_auc is None or best_v14 is None:
        print("PROMOTE: no (insufficient data — could not score both v8 and v14)")
    else:
        delta = best_v14 - base_auc
        coverage_str = f"coverage={cov_pct:.1%}"
        print(f"baseline AUC (v8):     {base_auc:.4f}")
        print(f"+v14 AUC (best):       {best_v14:.4f}")
        print(f"delta:                 {delta:+.4f}     {coverage_str}")
        if delta >= 0.002:
            print("PROMOTE: yes (delta >= +0.002 AUC, no degradation)")
        else:
            print("PROMOTE: no (delta < +0.002 AUC threshold)")


if __name__ == "__main__":
    main()
