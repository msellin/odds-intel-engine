"""
TENNIS-PAPER-BETS Phase 2 — tennis paper-bot registry.

Soccer's `workers/jobs/daily_pipeline_v2.BOTS_CONFIG` covers ~16 personas across
1x2/OU/BTTS/AH/inplay. Tennis is simpler — one market (match_winner), 6 books
(Pinnacle sharp + 5 soft + Coolbet). The bot axis is just:
  (edge_threshold, allowed_bookmakers, stake_unit)

Three starter bots, all paper. Re-evaluate / expand only after each has ≥100
settled bets — the cap per `feedback_odds_quality_recurring` discipline.

Bot personas:
  bot_tennis_pin_broad     — any soft book, edge ≥ 3% (high volume, low bar)
  bot_tennis_pin_selective — any soft book, edge ≥ 5% (lower volume, higher bar)
  bot_tennis_coolbet_only  — Coolbet only, edge ≥ 3% (the placeable book — mirrors
                             real-money workflow even though placement stays off
                             until track record justifies it per
                             feedback_coolbet_execute_safety)
"""
from __future__ import annotations

from typing import Iterator


# ── Bot registry ─────────────────────────────────────────────────────────────
# `bookmakers=None` means "any book". `bookmakers=[...]` whitelists books.
TENNIS_BOTS: dict[str, dict] = {
    "bot_tennis_pin_broad": {
        "edge_threshold": 0.03,
        "bookmakers": None,
        "stake": 1.0,
        "maturity_label": "experimental",
        "description": (
            "Any soft book with edge ≥ 3% vs Pinnacle de-vigged fair odds. "
            "High volume / low bar — the baseline paper bot."
        ),
    },
    "bot_tennis_pin_selective": {
        "edge_threshold": 0.05,
        "bookmakers": None,
        "stake": 1.0,
        "maturity_label": "experimental",
        "description": (
            "Any soft book with edge ≥ 5%. Selective variant — should show "
            "higher hit rate but much lower volume than pin_broad."
        ),
    },
    "bot_tennis_coolbet_only": {
        "edge_threshold": 0.03,
        "bookmakers": ["coolbet"],
        "stake": 1.0,
        "maturity_label": "experimental",
        "description": (
            "Edge ≥ 3% on Coolbet specifically. Mirrors the placeable-book "
            "workflow so its ROI is the closest analogue to real-money outcomes "
            "(though placement stays paper-only until track record clears bar)."
        ),
    },
}


def matching_bots(*, bookmaker: str, edge: float) -> Iterator[tuple[str, dict]]:
    """
    Yield (bot_id, config) for every bot in TENNIS_BOTS that accepts an
    observation with the given bookmaker and edge (as a decimal, e.g. 0.03 = 3%).
    """
    for bot_id, cfg in TENNIS_BOTS.items():
        if edge < cfg["edge_threshold"]:
            continue
        allowed = cfg["bookmakers"]
        if allowed is not None and bookmaker not in allowed:
            continue
        yield bot_id, cfg
