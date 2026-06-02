"""
OddsIntel — National-Team Predictor v1 (WC-PHASE-3)

Separate prediction model for international football. The club model in
`train.py` / `features.py` assumes league/season context that doesn't exist
for nationals (teams meet rarely, no "season form", neutral tournament
venues), so this is a parallel implementation.

Inputs (no opponent-specific learned weights — v1):
  - Current ELO ratings from `team_elo_international` (or in-memory during
    backtest)
  - Recent goal-scoring + goal-conceding rates per team

Outputs to the standard `predictions` table:
  - `model_probability` per market (`1x2_home`, `1x2_draw`, `1x2_away`,
    `over_2_5`, `under_2_5`, `btts_yes`, `btts_no`)
  - `source='national_team'`, `model_version='national_team_v1'`

1X2 from ELO:
  rating_diff = home_elo - away_elo + home_advantage
  draw_prob = max(0.16, 0.28 - 0.0002 * |rating_diff|)   # closer teams → more draws
  home_win|no-draw = 1 / (1 + 10^(-rating_diff/400))
  P(home) = (1 - draw_prob) * home_win|no-draw
  P(away) = (1 - draw_prob) * (1 - home_win|no-draw)

Goals model (Dixon-Coles light, no rho):
  For each team, mean goals scored / conceded in last N=20 internationals,
  weighted by competition: tournament=1.0, qualifier_nl=0.8, friendly=0.3
  (friendlies are noisier — rotational squads, lower intensity).
  λ_home = home.goals_for_w * away.goals_against_w / overall_avg_goals
  λ_away = away.goals_for_w * home.goals_against_w / overall_avg_goals
  Then use Poisson product for O/U 2.5 and BTTS.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import date

# Competition weighting — affects goals-model only. ELO K-factor is handled
# in compute_international_elo.py (training time).
COMP_WEIGHT = {
    "tournament":   1.0,
    "qualifier_nl": 0.8,
    "friendly":     0.3,
}

# Home advantage in ELO points — only applied to qualifier_nl matches
# (tournaments are mostly neutral; friendlies are de facto neutral).
HOME_ADV_BY_CAT = {"tournament": 0, "qualifier_nl": 60, "friendly": 0}

# Overall mean goals/team/match in international football (anchored from
# data — tuned at 1.25 which matches our 6651-match corpus' avg goals/team)
OVERALL_AVG_GOALS = 1.25


@dataclass
class TeamGoalStats:
    """Recent goal stats for one national team — weighted."""
    goals_for: float = 0.0       # weighted mean per match
    goals_against: float = 0.0   # weighted mean per match
    n_matches: int = 0           # raw sample size


def _draw_prob(rating_diff: float, base: float = 0.28) -> float:
    """Heuristic draw inflation — high when teams are close in ELO."""
    return max(0.16, base - 0.0002 * abs(rating_diff))


def predict_1x2_from_elo(
    home_elo: float,
    away_elo: float,
    comp_category: str,
    softening_factor: float = 1.0,
    draw_base: float = 0.28,
) -> dict[str, float]:
    """
    Return {'home': p, 'draw': p, 'away': p} summing to 1.0.

    softening_factor: divides rating_diff before logistic — shrinks toward
    even odds. 1.0 = standard ELO. >1 = soften favourites. The 2026-06-02
    backtest showed standard ELO is overconfident in the 60-80% bucket
    (predicted 64% / actual 42%), so a value of ~1.3 is the sane default.
    draw_base: prior weight on draw outcome (heuristic; 0.28 = standard).
    """
    h_adv = HOME_ADV_BY_CAT.get(comp_category, 0)
    rating_diff = ((home_elo + h_adv) - away_elo) / max(softening_factor, 1e-6)
    draw_p = _draw_prob(rating_diff, base=draw_base)
    home_no_draw = 1.0 / (1 + 10 ** (-rating_diff / 400))
    home_p = (1 - draw_p) * home_no_draw
    away_p = (1 - draw_p) * (1 - home_no_draw)
    return {"home": home_p, "draw": draw_p, "away": away_p}


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _matrix(lam_h: float, lam_a: float, max_goals: int = 8):
    """Joint pmf grid P(home_goals=i, away_goals=j) assuming independence."""
    ph = [_poisson_pmf(i, lam_h) for i in range(max_goals + 1)]
    pa = [_poisson_pmf(j, lam_a) for j in range(max_goals + 1)]
    return ph, pa


def predict_goals(
    home_stats: TeamGoalStats,
    away_stats: TeamGoalStats,
    home_elo: float = 1500.0,
    away_elo: float = 1500.0,
    avg_goals_per_team: float = OVERALL_AVG_GOALS,
    elo_goal_factor: float = 0.0,
    smoothing: float = 0.0,
) -> dict[str, float]:
    """
    Returns over/under 2.5 + BTTS probabilities + λ values.

    elo_goal_factor: if >0, scales λ by ELO differential. 0.0008 means a
    +200 ELO favourite gets +16% goal expectation, opponent -16%.
    Recommended starting value: 0.0006-0.001.

    smoothing: shrinks toward 0.5 (regression to mean). 0.3 means
    final_p = 0.7*p + 0.3*0.5. Use when underlying λ estimates are noisy.
    """
    avg = avg_goals_per_team
    # Fall back to overall mean when team stats are sparse
    h_for = home_stats.goals_for if home_stats.n_matches >= 5 else avg
    h_ag  = home_stats.goals_against if home_stats.n_matches >= 5 else avg
    a_for = away_stats.goals_for if away_stats.n_matches >= 5 else avg
    a_ag  = away_stats.goals_against if away_stats.n_matches >= 5 else avg

    lam_h = max(0.15, (h_for * a_ag) / avg)
    lam_a = max(0.15, (a_for * h_ag) / avg)

    # ELO-aware adjustment: favourite scores more, underdog less
    if elo_goal_factor > 0:
        rd = home_elo - away_elo
        lam_h = max(0.15, lam_h * (1 + elo_goal_factor * rd))
        lam_a = max(0.15, lam_a * (1 - elo_goal_factor * rd))

    ph, pa = _matrix(lam_h, lam_a)
    # Over 2.5: total goals >= 3
    p_total = {}
    for i in range(len(ph)):
        for j in range(len(pa)):
            p_total.setdefault(i + j, 0.0)
            p_total[i + j] += ph[i] * pa[j]
    p_under_25 = sum(p for tot, p in p_total.items() if tot <= 2)
    p_over_25 = max(0.0, 1.0 - p_under_25)

    # BTTS yes = P(h>=1 AND a>=1) under independence
    p_h_zero = ph[0]
    p_a_zero = pa[0]
    p_btts_no = p_h_zero + p_a_zero - (p_h_zero * p_a_zero)
    p_btts_yes = max(0.0, 1.0 - p_btts_no)

    # Smoothing toward 0.5
    if smoothing > 0:
        p_over_25 = (1 - smoothing) * p_over_25 + smoothing * 0.5
        p_under_25 = 1.0 - p_over_25
        p_btts_yes = (1 - smoothing) * p_btts_yes + smoothing * 0.5
        p_btts_no = 1.0 - p_btts_yes

    return {
        "over_2_5":  p_over_25,
        "under_2_5": p_under_25,
        "btts_yes":  p_btts_yes,
        "btts_no":   p_btts_no,
        "lam_h":     lam_h,
        "lam_a":     lam_a,
    }


def predict_match(
    home_elo: float,
    away_elo: float,
    home_stats: TeamGoalStats,
    away_stats: TeamGoalStats,
    comp_category: str = "tournament",
    softening_factor: float = 1.0,
    draw_base: float = 0.28,
    avg_goals_per_team: float = OVERALL_AVG_GOALS,
    elo_goal_factor: float = 0.0,
    goals_smoothing: float = 0.0,
) -> dict:
    """Full prediction dict — combines 1X2 (ELO) + goals (Poisson)."""
    p1x2 = predict_1x2_from_elo(home_elo, away_elo, comp_category,
                                softening_factor=softening_factor,
                                draw_base=draw_base)
    pgoals = predict_goals(home_stats, away_stats,
                            home_elo=home_elo, away_elo=away_elo,
                            avg_goals_per_team=avg_goals_per_team,
                            elo_goal_factor=elo_goal_factor,
                            smoothing=goals_smoothing)
    return {
        "1x2_home":  p1x2["home"],
        "1x2_draw":  p1x2["draw"],
        "1x2_away":  p1x2["away"],
        "over_2_5":  pgoals["over_2_5"],
        "under_2_5": pgoals["under_2_5"],
        "btts_yes":  pgoals["btts_yes"],
        "btts_no":   pgoals["btts_no"],
        "lam_h":     pgoals["lam_h"],
        "lam_a":     pgoals["lam_a"],
    }
