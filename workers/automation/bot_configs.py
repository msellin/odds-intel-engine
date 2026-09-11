"""BOT-CONFIGS — the registry of real-money-eligible pick generators.

One entry per bot. The MECHANISM lives in `pick_generator.generate`; everything
here is gates and configuration, which is the shape the owner asked for:
*"the bots look very similar in the way they work, only the gates and
configuration of the bots are different."*

Adding a bot is an entry in `CONFIGS`. It is deliberately NOT a new job file —
the two mirrors this replaced were near-identical 250-line modules that had
already drifted apart in ways that cost real bets (one pre-filtered on the
pipeline's odds, the other applied no odds floor at all).

FLOORS ARE OMITTED ON PURPOSE. `edge_floor=None` takes the shared
selection-aware `min_edge_for_pick`; `odds_floor=None` takes
`_min_odds_for(market)`. Write a number here only to deliberately deviate from
the engine, and say why — the 1x2 edge floor had six independent copies once,
and re-typing it per bot is exactly how that happened.
"""
from __future__ import annotations

from workers.automation.best_price_router import PLACEABLE_BOOKS
from workers.automation.pick_generator import BotConfig


def _ou_convert(market: str, selection: str):
    """Pipeline O/U vocabulary -> the placer's. Returns None for a line we
    cannot place. Delegates to the shared normalizer so there is no second copy
    of the line encoding."""
    from workers.canonical_market import normalize
    c = normalize(market, selection)
    if (not c or c["family"] != "o/u"
            or c["market"] not in ("over_under_25", "over_under_35")):
        return None
    return c["market"], c["selection"]


CONFIGS: list[BotConfig] = [
    BotConfig(
        bot_name="bot_coolbet_1x2_model_v1",
        shadow_cohort="coolbet_1x2_model",
        markets=("1x2",),
        # FAVLONG-CUTS-2026-09-09: home-underdogs are the one fold-robust 1x2
        # engine. Home-favs lose at every floor, aways are not robust, and draws
        # are a sharp edge the model cannot see (ANALYSIS_GOTCHAS 57). The
        # odds floor then excludes home-favs by construction, so this selection
        # filter plus the registry floors IS the FAVLONG policy — no extra rule.
        selections=("home",),
        books=PLACEABLE_BOOKS,
        notes="model-edge 1x2, home-underdogs only; real-money eligible",
    ),
    BotConfig(
        bot_name="bot_coolbet_ou_model_v1",
        shadow_cohort="coolbet_ou_model",
        # Accepts BOTH the legacy 'o/u' spelling and the canonical ones so it
        # keeps feeding through the vocabulary migration.
        markets=("o/u", "over_under_25", "over_under_35"),
        books=PLACEABLE_BOOKS,
        convert=_ou_convert,
        notes="model-edge O/U 2.5/3.5; real-money eligible",
    ),
]

CONFIG_BY_NAME = {c.bot_name: c for c in CONFIGS}
