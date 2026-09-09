"""SYSTEM-MAP-REGISTRY (2026-09-09) — the single source of truth for what every
active bot IS.

Why this file exists: the owner observed the betting system gets *harder* to
understand every time we touch bots / floors / anchors, because the facts about a
bot were scattered (bots table, the web SHADOW_BOTS list, coolbet_placer's
PLACEABLE_BOTS, the scheduler) with no one place required to stay true. This is
that one place. `docs/SYSTEM_MAP.md` is its human-readable form; the smoke test
`SYSTEM-MAP-REGISTRY-NOT-DRIFTED` fails CI if this file disagrees with the code or
the DB, so it cannot rot into fiction.

The one word that caused most of the confusion is "edge". There are TWO:
  * MODEL edge  = cal_prob − 1/book_odds   (anchor: our calibrated model)  floor 13%/8%
  * SHARP edge  = P_sharp  − 1/book_odds   (anchor: de-vigged Pinnacle)     floor ~3%
They are different quantities against different yardsticks — the floors are NOT
comparable across them. `anchor` on each spec says which one a bot uses.

Nothing here places bets or reads at runtime-critical paths; it is pure data +
lookups. PLACEABLE_BOTS stays hardcoded in scripts/place_coolbet_ui.py as a
defense-in-depth safety set — this registry does not re-derive it, the drift test
only asserts the two agree.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── anchor / family vocab ────────────────────────────────────────────────────
ANCHOR_MODEL = "model"   # fair value = our calibrated model probability
ANCHOR_SHARP = "sharp"   # fair value = Shin-de-vigged Pinnacle probability
ANCHOR_NONE = "none"     # internal strategy bot — not an edge-vs-book selection

FAM_COOLBET_REAL = "coolbet_real"      # places real money at Coolbet (gated)
FAM_TRIGGER = "trigger"                # book-agnostic trigger engine (paper)
FAM_COOLBET_PAPER = "coolbet_paper"    # Coolbet own-price paper bots
FAM_INTERNAL = "internal"              # internal model/strategy paper validators

FAMILY_TITLES = {
    FAM_COOLBET_REAL: "Real-money capable · Coolbet UI placer",
    FAM_TRIGGER: "Trigger engine · model vs sharp anchor (paper)",
    FAM_COOLBET_PAPER: "Coolbet own-price paper bots",
    FAM_INTERNAL: "Internal model / strategy validators (paper)",
}


@dataclass(frozen=True)
class BotSpec:
    name: str
    family: str
    market: str            # human label: '1x2', 'O/U 2.5', 'O/U 3.5', 'corners', 'mixed'
    anchor: str            # ANCHOR_MODEL | ANCHOR_SHARP | ANCHOR_NONE
    edge_floor: float | None   # required edge in prob-points (None = n/a)
    odds_floor: float | None
    real_money: bool       # True == must be in PLACEABLE_BOTS
    one_liner: str         # what it does, plain language
    twin: str | None = None    # the bot it is a head-to-head variant of


# ── the active bots (must match `bots` WHERE retired_at IS NULL) ─────────────
# Floors that are enforced by coolbet_placer are annotated; the drift test checks
# the model-bot / trigger floors against coolbet_placer._MIN_*_BY_MARKET.
BOTS: list[BotSpec] = [
    # Real-money capable — anchor = MODEL, validated floors 13%/8% + odds 2.80/1.80
    BotSpec("bot_coolbet_1x2_model_v1", FAM_COOLBET_REAL, "1x2", ANCHOR_MODEL,
            0.13, 2.80, True,
            "Places our calibrated model's 1x2 picks at Coolbet's own price when model edge ≥13% & odds ≥2.80. Real money, per-bot toggle."),
    BotSpec("bot_coolbet_ou_model_v1", FAM_COOLBET_REAL, "O/U 2.5", ANCHOR_MODEL,
            0.08, 1.80, True,
            "Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. Real money, per-bot toggle."),

    # Trigger engine — book-agnostic windows, PAPER. Model vs Sharp anchor twins.
    BotSpec("bot_coolbet_trigger_1x2_v1", FAM_TRIGGER, "1x2", ANCHOR_MODEL,
            0.13, 2.80, False,
            "Fires when Coolbet's 1x2 price lands in the MODEL trigger window (model edge ≥13% at Coolbet's own odds). Paper. OOS backtest −21% (adverse selection)."),
    BotSpec("bot_coolbet_trigger_sharp_1x2_v1", FAM_TRIGGER, "1x2", ANCHOR_SHARP,
            0.03, 1.50, False,
            "Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (odds ≥1.50). Paper. Head-to-head vs the model twin.",
            twin="bot_coolbet_trigger_1x2_v1"),
    BotSpec("bot_coolbet_trigger_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_MODEL,
            0.08, 1.80, False,
            "Fires when Coolbet's O/U 2.5 price lands in the MODEL trigger window (model edge ≥8% at Coolbet's own odds). Paper. OOS backtest +4.3% not-robust."),
    BotSpec("bot_coolbet_trigger_sharp_ou_v1", FAM_TRIGGER, "O/U 2.5", ANCHOR_SHARP,
            0.03, 1.50, False,
            "Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (odds ≥1.50). Paper. Head-to-head vs the model twin.",
            twin="bot_coolbet_trigger_ou_v1"),

    # Coolbet own-price paper bots
    BotSpec("bot_ou35_model_v1", FAM_COOLBET_PAPER, "O/U 3.5", ANCHOR_MODEL,
            0.08, 1.80, False,
            "Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic calibration). Paper. +7.8% not-robust, accruing forward."),
    BotSpec("bot_corners_paper_shadow_v1", FAM_COOLBET_PAPER, "corners", ANCHOR_SHARP,
            0.0, None, False,
            "Best Betano/Unibet corners price vs de-vigged Pinnacle corners line (sharp edge ≥0%). Forward paper test on executable corners books."),

    # Internal model / strategy validators (paper, not a Coolbet placement path)
    BotSpec("bot_v10_all", FAM_INTERNAL, "mixed", ANCHOR_MODEL,
            None, None, False,
            "The calibrated reference bot: v10 model across target leagues, tier-adjusted thresholds. Honestly calibrated, +11–13% — the yardstick other bots are read against."),
    BotSpec("bot_1x2_specialist", FAM_INTERNAL, "1x2", ANCHOR_NONE,
            None, None, False,
            "1x2 home/away value specialist with per-strategy league whitelists. Internal paper strategy validator."),
    BotSpec("bot_dnb_specialist", FAM_INTERNAL, "DNB", ANCHOR_NONE,
            None, None, False,
            "Draw-no-bet home+away specialist with per-strategy league whitelists. Internal paper strategy validator."),
    BotSpec("bot_high_roi_global_v2", FAM_INTERNAL, "1x2", ANCHOR_NONE,
            None, None, False,
            "1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator."),
    BotSpec("bot_summer_specialist", FAM_INTERNAL, "mixed", ANCHOR_NONE,
            None, None, False,
            "League-whitelist summer-season specialist. Internal paper strategy validator."),
]


def by_name(name: str) -> BotSpec | None:
    return next((b for b in BOTS if b.name == name), None)


def active_names() -> set[str]:
    return {b.name for b in BOTS}


def placeable_names() -> set[str]:
    """Bots this registry declares as real-money — MUST equal PLACEABLE_BOTS."""
    return {b.name for b in BOTS if b.real_money}


def family(fam: str) -> list[BotSpec]:
    return [b for b in BOTS if b.family == fam]
