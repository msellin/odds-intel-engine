"""
COOLBET-MODEL-1X2-SHADOW-BOT-2026-09-08 — the real-money-eligible model-edge 1x2
bot: HOME-UNDERDOGS only, priced at the books we can actually bet, emitted into
`shadow_bets` under `bot_coolbet_1x2_model_v1` so the PROVEN UI placer can stake
it.

PICK-GENERATOR-DELEGATION (2026-09-11) — THIS MODULE NO LONGER HOLDS A
MECHANISM. It used to carry its own ~180-line copy of the candidate query, the
per-book re-pricing loop and the upsert. Its O/U sibling carried a
near-identical copy, and the two had already DRIFTED apart in ways that cost
real bets: this one pre-filtered on the pipeline's odds (dropping Nancy v Reims
on Betano's 3.15 while Coolbet was live at 3.25 and clearing), the O/U one
applied no odds floor at all. Two copies of one mechanism is how that happens,
and a third bot would have drifted further.

So the mechanism now lives in exactly one place —
`workers/automation/pick_generator.generate` — and this bot is a `BotConfig` in
`workers/automation/bot_configs.py`. What remains here is the scheduler's entry
point, the operator's env overrides, and this explanation. Read the generator
for HOW a pick is made; read the config for WHAT this bot's gates are
(`python3 scripts/bots_describe.py` prints them next to every other bot).

WHAT THIS BOT IS, in the axes that actually distinguish one bot from another:
    candidate source   `pipeline` — `simulated_bets.calibrated_prob` on the
                       'calibrated' cohort. Narrow (AF-API-derived, no Coolbet
                       prices in it at all), but it is the probability this
                       real-money bot was VALIDATED on. The wide-source paper
                       twin `bot_wide_1x2_model_v1` exists to measure the
                       alternative rather than argue about it.
    anchor             our model
    market/selection   1x2, HOME only — FAVLONG-CUTS-2026-09-09: home-underdogs
                       are the one fold-robust 1x2 engine. Home-favs lose at
                       every floor, aways are not robust, and draws are a sharp
                       edge the model cannot see (ANALYSIS_GOTCHAS 57). The odds
                       floor then excludes home-favs by construction, so
                       "home + the registry floors" IS the FAVLONG policy.
    floors             from the registry, not from here — `min_edge_for_pick`
                       (0.10 for home-underdogs) and `_min_odds_for('1x2')`
                       (2.80). The 1x2 edge floor once had six independent
                       copies; the env vars below are the only override and they
                       default to the engine's own values.
    books              every placeable book, NOT just Coolbet. The bot and this
                       module keep their `coolbet_*` names for history — the
                       NAME is Coolbet, the SCOPE is both books (owner,
                       2026-09-11: "we share the bot for the books we place,
                       this 1x2 bot is for both unibet and coolbet"). The
                       winning book is recorded on the row and the placer routes
                       from there rather than assuming Coolbet from the name.

Settlement: '1x2' is a STANDARD match-result market, graded by the generic
shadow settler via the resolver registry's 1x2 predicate. No settler here.

Real money is OFF BY DEFAULT: the UI placer stakes this bot only when its
`coolbet_placer_bots` row is toggled `ui_place_enabled=true`, and only because
the bot is in the code-level `PLACEABLE_BOTS` allowlist (COOLBET-PLACER-CONTROL).
This job writes `shadow_bets`; it never places or touches a bankroll.

Run as:
    python3 -m workers.jobs.coolbet_model_1x2_shadow
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

BOT_NAME = "bot_coolbet_1x2_model_v1"
SHADOW_COHORT = "coolbet_1x2_model"

# OPERATOR OVERRIDES ONLY. Unset — which is the normal state — means the config
# carries `None` for both floors and the generator takes the registry's
# selection-aware value. Deliberately NOT read as `os.getenv(..., "0.10")`: that
# shape is how this module came to hold one of six independent copies of the 1x2
# edge floor, a literal that only HAPPENED to equal the engine's value with
# nothing keeping the two in step.
_ENV_EDGE_FLOOR = "COOLBET_MODEL_1X2_EDGE_FLOOR"
_ENV_MIN_ODDS = "COOLBET_MODEL_1X2_MIN_ODDS"


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
        log.warning("model-1x2 shadow: env override active %s — the registry "
                    "floors are NOT in force for this run", over)
        return dataclasses.replace(cfg, **over)
    return cfg


def floors() -> tuple[float, float]:
    """(edge floor, odds floor) this bot will actually apply, after overrides.

    Derived through the generator's own `_floors` so the number reported here
    cannot differ from the number that gates a pick. Evaluated at HOME because
    that is the only selection this bot bets, and the 1x2 edge floor is
    selection-aware (home-underdogs 0.10, pooled 0.13).
    """
    from workers.automation.pick_generator import _floors
    return _floors(config(), "1x2", "home", None)


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
