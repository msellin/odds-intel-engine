"""
OddsIntel — Model Improvements (P1-P4)
Implements the prioritized changes from MODEL_ANALYSIS.md, revised per
4-assessment synthesis (2026-04-27).

Architecture decisions:
  - P1 (calibration): ACTIVE — tier-specific alpha + Platt sigmoid post-hoc
  - P2 (odds movement): ACTIVE — soft penalty on Kelly, hard veto only >10%
  - P3 (alignment): LOG-ONLY — stores scores, does NOT filter/modify stakes yet
  - P4 (Kelly sizing): ACTIVE — 1/4 Kelly, 1.5% cap, simplified multipliers

Key revision: Alignment uses EXTERNAL signals only (odds movement, news,
lineup, situational). Strength/form/xG are already model inputs — including
them in alignment double-counts what the Poisson model already knows.

Platt scaling (2026-04-30): After tier-specific shrinkage, applies a learned
sigmoid correction fitted on settled predictions. Parameters loaded from
model_calibration table, refreshed weekly by scripts/fit_platt.py.

DB access (2026-05-03): All DB queries use direct psycopg2 via execute_query()
— no PostgREST/supabase SDK. Consistent with the rest of the pipeline.
"""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query


# =============================================================================
# P1: CALIBRATION — Tier-specific shrinkage toward market price
# =============================================================================

# Per-tier alpha: weight on model probability.
# T1-2: market is well-calibrated → trust market more (lower alpha)
# T3-4: market is less efficient → trust model more (higher alpha)
CALIBRATION_ALPHA = {
    1: 0.20,
    2: 0.30,
    3: 0.50,
    4: 0.65,
}
CALIBRATION_ALPHA_DEFAULT = 0.35


def calibrate_prob(model_prob: float, implied_prob: float,
                   tier: int = 1, market: str = "") -> float:
    """
    Two-stage calibration:
      1. Tier-specific shrinkage toward market-implied probability
      2. Platt sigmoid correction (if parameters available for this market)

    Stage 1 (shrinkage):
      adjusted = alpha * model_prob + (1 - alpha) * implied_prob
      Uses tier-specific alpha (T1: 0.20 → T4: 0.65).

    Stage 2 (Platt):
      calibrated = 1 / (1 + exp(-(a * adjusted + b)))
      Parameters a, b loaded from model_calibration table.
      Skipped if no params exist for this market (graceful no-op).

    Args:
        model_prob: Raw model probability (Poisson/XGBoost/ensemble)
        implied_prob: 1/odds (bookmaker-implied probability before margin)
        tier: League tier (1-4)
        market: Market key for Platt lookup (e.g. '1x2_home')

    Returns:
        Calibrated probability
    """
    if implied_prob <= 0 or implied_prob >= 1:
        return model_prob
    alpha = CALIBRATION_ALPHA.get(tier, CALIBRATION_ALPHA_DEFAULT)
    shrunk = alpha * model_prob + (1 - alpha) * implied_prob

    # Stage 2: Platt sigmoid (if available for this market)
    return apply_platt(shrunk, market)


# =============================================================================
# P1b: PLATT SCALING — Learned sigmoid post-hoc calibration
# =============================================================================

# Cache: loaded once per pipeline run, refreshed weekly by fit_platt.py
_platt_params: dict[str, tuple[float, float]] | None = None


def load_platt_params() -> dict[str, tuple[float, float]]:
    """
    Load latest Platt α, β per market from model_calibration table.
    Returns dict: market → (a, b). Empty dict if table doesn't exist or is empty.
    Cached for the lifetime of the process.
    """
    global _platt_params
    if _platt_params is not None:
        return _platt_params

    _platt_params = {}
    try:
        rows = execute_query(
            "SELECT market, platt_a, platt_b FROM model_calibration ORDER BY fitted_at DESC LIMIT 20",
            [],
        )
        seen: set = set()
        for row in rows:
            mkt = row["market"]
            if mkt not in seen:
                _platt_params[mkt] = (float(row["platt_a"]), float(row["platt_b"]))
                seen.add(mkt)
    except Exception:
        pass  # Table may not exist yet — graceful no-op

    return _platt_params


def apply_platt(prob: float, market: str) -> float:
    """
    Apply Platt sigmoid if parameters exist for this market.

        calibrated = 1 / (1 + exp(-(a * prob + b)))

    Falls back to returning prob unchanged if no params available.
    """
    if not market:
        return prob

    params = load_platt_params()
    if market not in params:
        return prob

    a, b = params[market]
    z = a * prob + b
    # Clamp to prevent overflow
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def reset_platt_cache():
    """Force reload of Platt params on next call. Used by tests."""
    global _platt_params
    _platt_params = None


# =============================================================================
# P2: ODDS MOVEMENT — Drift, velocity, soft penalty
# =============================================================================

def compute_odds_movement(match_id: str, market: str, selection: str,
                          current_odds: float) -> dict:
    """
    Compute odds drift and velocity from stored odds_snapshots.

    Anchors at earliest available snapshot (ideally T-24h when liquidity
    normalizes, but uses whatever we have since snapshots are new).

    Returns dict with drift metrics and penalty/veto flags.
    """
    result = {
        "odds_at_open": None,
        "odds_drift": 0.0,
        "drift_pct": 0.0,
        "drift_velocity": 0.0,
        "steam_move": False,
        "against_pick": False,
        "penalty": 0.0,    # 0.0 = no penalty, 0.0-1.0 = scale Kelly down
        "veto": False,      # hard veto only for extreme moves (>10%)
    }

    try:
        snapshots = execute_query(
            """SELECT odds, timestamp, minutes_to_kickoff
               FROM odds_snapshots
               WHERE match_id = %s AND market = %s AND selection = %s
               ORDER BY timestamp ASC""",
            [match_id, market, selection],
        )

        if not snapshots or len(snapshots) < 2:
            return result

        # Opening odds = earliest snapshot
        opening = snapshots[0]
        result["odds_at_open"] = float(opening["odds"])

        opening_implied = 1.0 / float(opening["odds"])
        current_implied = 1.0 / current_odds

        # Drift = change in implied probability (positive = shortened/stronger)
        drift = current_implied - opening_implied
        result["odds_drift"] = round(drift, 6)
        result["drift_pct"] = round(drift / opening_implied, 6) if opening_implied > 0 else 0.0

        # Velocity: drift per hour
        try:
            ts = opening["timestamp"]
            if hasattr(ts, "isoformat"):
                open_time = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            else:
                open_time = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            hours_elapsed = max(
                (datetime.now(timezone.utc) - open_time).total_seconds() / 3600,
                1.0
            )
            result["drift_velocity"] = round(drift / hours_elapsed, 6)
        except (ValueError, TypeError):
            pass

        # Steam move: >3% implied prob change
        result["steam_move"] = abs(result["drift_pct"]) > 0.03

        # Against pick: odds have lengthened (implied prob decreased)
        result["against_pick"] = drift < -0.005

        # --- Soft penalty instead of hard veto (assessment 2 recommendation) ---
        # Scale Kelly down proportionally to adverse movement.
        # No penalty for favorable movement, graduated penalty for adverse.
        if drift < -0.01:
            # Penalty scales from 0 (at -1%) to 0.8 (at -10%)
            adverse_pct = min(abs(drift), 0.10)
            result["penalty"] = round(adverse_pct / 0.10 * 0.8, 3)

        # Hard veto ONLY for extreme moves (>10% against pick)
        # This is where the market almost certainly knows something we don't
        result["veto"] = drift < -0.10

    except Exception:
        pass  # Non-critical — return defaults

    return result


# =============================================================================
# P3: ALIGNMENT — External signals only (LOG-ONLY MODE)
# =============================================================================
#
# IMPORTANT: Alignment is tracked but does NOT affect bet decisions yet.
# It will be activated after 300+ bets show ROI correlating with alignment.
#
# Only uses signals EXTERNAL to the model:
#   1. Odds movement (market's aggregated opinion)
#   2. News/injuries (Gemini analysis)
#   3. Lineup confirmation
#   4. Situational context (rest, motivation)
#
# Dropped from alignment (already in Poisson model):
#   - ELO/strength differential
#   - Form momentum
#   - xG over/underperformance
#   - H2H pattern (also noise per 3/4 assessments)

ALIGNMENT_DIMENSIONS = ["odds_move", "news", "lineup", "situation", "sharp", "pinnacle"]


def compute_alignment(
    match_id: str,
    selection: str,
    odds_movement: dict,
    match: dict,
) -> dict:
    """
    Compute alignment score from external signals only.

    Each dimension scores +1 (agrees with pick), 0 (neutral), or -1 (against).
    Alignment = count of agreeing dimensions / count of active dimensions.

    NOTE: This is LOG-ONLY. The alignment_class is stored on the bet record
    but does NOT modify stakes or filter bets. It will be activated once
    we have 300+ bets showing alignment correlates with ROI.

    Returns:
        {
            "dimensions": {"odds_move": 1, "news": 0, ...},
            "alignment_count": int,
            "alignment_total": int,
            "alignment_ratio": float,
            "alignment_class": "NONE" | "HIGH" | "MEDIUM" | "LOW",
        }
        alignment_class is "NONE" when no dimensions fired (active=0), so that
        LOW/MEDIUM/HIGH are only assigned when there is actual signal data.
    """
    dimensions = {}

    is_home_pick = selection.lower() == "home"
    is_away_pick = selection.lower() == "away"
    is_1x2 = is_home_pick or is_away_pick

    # --- Dimension 1: Odds Movement (market direction) ---
    dimensions["odds_move"] = _dim_odds_movement(odds_movement)

    # --- Dimension 2: News/External Info ---
    dimensions["news"] = _dim_news(match_id)

    # --- Dimension 3: Lineup Confirmation ---
    # Checks simulated_bets for lineup_confirmed flag (set by news_checker v2)
    dimensions["lineup"] = _dim_lineup(match_id)

    # --- Dimension 4: Situational Context (rest + home advantage in lower leagues) ---
    dimensions["situation"] = _dim_situational(match, is_home_pick, is_away_pick, is_1x2)

    # --- Dimension 5: Sharp consensus ---
    dimensions["sharp"] = _dim_sharp_consensus(match_id, selection)

    # --- Dimension 6: Pinnacle agreement ---
    # model_prob: use calibrated_prob from the bet record if available.
    # Falls back to 0.0 → _dim_pinnacle returns 0 (neutral) if no prob available.
    model_prob = match.get("calibrated_prob", 0.0) or 0.0
    dimensions["pinnacle"] = _dim_pinnacle(match_id, selection, float(model_prob))

    # --- Compute alignment ---
    agreeing = sum(1 for v in dimensions.values() if v > 0)
    active = sum(1 for v in dimensions.values() if v != 0)
    ratio = agreeing / active if active > 0 else 0.0

    # Classification — thresholds are provisional, will be set from data
    # after 300+ bets (per assessment 1 & 4 recommendation).
    # NONE = no dimensions fired at all (no external signal data available).
    if active == 0:
        alignment_class = "NONE"
    elif ratio >= 0.75:
        alignment_class = "HIGH"
    elif ratio >= 0.50:
        alignment_class = "MEDIUM"
    else:
        alignment_class = "LOW"

    return {
        "dimensions": dimensions,
        "alignment_count": agreeing,
        "alignment_total": active,
        "alignment_ratio": round(ratio, 3),
        "alignment_class": alignment_class,
    }


def _dim_odds_movement(odds_movement: dict) -> int:
    """
    Dimension 1: Odds movement direction.
    Positive drift (shortened) = market confirms pick = +1
    Negative drift (lengthened) = market against pick = -1
    """
    drift = odds_movement.get("odds_drift", 0)

    if drift > 0.01:
        return 1  # Market shortened → agrees with pick
    elif drift < -0.01:
        return -1  # Market lengthened → disagrees
    return 0


def _dim_news(match_id: str) -> int:
    """
    Dimension 2: News/external info impact.
    Checks news_events table for any flagged impacts on this match.
    """
    try:
        events = execute_query(
            "SELECT impact_type, impact_magnitude FROM news_events WHERE match_id = %s",
            [match_id],
        )

        if not events:
            return 0

        # Net impact: injuries/suspensions are negative, positive news is positive
        net_impact = 0.0
        for ev in events:
            mag = float(ev.get("impact_magnitude", 0) or 0)
            if ev.get("impact_type") in ("injury", "suspension"):
                net_impact -= mag
            elif ev.get("impact_type") in ("lineup", "transfer", "motivation"):
                net_impact += mag

        if abs(net_impact) < 10:
            return 0
        return 1 if net_impact > 0 else -1

    except Exception:
        return 0


def _dim_lineup(match_id: str) -> int:
    """
    Dimension 3: Lineup confirmation status.
    Confirmed lineup = +1 (we can trust our prediction more).
    Unconfirmed = 0 (neutral, no info).

    Reads lineup_confirmed from simulated_bets (set by news_checker v2).
    Note: This dimension only activates AFTER news_checker has run for this match.
    The morning pipeline runs before news_checker, so this will typically be 0
    on first pass and update to +1 on subsequent news_checker runs.
    """
    try:
        rows = execute_query(
            "SELECT id FROM simulated_bets WHERE match_id = %s AND lineup_confirmed = true LIMIT 1",
            [match_id],
        )
        if rows:
            return 1  # Lineup confirmed — our prediction is more trustworthy
    except Exception:
        pass

    return 0  # Unknown or not confirmed yet


def _dim_situational(match: dict, is_home: bool, is_away: bool,
                     is_1x2: bool) -> int:
    """
    Dimension 4: Situational context (rest + home advantage in lower leagues).
    """
    if not is_1x2:
        return 0  # Situational factors mainly affect 1X2

    # Home advantage is stronger in lower leagues
    tier = match.get("tier", 1)
    if tier >= 3 and is_home:
        return 1
    elif tier >= 3 and is_away:
        return -1

    return 0


def _dim_sharp_consensus(match_id: str, selection: str) -> int:
    """
    Dimension 5: Sharp bookmaker consensus (P5.1 signal).
    sharp_consensus_home > 0: sharp books price home higher than soft books.
    Only meaningful for 1X2 picks. O/U always returns 0 (neutral).
    """
    is_home = selection.lower() == "home"
    is_away = selection.lower() == "away"
    if not (is_home or is_away):
        return 0  # O/U, draw — no sharp consensus signal for these yet

    try:
        rows = execute_query(
            "SELECT signal_value FROM match_signals WHERE match_id = %s AND signal_name = 'sharp_consensus_home' ORDER BY captured_at DESC LIMIT 1",
            [match_id],
        )
        if not rows:
            return 0
        val = float(rows[0].get("signal_value") or 0)
        if abs(val) < 0.01:
            return 0  # Too small to be meaningful
        # Positive = sharp books price home higher
        if is_home:
            return 1 if val > 0.01 else (-1 if val < -0.01 else 0)
        else:  # away pick
            return 1 if val < -0.01 else (-1 if val > 0.01 else 0)
    except Exception:
        return 0


def _dim_pinnacle(match_id: str, selection: str, model_prob: float) -> int:
    """
    Dimension 6: Pinnacle anchor — does Pinnacle agree with our pick direction?
    We're betting on this selection because model_prob > implied_prob (positive edge).
    If Pinnacle implied is close to our model → Pinnacle doesn't strongly disagree → +1.
    If Pinnacle implied >> model_prob → sharp market strongly disagrees → -1.
    """
    is_home = selection.lower() == "home"
    if not is_home:
        return 0  # Only have pinnacle_implied_home for now

    try:
        rows = execute_query(
            "SELECT signal_value FROM match_signals WHERE match_id = %s AND signal_name = 'pinnacle_implied_home' ORDER BY captured_at DESC LIMIT 1",
            [match_id],
        )
        if not rows or model_prob <= 0:
            return 0
        pinnacle_implied = float(rows[0].get("signal_value") or 0)
        if pinnacle_implied <= 0:
            return 0
        gap = model_prob - pinnacle_implied  # positive = model rates higher
        # We're betting home because model finds value (gap should be positive)
        # If Pinnacle also agrees (small gap): neutral-to-positive
        # If Pinnacle strongly disagrees (gap very negative): bad sign
        if gap > -0.03:  # Pinnacle doesn't strongly disagree
            return 1
        elif gap < -0.08:  # Pinnacle strongly disagrees with our model
            return -1
        return 0
    except Exception:
        return 0


# =============================================================================
# P4: KELLY-BASED STAKE SIZING
# =============================================================================

# Fraction of Kelly to use — reduced from 0.25 to 0.15 (2026-04-29)
# With 6 concurrent bots, 0.25× was stacking up to 9% bankroll exposure.
KELLY_FRACTION = 0.15
# Maximum stake as fraction of bankroll — reduced from 1.5% to 1.0% (2026-04-29)
MAX_STAKE_PCT = 0.010

# Data tier multipliers (only non-model multiplier applied to stakes)
# Alignment multipliers are NOT active yet (log-only mode)
DATA_TIER_MULTIPLIERS = {
    "A": 1.0,
    "B": 0.5,
    "C": 0.25,
}


def compute_kelly(model_prob: float, odds: float) -> float:
    """
    Compute Kelly fraction for a bet.

    kelly = (p * odds - 1) / (odds - 1)

    Where p = calibrated probability, odds = decimal odds.

    Returns:
        Kelly fraction (0.0 if negative EV)
    """
    if odds <= 1.0 or model_prob <= 0 or model_prob >= 1:
        return 0.0

    kelly = (model_prob * odds - 1) / (odds - 1)
    return max(kelly, 0.0)


def compute_stake(
    kelly: float,
    bankroll: float,
    data_tier: str,
    odds_penalty: float = 0.0,
) -> float:
    """
    Compute stake using fractional Kelly with simplified multipliers.

    Simplified from 4-multiplier stack (assessment 4 flagged near-zero stakes)
    to: Kelly × data_tier × odds_penalty only.

    Alignment and tier multipliers are NOT applied yet (alignment is log-only,
    tier is already captured in the calibration alpha).

    Args:
        kelly: Raw Kelly fraction
        bankroll: Current bankroll
        data_tier: "A", "B", or "C"
        odds_penalty: 0.0-0.8 penalty from adverse odds movement

    Returns:
        Stake amount in EUR (rounded to 2dp), 0 if below minimum
    """
    if kelly <= 0 or bankroll <= 0:
        return 0.0

    base_stake = kelly * KELLY_FRACTION * bankroll
    max_stake = MAX_STAKE_PCT * bankroll
    stake = min(base_stake, max_stake)

    # Apply data tier multiplier
    stake *= DATA_TIER_MULTIPLIERS.get(data_tier, 0.5)

    # Apply odds movement penalty (0 = no penalty, 0.8 = 80% reduction)
    if odds_penalty > 0:
        stake *= (1.0 - odds_penalty)

    # Minimum stake floor — micro-bets are noise (assessment 4)
    if stake < 1.0:
        return 0.0

    return round(stake, 2)


def compute_rank_score(kelly: float, alignment_ratio: float) -> float:
    """
    Rank score for UI display / bot prioritization.
    Uses continuous alignment ratio (not class) for finer ranking.
    """
    # Weight alignment at 30% of rank (it's informational, not validated yet)
    alignment_weight = 0.3 * alignment_ratio + 0.7
    return round(kelly * alignment_weight, 6)
