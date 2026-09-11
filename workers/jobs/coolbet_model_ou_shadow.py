"""
COOLBET-MODEL-OU-SHADOW-BOT-2026-09-08 — the real-money-eligible model-edge
Over/Under bot: lines 2.5 and 3.5 only, priced at the books we can actually bet,
emitted into `shadow_bets` under `bot_coolbet_ou_model_v1` so the PROVEN UI
placer can stake it.

Why this bot exists (COOLBET-OWN-UNIFIED-FLOW-EPIC decision (a)): the strategy
backtest found the model-edge O/U instrument is +15% (fold-robust) at executable
prices where the line-shop bot's O/U is -17% (negative every month, n~1100 — a
live money leak). It does NOT invent model logic: it takes probabilities the
calibrated model already produced and re-prices them at our books.

PICK-GENERATOR-DELEGATION (2026-09-11) — THIS MODULE NO LONGER HOLDS A
MECHANISM. It used to carry its own ~190-line copy of the candidate query, the
per-book re-pricing loop and the upsert, and its 1x2 sibling carried a
near-identical copy. The two had already DRIFTED apart in ways that cost real
bets: this one applied NO odds floor at all, the 1x2 one pre-filtered on the
pipeline's odds (dropping Nancy v Reims on Betano's 3.15 while Coolbet was live
at 3.25 and clearing). Two copies of one mechanism is how that happens.

So the mechanism now lives in exactly one place —
`workers/automation/pick_generator.generate` — and this bot is a `BotConfig` in
`workers/automation/bot_configs.py`. What remains here is the scheduler's entry
point, the operator's env overrides, and this explanation. Read the generator
for HOW a pick is made; read the config for WHAT this bot's gates are
(`python3 scripts/bots_describe.py` prints them next to every other bot).

WHAT THIS BOT IS, in the axes that actually distinguish one bot from another:
    candidate source   `pipeline` — `simulated_bets.calibrated_prob` on the
                       'calibrated' cohort. Narrow (AF-API-derived, and it
                       contains no Coolbet prices at all), but it is the
                       probability this real-money bot was VALIDATED on. The
                       wide-source paper twin `bot_wide_ou_model_v1` exists to
                       measure the alternative rather than argue about it.
    anchor             our model
    market/selection   goals O/U, lines 2.5 and 3.5 ONLY. Any other line (1.5,
                       4.5, ...) is SKIPPED, never rounded — rounding would
                       fabricate both a price and a settlement line.
    floors             from the registry, not from here — `min_edge_for_pick`
                       (0.08) and `_min_odds_for('o/u')` (1.80). The env vars
                       below are the only override and they default to the
                       engine's own values.
    books              every placeable book, NOT just Coolbet. The bot and this
                       module keep their `coolbet_*` names for history — the
                       NAME is Coolbet, the SCOPE is both books. The winning
                       book is recorded on the row and the placer routes from
                       there rather than assuming Coolbet from the name.

VOCABULARY CONVERSION is the one genuinely per-bot transform, and it is declared
on the config as `convert=`:
    'o/u' + 'over 2.5'  -> market='over_under_25', selection='over'
    'o/u' + 'under 3.5' -> market='over_under_35', selection='under'
It is written in the LINE-SHOP vocabulary so this bot reuses the line-shop O/U
search/place path in the UI placer, and it routes through the ONE normalizer
(`workers.canonical_market.normalize`) so it accepts BOTH the legacy
simulated_bets encoding and the canonical one across the DB vocabulary flip.

Settlement: over_under_25/35 are STANDARD goals O/U markets, graded by the
generic shadow settler (`_r_ou_goals`, matched by the resolver registry's
"over_under" predicate). The corners_ou_ skip in the settler's pending query
does not touch them. No settler here.

Real money is OFF BY DEFAULT: the UI placer stakes this bot only when its
`coolbet_placer_bots` row is toggled `ui_place_enabled=true`, and only because
the bot is in the code-level `PLACEABLE_BOTS` allowlist (COOLBET-PLACER-CONTROL).
This job writes `shadow_bets`; it never places or touches a bankroll.

Run as:
    python3 -m workers.jobs.coolbet_model_ou_shadow
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

BOT_NAME = "bot_coolbet_ou_model_v1"
SHADOW_COHORT = "coolbet_ou_model"

# A null value in the engine registry means the market is RETIRED (btts and
# double_chance are None there, and _min_edge_for returns infinity so nothing
# clears). Fail LOUDLY rather than let this bot keep selecting picks for a
# market we no longer bet — a silently-resurrected floor is precisely the
# failure this consolidation exists to remove.
from workers.automation.coolbet_placer import _MIN_EDGE_BY_MARKET  # noqa: E402

if _MIN_EDGE_BY_MARKET.get("o/u") is None:
    raise RuntimeError(
        "_MIN_EDGE_BY_MARKET['o/u'] is None — the o/u market is retired in the "
        "engine, so this mirror must not keep selecting picks for it. Retire "
        "the bot or restore the floor; do not hardcode one here."
    )

# OPERATOR OVERRIDES ONLY. Unset — the normal state — means the config carries
# `None` for both floors and the generator takes the registry's value.
# Deliberately NOT read as `os.getenv(..., "0.08")`: that shape is how the
# floors came to have four and six independent copies, literals that only
# HAPPENED to equal the engine's values with nothing keeping them in step.
_ENV_EDGE_FLOOR = "COOLBET_MODEL_OU_EDGE_FLOOR"
_ENV_MIN_ODDS = "COOLBET_MODEL_OU_MIN_ODDS"


def _convert(market: str, selection: str) -> tuple[str, str] | None:
    """Return (over_under_market, side) for a supported O/U pick, else None.

    Kept as this module's public name because callers and tests reach for it
    here, but it is NOT a second copy: it delegates to the same `_ou_convert`
    the config declares, which delegates to the shared canonical normalizer.
    """
    from workers.automation.bot_configs import _ou_convert
    return _ou_convert(market, selection)


def config():
    """This bot's `BotConfig`, with any operator env override applied.

    The config is fetched from `bot_configs` rather than built here, so there is
    one statement of this bot's gates and `scripts/bots_describe.py` reads the
    same one the scheduler runs.
    """
    import dataclasses
    from workers.automation.bot_configs import CONFIGS

    cfg = next(c for c in CONFIGS if c.bot_name == BOT_NAME)
    over: dict = {}
    if os.getenv(_ENV_EDGE_FLOOR):
        over["edge_floor"] = float(os.environ[_ENV_EDGE_FLOOR])
    if os.getenv(_ENV_MIN_ODDS):
        over["odds_floor"] = float(os.environ[_ENV_MIN_ODDS])
    if over:
        log.warning("model-ou shadow: env override active %s — the registry "
                    "floors are NOT in force for this run", over)
        return dataclasses.replace(cfg, **over)
    return cfg


def floors() -> tuple[float, float]:
    """(edge floor, odds floor) this bot will actually apply, after overrides.

    Derived through the generator's own `_floors` so the number reported here
    cannot differ from the number that gates a pick.
    """
    from workers.automation.pick_generator import _floors
    return _floors(config(), "over_under_25", "over", None)


EDGE_FLOOR, MIN_ODDS = floors()


def generate_picks() -> dict:
    """Run this bot through the shared mechanism. Never raises (the generator
    swallows per-bot errors — a generation failure must not take down the sweep
    or pipeline that called it)."""
    from workers.automation.pick_generator import generate
    return generate(config())


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(generate_picks(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
