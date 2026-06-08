"""
Per-team feature module for CS2 matches.

Given the historical match list (sorted chronologically), computes per-team:
  - form_momentum: last-5 win-rate minus last-20 win-rate (range −1..+1)
  - h2h_win_pct:   head-to-head record between team1 and team2 (last 2y)
  - days_since_last_match: rust/fatigue proxy
  - opponent_strength_avg: avg opponent ELO over last 10 matches (SoS)

All computed as point-in-time snapshots — only matches BEFORE the target date
contribute. No lookahead. The scanner can call `compute_features(history,
team1, team2, target_date, ratings_at_date)` to produce a flat dict ready to
write to cs2_predictions / cs2_upcoming_matches.

These features aren't used by the production combined_win_prob yet — they
accumulate on cs2_predictions so a future model can retrain on them.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from collections import defaultdict


H2H_LOOKBACK_DAYS = 730  # 2 years


def build_team_history_index(matches: list[dict]) -> dict[str, list[dict]]:
    """Group matches by team, each containing both perspectives.

    Returns {team_name: [{date, won, opponent, opponent_elo}, ...]} sorted by date.
    `won` is the team's perspective: True if THIS team won the match.
    """
    by_team: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        date = m["date"]
        result = m["result"]   # 1 if team1 won, 0 if team2 won

        by_team[t1].append({
            "date": date, "won": result == 1, "opponent": t2,
        })
        by_team[t2].append({
            "date": date, "won": result == 0, "opponent": t1,
        })

    for team in by_team:
        by_team[team].sort(key=lambda x: x["date"])
    return dict(by_team)


def build_h2h_index(matches: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Key is (sorted team pair). Value is matches between them, by date."""
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in matches:
        pair = tuple(sorted([m["team1"], m["team2"]]))
        out[pair].append(m)
    for pair in out:
        out[pair].sort(key=lambda x: x["date"])
    return dict(out)


def form_momentum(team_history: list[dict], target_date: datetime) -> float | None:
    """Last-5 win rate minus last-20 win rate (using only matches < target_date)."""
    prior = [h for h in team_history if h["date"] < target_date]
    if len(prior) < 5:
        return None
    last5  = prior[-5:]
    last20 = prior[-20:] if len(prior) >= 20 else prior

    wr5  = sum(1 for h in last5  if h["won"]) / len(last5)
    wr20 = sum(1 for h in last20 if h["won"]) / len(last20)
    return round(wr5 - wr20, 4)


def days_since_last_match(team_history: list[dict], target_date: datetime) -> int | None:
    """Days between target_date and the team's most recent prior match."""
    prior = [h for h in team_history if h["date"] < target_date]
    if not prior:
        return None
    delta = target_date - prior[-1]["date"]
    return max(0, delta.days)


def opponent_strength_avg(
    team_history: list[dict], target_date: datetime,
    ratings_at_date: dict[str, float],
    initial_elo: float = 1500.0,
    n: int = 10,
) -> float | None:
    """Average ELO of last N opponents, using ratings as of target_date."""
    prior = [h for h in team_history if h["date"] < target_date]
    if len(prior) < 3:
        return None
    recent = prior[-n:]
    elos = [ratings_at_date.get(h["opponent"], initial_elo) for h in recent]
    return round(sum(elos) / len(elos), 1)


def h2h_win_pct(
    h2h_index: dict[tuple[str, str], list[dict]],
    team1: str, team2: str,
    target_date: datetime,
    lookback_days: int = H2H_LOOKBACK_DAYS,
) -> tuple[float | None, int]:
    """Team1's win rate in head-to-head matches before target_date.

    Returns (win_pct, count). win_pct is None if count < 3.
    """
    pair = tuple(sorted([team1, team2]))
    cutoff = target_date - timedelta(days=lookback_days)
    prior = [m for m in h2h_index.get(pair, [])
             if cutoff <= m["date"] < target_date]
    if len(prior) < 3:
        return None, len(prior)

    # `result=1` means team1-of-the-match won. Translate to team1-of-the-query.
    wins = 0
    for m in prior:
        t1_won = m["result"] == 1
        if m["team1"] == team1 and t1_won:
            wins += 1
        elif m["team2"] == team1 and not t1_won:
            wins += 1
    return round(wins / len(prior), 4), len(prior)


def compute_features(
    history_by_team: dict[str, list[dict]],
    h2h_index: dict[tuple[str, str], list[dict]],
    ratings: dict[str, float],
    team1: str, team2: str,
    target_date: datetime,
) -> dict:
    """Flat dict of features ready to write to DB. None where uncomputable."""
    t1h = history_by_team.get(team1, [])
    t2h = history_by_team.get(team2, [])

    h2h_pct, h2h_n = h2h_win_pct(h2h_index, team1, team2, target_date)

    return {
        "form_momentum1":        form_momentum(t1h, target_date),
        "form_momentum2":        form_momentum(t2h, target_date),
        "days_since_match1":     days_since_last_match(t1h, target_date),
        "days_since_match2":     days_since_last_match(t2h, target_date),
        "opp_strength_avg1":     opponent_strength_avg(t1h, target_date, ratings),
        "opp_strength_avg2":     opponent_strength_avg(t2h, target_date, ratings),
        "h2h_team1_win_pct":     h2h_pct,
        "h2h_count":             h2h_n,
    }
