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


# ── WIDE-SOURCE PAPER TWINS (migration 330) ──────────────────────────────────
# Identical to the two above in every respect EXCEPT `prob_source`. That is the
# whole experiment: the real-money bots take candidates from `simulated_bets`,
# i.e. only fixtures the pipeline already picked, and that pick list is built
# from AF API odds which are not current and contain no Coolbet. Measured at one
# moment: 1 candidate vs 81; by day roughly 1,000 candidate selections against
# the pipeline's ~10 picks.
#
# Kept as PAPER twins rather than flipping the real-money bots, because the
# wider source is also a DIFFERENT probability — it re-calibrates raw
# predictions here, where the pipeline's calibrated_prob is what the real-money
# bots were validated on. Running both on the same mechanism, differing in one
# field, makes it a measurement instead of an argument.
WIDE_CONFIGS: list[BotConfig] = [
    BotConfig(
        bot_name="bot_wide_1x2_model_v1",
        shadow_cohort="wide_1x2_model",
        markets=("1x2",),
        selections=("home",),
        books=PLACEABLE_BOOKS,
        prob_source="predictions",
        notes="paper twin of the real-money 1x2 bot on the wide candidate source",
    ),
    BotConfig(
        bot_name="bot_wide_ou_model_v1",
        shadow_cohort="wide_ou_model",
        markets=("o/u", "over_under_25", "over_under_35"),
        books=PLACEABLE_BOOKS,
        convert=_ou_convert,
        prob_source="predictions",
        # RETIRED by migration 331 with the rest of WIDE_CONFIGS, so this never
        # runs — but for the record: the predictions source gained O/U on
        # 2026-09-11 (PREDICTIONS-SOURCE-OU), so the "inert" note that used to
        # sit here is no longer the reason it does not generate.
        notes="paper twin of the real-money O/U bot (retired — superseded by "
              "bot_trigger_ou_model_v1)",
    ),
]

# ── TRIGGER CONFIGS — 8 bots collapse to 4 ───────────────────────────────────
# The eight trigger bots were 2 anchors x 2 books x 2 markets. The BOOK is not a
# strategy, it is a venue, and the generator already compares across every book
# a bot may use — so the per-book split bought nothing and would have become 12
# bots the moment Epicbet joined. Collapsing it halves the count and makes the
# remaining axes the real ones: ANCHOR and MARKET.
#
# The per-book performance question does not disappear: `recommended_bookmaker`
# is recorded on every pick (fixed 2026-09-11 — it was NULL on 100% of trigger
# rows), so `bots_describe`/`floor_grid_sweep --group-by bookmaker` still splits
# them. Book became a column instead of an identity, which is where it belongs.
#
# ⚠️ THE SHARP CONFIGS SET `edge_floor` EXPLICITLY, and must. Their probability
# is a de-vigged Pinnacle line, so a 3% overlay is a REAL 3%; inheriting the
# registry's 13% model floor would demand a 13% overlay on Pinnacle, which is
# nearly unobservable (max seen +6.6%), and the bot would silently never fire.
# This is the one place a hand-written floor is correct rather than a sixth copy.
_SHARP_EDGE_FLOOR = 0.03
_SHARP_ODDS_FLOOR = 1.01   # effectively off: these are observational paper bots

TRIGGER_CONFIGS: list[BotConfig] = [
    BotConfig(
        bot_name="bot_trigger_1x2_model_v1",
        shadow_cohort="trigger_1x2_model",
        markets=("1x2",),
        books=PLACEABLE_BOOKS,
        prob_source="predictions",
        notes="model-anchored 1x2 trigger, both books; replaces "
              "bot_{coolbet,unibet}_trigger_1x2_v1",
    ),
    BotConfig(
        bot_name="bot_trigger_ou_model_v1",
        shadow_cohort="trigger_ou_model",
        markets=("over_under_25",),
        books=PLACEABLE_BOOKS,
        prob_source="predictions",
        notes="model-anchored O/U 2.5 trigger, both books; live since "
              "PREDICTIONS-SOURCE-OU (2026-09-11)",
    ),
    BotConfig(
        bot_name="bot_trigger_1x2_sharp_v1",
        shadow_cohort="trigger_1x2_sharp",
        markets=("1x2",),
        books=PLACEABLE_BOOKS,
        prob_source="sharp_devig",
        edge_floor=_SHARP_EDGE_FLOOR,
        odds_floor=_SHARP_ODDS_FLOOR,
        notes="sharp-anchored 1x2 trigger, both books; the only triggers with "
              "POSITIVE CLV so far (+8.2%/+9.7%) but n=13-30",
    ),
    BotConfig(
        bot_name="bot_trigger_ou_sharp_v1",
        shadow_cohort="trigger_ou_sharp",
        markets=("over_under_25",),
        books=PLACEABLE_BOOKS,
        prob_source="sharp_devig",
        edge_floor=_SHARP_EDGE_FLOOR,
        odds_floor=_SHARP_ODDS_FLOOR,
        notes="sharp-anchored O/U trigger, both books",
    ),
]

# WIDE_CONFIGS are RETIRED by migration 331 and deliberately NOT in the run set:
# `bot_trigger_1x2_model_v1` uses the same prob_source='predictions' and adds
# draw/away, so the home-only wide twin is a strict subset of it and would write
# duplicate home rows under a second name. The comparison the twins were created
# for is unchanged — it is the merged trigger bot filtered to home, against
# `bot_coolbet_1x2_model_v1`. Kept here as the record of why, not as config.
ALL_CONFIGS: list[BotConfig] = CONFIGS + TRIGGER_CONFIGS

CONFIG_BY_NAME = {c.bot_name: c for c in ALL_CONFIGS}
