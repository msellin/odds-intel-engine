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
import os

from rich.console import Console
console = Console()
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
# These are the hardcoded fallback values. The pipeline loads learned values
# from model_calibration at startup (via load_shrinkage_alphas()).
CALIBRATION_ALPHA = {
    1: 0.20,
    2: 0.30,
    3: 0.50,
    4: 0.65,
}
CALIBRATION_ALPHA_DEFAULT = 0.35

# Goal-line markets (BTTS, O/U) use higher alpha — Poisson expected-goals
# model is specifically designed for these, so we trust it more than for 1x2.
CALIBRATION_ALPHA_GOALLINE = {
    1: 0.35,
    2: 0.45,
    3: 0.65,
    4: 0.80,
}
_GOALLINE_PREFIXES = ("btts", "over", "under")

# Cache: loaded once per pipeline run by load_shrinkage_alphas()
_shrinkage_alphas: dict[str, float] | None = None


def load_shrinkage_alphas() -> dict[str, float]:
    """
    Load learned shrinkage alphas from model_calibration table.
    Keys: 'shrinkage_alpha_t{tier}_{market_type}', e.g. 'shrinkage_alpha_t1_1x2'
    Returns empty dict if no rows exist (falls back to hardcoded CALIBRATION_ALPHA).
    Cached for the lifetime of the process.
    """
    global _shrinkage_alphas
    if _shrinkage_alphas is not None:
        return _shrinkage_alphas

    _shrinkage_alphas = {}
    try:
        rows = execute_query(
            """
            SELECT market, platt_a, fitted_at
            FROM model_calibration
            WHERE market LIKE 'shrinkage_alpha_%%'
            ORDER BY fitted_at DESC
            """,
            [],
        )
        seen: set = set()
        for row in rows:
            mkt = row["market"]
            if mkt not in seen:
                _shrinkage_alphas[mkt] = float(row["platt_a"])
                seen.add(mkt)
    except Exception:
        pass  # Table may not have shrinkage rows yet — fall back to hardcoded

    return _shrinkage_alphas


def reset_shrinkage_cache():
    """Force reload of shrinkage alphas on next call. Used by tests."""
    global _shrinkage_alphas
    _shrinkage_alphas = None


def _get_shrinkage_alpha(tier: int, goalline: bool) -> float:
    """
    Return shrinkage alpha for (tier, market_type).
    Prefers learned values from model_calibration; falls back to hardcoded.
    """
    market_type = "goalline" if goalline else "1x2"
    learned = load_shrinkage_alphas()
    key = f"shrinkage_alpha_t{tier}_{market_type}"
    if key in learned:
        return learned[key]
    if goalline:
        return CALIBRATION_ALPHA_GOALLINE.get(tier, CALIBRATION_ALPHA_DEFAULT)
    return CALIBRATION_ALPHA.get(tier, CALIBRATION_ALPHA_DEFAULT)


def calibrate_prob(model_prob: float, implied_prob: float,
                   tier: int = 1, market: str = "",
                   anchor_implied: float | None = None,
                   odds: float | None = None) -> float:
    """
    Two-stage calibration:
      1. Tier-specific shrinkage toward an implied probability anchor
      2. Platt sigmoid correction (if parameters available for this market)

    Stage 1 (shrinkage):
      adjusted = alpha * model_prob + (1 - alpha) * anchor

      Anchor priority (CAL-PIN-SHRINK, 2026-05-06):
        Pinnacle-implied > market-implied (implied_prob)
        Pinnacle vig is 2-3% vs 5-8% for soft books, so Pinnacle implied
        probabilities are closer to true probabilities. Soft books price
        for liability management, not probability estimation.

      Odds-conditional alpha (CAL-ALPHA-ODDS, 2026-05-06):
        When odds > 3.0, alpha is boosted by +0.20 (capped at 0.85).
        Live data (31 settled home bets): the 0.30-0.40 probability bin
        showed 13% actual win rate vs 35.5% predicted — all longshot bets.
        Pulling these harder toward the anchor reduces false edge detection.

    Stage 2 (Platt):
      calibrated = 1 / (1 + exp(-(a * adjusted + b)))
      Parameters a, b loaded from model_calibration table.
      Skipped if no params exist for this market (graceful no-op).

    Args:
        model_prob: Raw model probability (Poisson/XGBoost/ensemble)
        implied_prob: 1/odds (bookmaker-implied probability before margin)
        tier: League tier (1-4)
        market: Market key for Platt lookup (e.g. '1x2_home')
        anchor_implied: Pinnacle-implied prob when available (CAL-PIN-SHRINK)
        odds: Decimal odds for odds-conditional alpha boost (CAL-ALPHA-ODDS)

    Returns:
        Calibrated probability
    """
    if implied_prob <= 0 or implied_prob >= 1:
        return model_prob
    mkt_lower = market.lower()

    # AH-CAL-BYPASS (2026-05-24): AH and DC market probabilities are DERIVED from
    # already-Platt-calibrated 1X2 outputs (AH via _solve_lambdas_calibrated inverting
    # pred["home_prob"]/draw_prob → lambdas; DC via direct sums like home_prob+draw_prob).
    # Applying tier shrinkage a SECOND time toward `ip` is double-discounting our model
    # signal — confirmed empirically by the post-AH-HOME-BIAS-fix bot silence (May 21+):
    # ~170 AH-away candidates per day, ALL killed at the post-shrinkage 5% edge gate.
    # Pre-fix the AH home bias inflated raw probs ~7.5pp which masked the issue.
    # Skip stage 1 here; apply_platt below is already a no-op for these markets
    # (no Platt fit stored for `asian_handicap_*` or `double_chance_*`).
    if mkt_lower.startswith("asian_handicap") or mkt_lower.startswith("double_chance"):
        return _apply_stage2(model_prob, market, odds=odds)

    goalline = any(mkt_lower.startswith(p) for p in _GOALLINE_PREFIXES)
    alpha = _get_shrinkage_alpha(tier, goalline)

    # CAL-ALPHA-ODDS: for longshot bets (odds > 3.0), reduce model weight so
    # the calibration pulls harder toward the anchor (market/Pinnacle).
    # NOTE: alpha here is MODEL weight (lower = more anchor trust). The original
    # task spec used the inverse convention, so "alpha + 0.20" in the task meant
    # "more market trust" — achieved here by decreasing alpha.
    # Floor of 0.10 ensures we never fully discard the model signal.
    #
    # CAL-ALPHA-ODDS-V2 (2026-05-25): graduated by odds bucket. The
    # platt_overconfidence_deepdive.py audit found odds is the dominant
    # explainer of the 30-50% bin overconfidence:
    #   odds 2.5-3.0: -10pp gap (needs modest extra pull)
    #   odds 3.0-3.5: +2.3pp (already well-calibrated by current -0.20)
    #   odds 3.5-4.0: -12pp (needs more pull)
    #   odds 4.0+:   -20pp (catastrophic; current -0.20 is insufficient)
    # Env-gated: CAL_ALPHA_ODDS_V2_ENABLED=true activates graduated buckets.
    # Default OFF — preserves the current single -0.20 step.
    if odds is not None:
        if os.getenv("CAL_ALPHA_ODDS_V2_ENABLED", "false").lower() in ("true", "1", "yes"):
            if odds >= 4.0:
                alpha = max(alpha - 0.35, 0.10)    # longshots: harder pull
            elif odds >= 3.5:
                alpha = max(alpha - 0.25, 0.10)
            elif odds >= 3.0:
                alpha = max(alpha - 0.20, 0.10)    # current behaviour kept here
            elif odds >= 2.5:
                alpha = max(alpha - 0.10, 0.10)    # mild pull on mid-low odds
        elif odds > 3.0:
            alpha = max(alpha - 0.20, 0.10)

    # CAL-PIN-SHRINK: use Pinnacle-implied as shrinkage anchor when available
    effective_anchor = (
        anchor_implied
        if anchor_implied is not None and 0 < anchor_implied < 1
        else implied_prob
    )

    shrunk = alpha * model_prob + (1 - alpha) * effective_anchor

    # Stage 2: Platt sigmoid / 2-feature logistic / isotonic (if available)
    # Dispatcher reads STAGE2_CALIBRATOR env var; default 'platt' = no change.
    return _apply_stage2(shrunk, market, odds=odds)


# =============================================================================
# P1b: PLATT SCALING — Learned sigmoid post-hoc calibration
# =============================================================================

# Cache: loaded once per pipeline run, refreshed weekly by fit_platt.py
_platt_params: dict[str, tuple[float, float, float | None]] | None = None


def load_platt_params() -> dict[str, tuple[float, float, float | None]]:
    """
    Load latest calibration params per market from model_calibration table.

    Returns dict: market → (a, b, c) where:
      - 1-feature Platt (1X2): c=None, apply sigmoid(a*prob + b)
      - 2-feature logistic (O/U, CAL-PLATT-UPGRADE): c=w1, apply sigmoid(a*shrunk + c*log(odds) + b)

    Empty dict if table doesn't exist or is empty. Cached for process lifetime.
    """
    global _platt_params
    if _platt_params is not None:
        return _platt_params

    _platt_params = {}
    try:
        # PLATT-LIMIT-30-TRUNCATION-2026-09-03. This was
        # `ORDER BY fitted_at DESC LIMIT 30`. The daily settlement fit writes
        # 16 rows per run and runs twice a day, so the 30-row window covered
        # less than one day's fits — and `model_calibration` holds 29 distinct
        # markets across 1,470 rows.
        #
        # Result: 13 markets were permanently invisible, including btts_yes,
        # btts_no, double_chance_1x/x2 and asian_handicap*. `apply_platt` was a
        # silent no-op for every one of them — it returns the input unchanged
        # when the market is absent, so BTTS bets went to market on raw
        # ensemble probabilities while the code, the DB and the fitting job all
        # looked healthy. btts_yes had a perfectly good fit (a=3.885,
        # b=-2.563, n=261, 2026-08-30) that could never be read.
        #
        # DISTINCT ON takes the newest row per market with no row cap, so
        # adding a market can no longer push another one out of scope.
        rows = execute_query(
            """SELECT DISTINCT ON (market) market, platt_a, platt_b, platt_c
                 FROM model_calibration
                ORDER BY market, fitted_at DESC""",
            [],
        )
        seen: set = set()
        for row in rows:
            mkt = row["market"]
            if mkt not in seen:
                a = float(row["platt_a"])
                b = float(row["platt_b"])
                c = float(row["platt_c"]) if row.get("platt_c") is not None else None
                _platt_params[mkt] = (a, b, c)
                seen.add(mkt)
    except Exception:
        pass  # Table may not exist yet — graceful no-op

    return _platt_params


def apply_platt(prob: float, market: str, odds: float | None = None) -> float:
    """
    Apply learned calibration correction for this market.

    1-feature Platt (1X2, platt_c IS NULL):
        calibrated = 1 / (1 + exp(-(a * prob + b)))

    2-feature logistic (O/U, platt_c IS NOT NULL — CAL-PLATT-UPGRADE):
        calibrated = 1 / (1 + exp(-(a * prob + c * log(odds) + b)))
        Falls back to 1-feature if odds not provided.

    Returns prob unchanged if no params available for this market.
    """
    if not market:
        return prob

    params = load_platt_params()
    # Try exact key first; fall back to the market root so aggregate Platt
    # (e.g. "asian_handicap") covers all line-specific keys until per-line
    # data is sufficient to fit individually.
    _MARKET_ROOTS = ("asian_handicap", "double_chance", "btts", "1x2",
                     "over_under", "draw_no_bet")
    key = market
    if key not in params:
        key = next((r for r in _MARKET_ROOTS if market.startswith(r)), market)
    if key not in params:
        return prob

    a, b, c = params[key]

    if c is not None and odds is not None and odds > 1.0:
        z = a * prob + c * math.log(odds) + b
    else:
        z = a * prob + b

    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def reset_platt_cache():
    """Force reload of Platt params on next call. Used by tests."""
    global _platt_params
    _platt_params = None


# =============================================================================
# P1c: ISOTONIC CALIBRATION (CALIBRATION-ISOTONIC-IMPL, 2026-05-25)
# =============================================================================
#
# Isotonic regression as an alternative Stage-2 calibrator. Found 2026-05-25
# via LONGSHOT-GEO-AUDIT + calibrate_isotonic_test.py: isotonic beats Platt
# by +0.0115 ECE on the candidate bundle (20% relative). The 30-50% predicted-
# prob bin (where most bot edges live) is where Platt is systematically -12 to
# -16pp overconfident; isotonic closes most of that gap.
#
# Env gate: STAGE2_CALIBRATOR
#   'platt'    (default) — existing behaviour, no changes
#   'isotonic' — load and apply per-market isotonic models from the active
#                bundle's data/models/soccer/<version>/isotonic_<market>.pkl
#
# Falls back to Platt when:
#   - env var unset or 'platt'
#   - isotonic file missing for the market (graceful fallback, logs once)
#   - load failure
#
# Fit via scripts/fit_isotonic_offline.py — produces one .pkl per market.

_isotonic_models: dict[str, object] | None = None
_isotonic_mode_warned: bool = False
_isotonic_missing_logged: set[str] = set()


def load_isotonic_models() -> dict[str, object]:
    """Load per-market IsotonicRegression instances from the active bundle dir.

    Reads from data/models/soccer/<MODEL_VERSION>/isotonic_<market>.pkl.
    Empty dict if no isotonic models present — apply_isotonic then falls
    back to Platt.

    Cached for process lifetime; reset via reset_isotonic_cache().
    """
    global _isotonic_models
    if _isotonic_models is not None:
        return _isotonic_models
    _isotonic_models = {}
    try:
        from pathlib import Path
        import joblib
        version = os.getenv("MODEL_VERSION", "")
        if not version:
            return _isotonic_models
        bundle_dir = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "soccer" / version
        if not bundle_dir.exists():
            return _isotonic_models
        for f in bundle_dir.glob("isotonic_*.pkl"):
            market = f.stem.replace("isotonic_", "")
            try:
                _isotonic_models[market] = joblib.load(f)
            except Exception as e:
                console.print(f"[yellow]Failed to load {f.name}: {e}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]load_isotonic_models error: {e}[/yellow]")
    return _isotonic_models


def apply_isotonic(prob: float, market: str, odds: float | None = None) -> float:
    """Apply isotonic calibration for this market. Falls back to Platt
    if no isotonic model is loaded for the market.

    ISOTONIC-BUNDLE-MISMATCH-2026-09-03: `odds` was previously not a parameter,
    so both fallbacks called `apply_platt(prob, market)` and silently dropped
    the price. That disables Platt's 2-feature O/U logistic
    (`a*prob + c*log(odds) + b`) by construction — and since the active bundle
    v20260712 ships NO isotonic models at all, every stage-2 call on the VPS
    took that fallback. The env var said isotonic; the code delivered
    Platt-minus-odds.
    """
    if not market:
        return prob
    models = load_isotonic_models()
    model = models.get(market)
    if model is None:
        if market not in _isotonic_missing_logged:
            _isotonic_missing_logged.add(market)
            console.print(f"[dim]isotonic: no model for '{market}' — falling back to Platt[/dim]")
        return apply_platt(prob, market, odds=odds)
    try:
        calibrated = float(model.predict([prob])[0])
        return max(0.0, min(1.0, calibrated))
    except Exception:
        return apply_platt(prob, market, odds=odds)


def reset_isotonic_cache():
    """Force reload of isotonic models on next call. Used by tests."""
    global _isotonic_models, _isotonic_missing_logged
    _isotonic_models = None
    _isotonic_missing_logged = set()


def _apply_stage2(prob: float, market: str, odds: float | None = None) -> float:
    """Stage-2 calibration dispatch — picks isotonic or Platt based on
    STAGE2_CALIBRATOR env var. Default = 'platt' (no behaviour change).
    Activate isotonic on 2026-06-08 via `STAGE2_CALIBRATOR=isotonic`.
    """
    mode = os.getenv("STAGE2_CALIBRATOR", "platt").lower()
    if mode == "isotonic":
        # ISOTONIC-BUNDLE-MISMATCH-2026-09-03: say so once when the env var
        # asks for isotonic and the active bundle has none. Previously this
        # degraded to Platt in complete silence, so the VPS ran for weeks with
        # STAGE2_CALIBRATOR=isotonic and zero isotonic models — a configuration
        # that looked deliberate from every angle except the filesystem.
        global _isotonic_mode_warned
        if not _isotonic_mode_warned and not load_isotonic_models():
            _isotonic_mode_warned = True
            console.print(
                "[yellow]STAGE2_CALIBRATOR=isotonic but the active model "
                "bundle ships no isotonic_*.pkl — every call is falling back "
                "to Platt. Either fit isotonic models for this version "
                "(scripts/fit_isotonic_offline.py) or set "
                "STAGE2_CALIBRATOR=platt so the choice is explicit.[/yellow]"
            )
        return apply_isotonic(prob, market, odds=odds)
    return apply_platt(prob, market, odds=odds)


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

    NOTE: ALN-1 activated 2026-05-12. LOW-alignment bets require +1% edge
    in the pipeline (daily_pipeline_v2.py). HIGH/MEDIUM/NONE thresholds
    unchanged until sample sizes grow. Data at activation: 347 aligned bets,
    HIGH=3 (+223% ROI), MEDIUM=11 (+29.6%), LOW=255 (+16.4%), NONE=78 (+19.1%).

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

    LINEUP-CONFIDENCE-CLEANUP (2026-05-24): reads directly from matches.lineups_fetched_at,
    the only source of truth we actually populate. Previously read simulated_bets.lineup_confirmed
    which was set from Gemini's broken lineup_confidence (locked at 0.5 for all 3,647 rows,
    so the >=0.9 threshold never fired — dimension always returned 0). Signal: bets on
    matches with lineups fetched hit 45.1% / +8.1% ROI vs 33.5% / -4.5% without (n=1,752).
    """
    try:
        rows = execute_query(
            "SELECT id FROM matches WHERE id = %s AND lineups_fetched_at IS NOT NULL LIMIT 1",
            [match_id],
        )
        if rows:
            return 1
    except Exception:
        pass

    return 0


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
    if odds <= 1.0 or model_prob <= 0 or model_prob >= 1:  # NOSONAR
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
