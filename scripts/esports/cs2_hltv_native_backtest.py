"""
CS2 HLTV-NATIVE backtest harness — evaluates v8-equivalent base features and
the v10/v13/v14 feature blocks using `cs2_hltv_matches.winner_name` as the
ground truth (no bo3gg `cs2_results` join).

WHY: production sneak peeks score against `cs2_predictions ⨝ cs2_results`
(bo3gg-anchored). The HLTV detail tables that power v10/v13/v14 are only
dense from Apr 2026 onward, so feature ↔ outcome overlap is 0.6-2.1% on the
bo3gg-anchored backtest. By using HLTV as the ground truth, feature coverage
IS the evaluation coverage — unblocking honest evaluation of the
post-Apr-2026 window.

Walk-forward split on `cs2_hltv_matches.match_date`:
  Train: matches strictly before `--cutoff` (default 2026-05-01)
  Eval:  matches on/after `--cutoff`

Base (v8-equivalent) features, all PIT-correct from HLTV alone:
  - form_diff:   team1 last-10 winrate − team2 last-10 winrate
  - h2h_diff:    pairwise winrate of team1 vs team2 (centered on 0.5)
  - kd_diff:     team1 avg K/D over last 10 − team2 avg K/D over last 10
  - rest_diff:   normalized days-since-last-match (team1 − team2)

ELO/rank/tier are intentionally OMITTED — those depend on external feeds
(rosters/rankings) that aren't trivially PIT-correct from HLTV alone. The
native backtest provides a SHARED BASELINE so feature deltas are honest;
it is not a v8 stand-in.

Tested feature blocks (added on top of base):
  - v10-veto-native:  permaban_match_diff + decider_winrate_diff
                       + forced_off_permaban_flag  (logic copied from
                       cs2_sneak_peek_v10_veto.py)
  - v13-side-native:  ct_start_winrate_diff + bias_aligned_diff
                       (logic copied from cs2_sneak_peek_v13_starting_side.py)
  - v14-region-native: is_lan_event + region_advantage_diff
                       (logic copied from cs2_sneak_peek_v14_lan_region.py)

Persists every battery row to `cs2_model_backtest_history` with model_name
in {hltv-native-baseline, hltv-native-v10, hltv-native-v13, hltv-native-v14}.

Run:
    python3 scripts/esports/cs2_hltv_native_backtest.py [--cutoff 2026-05-01]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
from dotenv import dotenv_values

for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
    os.environ[k] = v

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score  # noqa: E402


RUN_ID = str(uuid.uuid4())


# ── Team-name normalisation (same shape as v13/v14). HLTV names are usually
#    bare so this is mostly a no-op here, but keeps lookups symmetric and
#    tolerant of stray "Team" / "Esports" suffixes the scraper may leave in.
_SUFFIX_RE = re.compile(
    r"\s+(team|esports|esport|gaming|gamingclub|club|pro|academy)$",
    re.IGNORECASE,
)


def _norm_team(name: str | None) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    for _ in range(2):
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return re.sub(r"[^a-z0-9]", "", s)


# ── Region detection (verbatim from v14, copied here so the native backtest
#    does not import from v14 and can stand alone).
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
    """EU/NA/SA/APAC/CIS/ONLINE/UNKNOWN — copied from v14."""
    if event_name:
        for region, pat in REGION_PATTERNS:
            if pat.search(event_name):
                return region
    if (stage or "").lower() == "online":
        return "ONLINE"
    return "UNKNOWN"


# Map-bias table (verbatim from v13).
MAP_BIAS_FAVORED_SIDE: dict[str, str] = {
    "Nuke":     "ct",   # +14pp CT
    "Anubis":   "t",    # +14pp T
    "Overpass": "ct",   # +12.8pp CT
    "Inferno":  "ct",   # +5-7pp CT
}


# ── 1. Load EVERYTHING up front. Single read of each table into memory so we
#       don't scan the same row 9k times during walk-forward feature build.
def load_all_matches() -> list[dict]:
    """Pull every cs2_hltv_matches row with a non-null winner, ordered by
    match_date ascending. We need the full table to compute PIT-correct
    history for each eval-window match."""
    rows = execute_query("""
        SELECT hltv_match_id, match_date, team1_name, team2_name,
               winner_name, score1, score2, stage, event_name, best_of
        FROM cs2_hltv_matches
        WHERE match_date IS NOT NULL
          AND team1_name IS NOT NULL
          AND team2_name IS NOT NULL
        ORDER BY match_date
    """, None)
    out = []
    for r in rows:
        if not r["winner_name"]:
            continue  # need a ground-truth label
        # Drop rows where winner_name doesn't match either team — these are
        # forfeits/draws with weird strings and would silently mis-label.
        if r["winner_name"] not in (r["team1_name"], r["team2_name"]):
            continue
        out.append(r)
    return out


def load_all_player_stats() -> dict[int, dict]:
    """Return {hltv_match_id: {team_name: {kills, deaths, n_players}}} so
    later we can compute per-team K/D for any match in O(1)."""
    rows = execute_query("""
        SELECT hltv_match_id, team_name, kills, deaths
        FROM cs2_hltv_player_match_stats
        WHERE team_name IS NOT NULL AND kills IS NOT NULL AND deaths IS NOT NULL
    """, None)
    per_match: dict[int, dict] = defaultdict(lambda: defaultdict(
        lambda: {"kills": 0, "deaths": 0, "n": 0}
    ))
    for r in rows:
        bucket = per_match[r["hltv_match_id"]][r["team_name"]]
        bucket["kills"] += r["kills"] or 0
        bucket["deaths"] += r["deaths"] or 0
        bucket["n"] += 1
    return per_match


def load_all_veto() -> dict[int, list[dict]]:
    """{hltv_match_id: [steps sorted by step]}."""
    rows = execute_query("""
        SELECT hltv_match_id, step, team_name, action, map_name
        FROM cs2_hltv_match_veto
        ORDER BY hltv_match_id, step
    """, None)
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["hltv_match_id"]].append(r)
    return out


def load_all_map_sides() -> dict[int, list[dict]]:
    """{hltv_match_id: [{map_name, team1_first_half_side, winner_name}]}."""
    rows = execute_query("""
        SELECT hltv_match_id, map_name, team1_first_half_side, winner_name,
               team1_score, team2_score
        FROM cs2_hltv_match_maps
        WHERE map_name IS NOT NULL
    """, None)
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["hltv_match_id"]].append(r)
    return out


# ── 2. Build per-team time-sorted streams used by PIT-correct features.
def build_team_streams(all_matches: list[dict],
                       player_stats: dict[int, dict],
                       map_sides: dict[int, list[dict]]) -> dict:
    """For each team name, build a chronologically-sorted list of dicts:
       {match_date, hltv_match_id, won, kd, opponent, map_sides_for_this_match,
        region, is_lan}.
    map_sides_for_this_match is the raw list (each with team1_first_half_side
    relative to that match's hltv team1) — used by CT-start-winrate.
    """
    streams: dict[str, list[dict]] = defaultdict(list)
    for m in all_matches:
        t1, t2 = m["team1_name"], m["team2_name"]
        winner = m["winner_name"]
        md = m["match_date"]
        hid = m["hltv_match_id"]
        ps = player_stats.get(hid, {})
        # Per-team K/D for this match
        kd_by_team = {}
        for tname in (t1, t2):
            tps = ps.get(tname)
            if tps and tps["deaths"] > 0 and tps["n"] >= 3:
                kd_by_team[tname] = tps["kills"] / tps["deaths"]
            else:
                kd_by_team[tname] = None
        region = detect_event_region(m.get("event_name"), m.get("stage"))
        is_lan = (m.get("stage") or "").strip().lower() == "lan"
        sides = map_sides.get(hid, [])

        for me, opp in ((t1, t2), (t2, t1)):
            # `team1_first_half_side` is recorded relative to the HLTV team1.
            # When "me" is HLTV team2, flip the side label so each stream
            # entry is "the side ME started on".
            my_sides = []
            for mp in sides:
                side_t1 = (mp.get("team1_first_half_side") or "").lower()
                if side_t1 not in ("ct", "t"):
                    continue
                if me == t1:
                    my_side = side_t1
                else:
                    my_side = "t" if side_t1 == "ct" else "ct"
                # Did "me" win this specific map?
                won_map = (mp.get("winner_name") == me)
                my_sides.append({
                    "map_name": mp["map_name"],
                    "started_ct": my_side == "ct",
                    "won_map": won_map,
                })
            streams[me].append({
                "match_date": md,
                "hltv_match_id": hid,
                "won": (winner == me),
                "kd": kd_by_team.get(me),
                "opponent": opp,
                "my_map_sides": my_sides,
                "region": region,
                "is_lan": is_lan,
            })

    for k in streams:
        streams[k].sort(key=lambda x: x["match_date"])
    return dict(streams)


def _bisect_strict_lt(stream: list[dict], kickoff) -> int:
    """Return the largest hi such that stream[:hi] only contains rows
    with match_date < kickoff. Streams are pre-sorted by match_date."""
    # Build a lightweight key list (one-time per call) — cheap because the
    # bottleneck is the linear feature aggregation that follows.
    dates = [s["match_date"] for s in stream]
    return bisect.bisect_left(dates, kickoff)


# ── 3. PIT-correct base features (form / h2h / kd / rest).
def base_features_for(team: str, kickoff,
                       streams: dict, window: int = 10) -> dict:
    """Return last-N form winrate, last-N avg KD, and days_since_last_match.
    Falls back to neutral values when the team has no prior matches."""
    stream = streams.get(team)
    if not stream:
        return {"form": 0.5, "form_n": 0, "kd": None, "days_since": 30.0}
    hi = _bisect_strict_lt(stream, kickoff)
    if hi == 0:
        return {"form": 0.5, "form_n": 0, "kd": None, "days_since": 30.0}
    last = stream[max(0, hi - window):hi]
    wins = sum(1 for s in last if s["won"])
    form_n = len(last)
    form = wins / form_n if form_n else 0.5
    kds = [s["kd"] for s in last if s["kd"] is not None]
    kd = (sum(kds) / len(kds)) if kds else None
    last_date = stream[hi - 1]["match_date"]
    days_since = (kickoff - last_date).total_seconds() / 86400.0
    return {"form": form, "form_n": form_n, "kd": kd,
            "days_since": min(days_since, 30.0)}


def h2h_winrate(team1: str, team2: str, kickoff, streams: dict) -> tuple[float, int]:
    """Returns (team1_wr, n_prior_h2h) over PIT-correct head-to-head matches."""
    stream = streams.get(team1)
    if not stream:
        return 0.5, 0
    hi = _bisect_strict_lt(stream, kickoff)
    if hi == 0:
        return 0.5, 0
    wins, n = 0, 0
    for s in stream[:hi]:
        if s["opponent"] != team2:
            continue
        n += 1
        if s["won"]:
            wins += 1
    if n == 0:
        return 0.5, 0
    return wins / n, n


# ── 4. v10-veto-native — permaban + decider + forced-off.
def per_team_permaban_freq(all_veto_by_match: dict[int, list[dict]]) -> dict:
    """{team_name: Counter(map_name)} — aggregate count of each team's
    permaban (action='removed', step 1 or 2). Aggregate is acceptable for the
    sneak-peek-style heuristic; matches v10's behaviour."""
    freq: dict[str, Counter] = defaultdict(Counter)
    for steps in all_veto_by_match.values():
        for v in steps:
            if v["action"] == "removed" and v["step"] in (1, 2):
                freq[v["team_name"]][v["map_name"]] += 1
    return freq


def per_team_map_winrate(map_sides_by_match: dict[int, list[dict]],
                          matches_by_id: dict[int, dict]) -> dict:
    """{(team_name, map_name): {wins, losses}} all-time. Same approximation
    as v10 (aggregate, not PIT)."""
    out: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
    for hid, sides in map_sides_by_match.items():
        m = matches_by_id.get(hid)
        if not m:
            continue
        t1, t2 = m["team1_name"], m["team2_name"]
        for mp in sides:
            if not mp.get("map_name") or not mp.get("winner_name"):
                continue
            for tname in (t1, t2):
                if not tname:
                    continue
                key = (tname, mp["map_name"])
                if mp["winner_name"] == tname:
                    out[key]["wins"] += 1
                else:
                    out[key]["losses"] += 1
    return out


def v10_veto_features(hid: int, team1: str, team2: str,
                       veto_by_match: dict, permaban_freq: dict,
                       map_winrates: dict) -> dict:
    """Derive permaban_match_diff, decider_winrate_diff, forced_off_permaban_flag
    for one match. Logic copied from cs2_sneak_peek_v10_veto.py:
    derive_match_veto_features."""
    match_veto = veto_by_match.get(hid, [])
    if not match_veto:
        return {"permaban_match_diff": 0.0, "decider_winrate_diff": 0.0,
                "forced_off_permaban_flag": 0.0, "v10_covered": 0}

    picks_in_order = [v["map_name"] for v in match_veto if v["action"] == "picked"]
    decider = picks_in_order[-1] if picks_in_order else None

    decider_diff = 0.0
    if decider:
        t1_stats = map_winrates.get((team1, decider))
        t2_stats = map_winrates.get((team2, decider))
        if t1_stats and t2_stats:
            t1_n = t1_stats["wins"] + t1_stats["losses"]
            t2_n = t2_stats["wins"] + t2_stats["losses"]
            if t1_n >= 3 and t2_n >= 3:
                decider_diff = (t1_stats["wins"] / t1_n) - (t2_stats["wins"] / t2_n)

    t1_top = [m for m, _ in permaban_freq.get(team1, Counter()).most_common(3)]
    t2_top = [m for m, _ in permaban_freq.get(team2, Counter()).most_common(3)]
    t1_bans = [v["map_name"] for v in match_veto
               if v["action"] == "removed" and v["team_name"] == team1]
    t2_bans = [v["map_name"] for v in match_veto
               if v["action"] == "removed" and v["team_name"] == team2]

    forced_off = 0.0
    if t1_bans and t1_top:
        if not any(m in t1_top for m in t1_bans[:2]):
            forced_off -= 1.0
    if t2_bans and t2_top:
        if not any(m in t2_top for m in t2_bans[:2]):
            forced_off += 1.0

    t1_stole = sum(1 for m in t1_bans if m in t2_top)
    t2_stole = sum(1 for m in t2_bans if m in t1_top)
    permaban_diff = float(t1_stole - t2_stole) / 3.0

    return {
        "permaban_match_diff": permaban_diff,
        "decider_winrate_diff": decider_diff,
        "forced_off_permaban_flag": forced_off,
        "v10_covered": 1 if (decider_diff != 0.0 or permaban_diff != 0.0
                             or forced_off != 0.0) else 0,
    }


# ── 5. v13-side-native — CT-start winrate + bias_aligned_diff.
def ct_start_winrate(team: str, kickoff, streams: dict,
                      min_per_map: int = 3) -> tuple[float | None, int]:
    """Per-map CT-start winrate averaged across maps with ≥min_per_map priors,
    falling back to pooled rate. Copied from v13.compute_ct_start_winrate."""
    stream = streams.get(team)
    if not stream:
        return None, 0
    hi = _bisect_strict_lt(stream, kickoff)
    if hi == 0:
        return None, 0
    per_map: dict[str, list[int]] = defaultdict(list)
    for s in stream[:hi]:
        for ms in s["my_map_sides"]:
            if ms["started_ct"]:
                per_map[ms["map_name"]].append(1 if ms["won_map"] else 0)
    if not per_map:
        return None, 0
    rates = []
    for results in per_map.values():
        if len(results) >= min_per_map:
            rates.append(sum(results) / len(results))
    if not rates:
        # Pool fallback
        pooled = [w for results in per_map.values() for w in results]
        if len(pooled) < 3:
            return None, len(pooled)
        return sum(pooled) / len(pooled), len(pooled)
    return sum(rates) / len(rates), sum(len(v) for v in per_map.values())


def bias_aligned_diff(hid: int, map_sides_by_match: dict) -> tuple[float, int]:
    """+1 if team1 started on its map's favored side and team2 didn't, −1 if
    reverse, 0 otherwise. Averaged across the match's biased maps
    (Nuke/Anubis/Overpass/Inferno). Logic copied from
    cs2_sneak_peek_v13_starting_side.py: compute_bias_aligned_diff.

    Note: native backtest's "team1" is always HLTV team1, so no orient flip.
    """
    sides = map_sides_by_match.get(hid, [])
    if not sides:
        return 0.0, 0
    scores = []
    for mp in sides:
        favored = MAP_BIAS_FAVORED_SIDE.get(mp.get("map_name"))
        if favored is None:
            continue
        side_t1 = (mp.get("team1_first_half_side") or "").lower()
        if side_t1 not in ("ct", "t"):
            continue
        if side_t1 == favored:
            scores.append(1.0)
        else:
            scores.append(-1.0)
    if not scores:
        return 0.0, 0
    return sum(scores) / len(scores), len(scores)


# ── 6. v14-region-native — is_lan_event + region_advantage_diff.
def team_home_region(team: str, kickoff, streams: dict,
                      min_n: int = 5) -> str | None:
    """PIT-correct mode of non-ONLINE/UNKNOWN regions in the team's history.
    Copied from v14.compute_team_home_region."""
    stream = streams.get(team)
    if not stream:
        return None
    hi = _bisect_strict_lt(stream, kickoff)
    if hi == 0:
        return None
    counts: Counter = Counter()
    for s in stream[:hi]:
        r = s["region"]
        if r in (None, "UNKNOWN", "ONLINE"):
            continue
        counts[r] += 1
    if not counts or sum(counts.values()) < min_n:
        return None
    return counts.most_common(1)[0][0]


# ── 7. Build the eval row for one match.
def build_row(m: dict, streams: dict,
               veto_by_match: dict, permaban_freq: dict,
               map_winrates: dict, map_sides_by_match: dict) -> dict | None:
    t1, t2, kickoff = m["team1_name"], m["team2_name"], m["match_date"]
    y = 1 if m["winner_name"] == t1 else 0

    b1 = base_features_for(t1, kickoff, streams)
    b2 = base_features_for(t2, kickoff, streams)
    form_diff = b1["form"] - b2["form"]
    rest_diff = (b1["days_since"] - b2["days_since"]) / 30.0
    kd_diff = ((b1["kd"] - b2["kd"])
                if (b1["kd"] is not None and b2["kd"] is not None) else 0.0)
    h2h_t1, h2h_n = h2h_winrate(t1, t2, kickoff, streams)
    h2h_diff = (h2h_t1 - 0.5) if h2h_n >= 2 else 0.0

    base_covered = 1 if (b1["form_n"] >= 3 and b2["form_n"] >= 3) else 0

    # v10
    hid = m["hltv_match_id"]
    v10 = v10_veto_features(hid, t1, t2, veto_by_match,
                              permaban_freq, map_winrates)

    # v13
    wr1, _n1 = ct_start_winrate(t1, kickoff, streams)
    wr2, _n2 = ct_start_winrate(t2, kickoff, streams)
    ct_start_winrate_diff = (
        float(wr1 - wr2) if (wr1 is not None and wr2 is not None) else 0.0
    )
    v13_ct_covered = 1 if (wr1 is not None and wr2 is not None) else 0
    bias_diff, n_biased = bias_aligned_diff(hid, map_sides_by_match)
    v13_bias_covered = 1 if n_biased > 0 else 0
    v13_covered = 1 if (v13_ct_covered or v13_bias_covered) else 0

    # v14
    region = detect_event_region(m.get("event_name"), m.get("stage"))
    is_lan_event = 1.0 if (m.get("stage") or "").strip().lower() == "lan" else 0.0
    home1 = team_home_region(t1, kickoff, streams)
    home2 = team_home_region(t2, kickoff, streams)
    rmt1 = 1 if (home1 and region not in ("ONLINE", "UNKNOWN")
                  and home1 == region) else 0
    rmt2 = 1 if (home2 and region not in ("ONLINE", "UNKNOWN")
                  and home2 == region) else 0
    region_advantage_diff = float(rmt1 - rmt2)
    v14_covered = 1 if (
        home1 is not None and home2 is not None
        and region not in ("ONLINE", "UNKNOWN")
    ) else 0

    return {
        "match_date": kickoff,
        "y": y,
        # base
        "form_diff": form_diff,
        "h2h_diff": h2h_diff,
        "kd_diff": kd_diff,
        "rest_diff": rest_diff,
        "base_covered": base_covered,
        # v10
        "permaban_match_diff": v10["permaban_match_diff"],
        "decider_winrate_diff": v10["decider_winrate_diff"],
        "forced_off_permaban_flag": v10["forced_off_permaban_flag"],
        "v10_covered": v10["v10_covered"],
        # v13
        "ct_start_winrate_diff": ct_start_winrate_diff,
        "bias_aligned_diff": bias_diff,
        "v13_covered": v13_covered,
        # v14
        "is_lan_event": is_lan_event,
        "region_advantage_diff": region_advantage_diff,
        "v14_covered": v14_covered,
    }


# ── 8. Evaluation.
def _metrics(y, p):
    return {
        "auc":     float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
        "logloss": float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))),
        "brier":   float(brier_score_loss(y, p)),
        "acc":     float(((p >= 0.5).astype(int) == y).mean()),
    }


def fit_and_score(train_rows: list[dict], eval_rows: list[dict],
                   keys: list[str]) -> dict:
    X_tr = np.array([[r[k] for k in keys] for r in train_rows], dtype=float)
    y_tr = np.array([r["y"] for r in train_rows], dtype=int)
    X_te = np.array([[r[k] for k in keys] for r in eval_rows], dtype=float)
    y_te = np.array([r["y"] for r in eval_rows], dtype=int)
    if len(set(y_tr)) < 2:
        return {"skipped": True, "reason": "train labels constant"}
    model = LogisticRegression(max_iter=2000)
    model.fit(X_tr, y_tr)
    p = model.predict_proba(X_te)[:, 1]
    return {
        "metrics": _metrics(y_te, p),
        "coefs": dict(zip(keys, model.coef_[0].tolist())),
    }


def persist(name: str, n: int, m: dict, since_d: date,
            keys: list[str] | None = None, coefs: dict | None = None,
            n_train: int | None = None) -> None:
    """Write one row to cs2_model_backtest_history. Best-effort — failures
    print a warning but never block the run."""
    try:
        execute_write(
            """INSERT INTO cs2_model_backtest_history
                (run_id, feature_set, n_matches, n_train, n_test,
                 auc, logloss, brier, accuracy, since_date, feature_keys, coefs)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (RUN_ID, name, n, n_train, (n - (n_train or 0)) or None,
             m.get("auc"), m["logloss"], m["brier"], m["acc"], since_d,
             keys, json.dumps(coefs) if coefs else None),
        )
    except Exception as e:
        print(f"  [warn] persist {name} failed: {e}")


# ── 9. Main driver.
BASE_KEYS = ["form_diff", "h2h_diff", "kd_diff", "rest_diff"]
V10_EXTRA = ["permaban_match_diff", "decider_winrate_diff",
              "forced_off_permaban_flag"]
V13_EXTRA = ["ct_start_winrate_diff", "bias_aligned_diff"]
V14_EXTRA = ["is_lan_event", "region_advantage_diff"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-05-01",
                    help="Train: matches < cutoff. Eval: matches >= cutoff.")
    args = ap.parse_args()
    cutoff_d = date.fromisoformat(args.cutoff)
    # Convert to tz-aware UTC datetime so comparisons against match_date
    # (TIMESTAMPTZ) work — naive datetime would TypeError in the bisect.
    from datetime import timezone
    cutoff_ts = datetime.combine(cutoff_d, datetime.min.time(),
                                  tzinfo=timezone.utc)

    print("loading HLTV match table…")
    matches = load_all_matches()
    print(f"  {len(matches)} usable matches "
          f"(winner_name matches team1 or team2)")

    matches_by_id = {m["hltv_match_id"]: m for m in matches}

    print("loading player match stats…")
    player_stats = load_all_player_stats()
    print(f"  player-stat rows aggregated across "
          f"{len(player_stats)} matches")

    print("loading veto + map sides…")
    veto_by_match = load_all_veto()
    map_sides_by_match = load_all_map_sides()
    print(f"  {len(veto_by_match)} matches with veto, "
          f"{len(map_sides_by_match)} matches with map sides")

    print("building per-team streams…")
    streams = build_team_streams(matches, player_stats, map_sides_by_match)
    print(f"  {len(streams)} teams streamed")

    print("aggregating veto-derived lookups (permaban freq, map winrates)…")
    permaban_freq = per_team_permaban_freq(veto_by_match)
    map_winrates = per_team_map_winrate(map_sides_by_match, matches_by_id)

    print(f"computing PIT-correct row features (cutoff {cutoff_d})…")
    rows: list[dict] = []
    for m in matches:
        row = build_row(m, streams, veto_by_match, permaban_freq,
                         map_winrates, map_sides_by_match)
        if row is not None:
            rows.append(row)

    # Split by cutoff
    train_rows = [r for r in rows if r["match_date"] < cutoff_ts]
    eval_rows  = [r for r in rows if r["match_date"] >= cutoff_ts]
    print(f"\n  n_train = {len(train_rows)}   n_eval = {len(eval_rows)}")
    if len(train_rows) < 50 or len(eval_rows) < 50:
        print("  [abort] insufficient rows for a meaningful train/eval split")
        return

    # Coverage on eval window
    n_eval = len(eval_rows)
    def cov(key):
        c = sum(1 for r in eval_rows if r[key])
        return c, (c / n_eval if n_eval else 0.0)
    base_c, base_p = cov("base_covered")
    v10_c, v10_p   = cov("v10_covered")
    v13_c, v13_p   = cov("v13_covered")
    v14_c, v14_p   = cov("v14_covered")
    print(f"  base_covered:  {base_c}/{n_eval} ({base_p:.1%})")
    print(f"  v10_covered:   {v10_c}/{n_eval} ({v10_p:.1%})")
    print(f"  v13_covered:   {v13_c}/{n_eval} ({v13_p:.1%})")
    print(f"  v14_covered:   {v14_c}/{n_eval} ({v14_p:.1%})\n")

    # Baseline
    print("=" * 78)
    print("BASELINE (base features only)")
    print("=" * 78)
    base = fit_and_score(train_rows, eval_rows, BASE_KEYS)
    if base.get("skipped"):
        print(f"  [skip] {base['reason']}")
        return
    bm = base["metrics"]
    print(f"  {'baseline (form+h2h+kd+rest)':45} "
          f"AUC={bm['auc'] or 0:.4f}  LogL={bm['logloss']:.4f}  "
          f"Brier={bm['brier']:.4f}  Acc={bm['acc']:.3f}")
    persist("hltv-native-baseline", n_eval, bm, cutoff_d,
            keys=BASE_KEYS, coefs=base["coefs"], n_train=len(train_rows))
    base_auc = bm["auc"]

    # Per-block: fit base + block on the train set, score on eval.
    blocks = [
        ("hltv-native-v10", "v10-veto-native",  V10_EXTRA, "v10_covered"),
        ("hltv-native-v13", "v13-side-native",  V13_EXTRA, "v13_covered"),
        ("hltv-native-v14", "v14-region-native",V14_EXTRA, "v14_covered"),
    ]
    summary: list[tuple] = []
    print()
    print("=" * 78)
    print("FEATURE-BLOCK BATTERY")
    print("=" * 78)
    print(f"{'block':22} {'AUC':>7} {'delta':>8} {'LogL':>7} {'Brier':>7} {'Acc':>6} {'cov':>7}")
    print("-" * 78)
    print(f"{'baseline':22} {base_auc or 0:>7.4f} {'':>8} "
          f"{bm['logloss']:>7.4f} {bm['brier']:>7.4f} {bm['acc']:>6.3f} "
          f"{base_p:>7.1%}")
    for model_name, label, extra_keys, cov_key in blocks:
        all_keys = BASE_KEYS + extra_keys
        res = fit_and_score(train_rows, eval_rows, all_keys)
        if res.get("skipped"):
            print(f"{label:22}  (skipped: {res['reason']})")
            summary.append((label, None, None, 0.0))
            continue
        rm = res["metrics"]
        delta = (rm["auc"] - base_auc) if (rm["auc"] and base_auc) else 0.0
        c_n = sum(1 for r in eval_rows if r[cov_key])
        c_pct = c_n / n_eval if n_eval else 0.0
        marker = "*" if abs(delta) >= 0.005 else " "
        print(f"{label:22} {rm['auc'] or 0:>7.4f}{marker}{delta:>+7.4f} "
              f"{rm['logloss']:>7.4f} {rm['brier']:>7.4f} {rm['acc']:>6.3f} "
              f"{c_pct:>7.1%}")
        persist(model_name, n_eval, rm, cutoff_d,
                keys=all_keys, coefs=res["coefs"], n_train=len(train_rows))
        summary.append((label, rm["auc"], delta, c_pct))

    # PROMOTE decisions
    print()
    print("=" * 78)
    print("PROMOTE RECOMMENDATIONS  (rule: delta >= +0.002 AUC vs baseline)")
    print("=" * 78)
    for label, auc, delta, c_pct in summary:
        if auc is None or delta is None:
            verdict = "no (skipped — insufficient data)"
        elif delta >= 0.002:
            verdict = f"yes (delta {delta:+.4f}, coverage {c_pct:.1%})"
        else:
            verdict = f"no (delta {delta:+.4f}, coverage {c_pct:.1%})"
        print(f"  {label:22} → {verdict}")


if __name__ == "__main__":
    main()
