"""PLACEMENT FLOOR — the ONE rule every real-money placer applies to a pick ([[#162]] W4.3).

WHY THIS EXISTS. Before 2026-09-25 each placer carried its own edge floor, and they disagreed:

  * `scripts/place_coolbet_ui.py` used `BOT_THRESHOLDS.get(bot, 0.03)`: selection-BLIND (the 1x2
    bot's 10% applied to every selection) and 3 pp for any bot not in the map, i.e. every sharp bot.
  * `best_price_router._route` used the same map and then stacked `clears_edge_floor()` (the 13 / 10 / 8
    pp model floor) on top of it, so the same sharp bot needed 13 pp there.
  * Neither applied a bot's own odds CEILING or edge CEILING, the phantom-price guards the generator
    itself enforces.

So one sharp pick would pass one placer and fail the other. The rule now lives here, and both placers
(and the Pick queue parity test) call `pick_clears()`.

THE RULE (owner 2026-09-25, plan decision 3C): real money takes the STRICTER of the bot's own rule
and today's market rule, unless the owner explicitly opts a bot out (`MARKET_FLOOR_OPT_OUT`, empty):

  edge floor  = max(bot floor, min_edge_for_pick(market, selection, odds))
  odds floor  = max(bot odds floor, _min_odds_for(market))
  odds max    = the bot's own ceiling, if it has one
  edge max    = the bot's own edge ceiling, if it has one
  selections  = the bot's own selection filter, if it has one

EDGE UNIT. Every one of the 11 placeable bots measures edge in probability points,
`p − 1/odds` (pick_generator, pick_triggers `_window`, ou35_model_shadow). `EDGE_UNIT = "pp"` says so
explicitly, and `edge_pp()` is the only conversion. A multiplicative floor (`p·odds − 1`) read as pp
would be a different gate. None exists among the placeable bots today; if one is ever added, it must
be converted here rather than compared raw.

⚠️ SHARP BOTS AND THE MARKET FLOOR. The sharp bots generate at 3 pp against de-vigged Pinnacle, and
[[#007]] explains why a 13 pp model floor cannot select a correctly priced sharp bet (max genuine
overlay seen is +6.6%). Under decision 3C they still face that floor for REAL money, so a sharp pick
can clear it only on a price far above fair — which the bot's own outlier cap and edge ceiling then
refuse. In practice they almost never place. That is the owner's default, not an accident: putting a sharp bot in
`MARKET_FLOOR_OPT_OUT` is the owner's switch to make it placeable at its own floor. The paper record
is unaffected either way, because the generators keep their own floors.

FAIL CLOSED. An unknown bot, a market the bot does not trade, a missing probability or price, and
an empty admissible window (floor above ceiling) all refuse. Pure: no DB, no network.
"""
from __future__ import annotations

from dataclasses import dataclass

EDGE_UNIT = "pp"

# Owner-only: bots that place at their OWN edge/odds floor without the market floor stacked on top.
# Empty on purpose (decision 3C). Adding a name here is a real-money policy change.
MARKET_FLOOR_OPT_OUT: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BotRule:
    bot: str
    anchor: str                         # 'model' | 'sharp'
    markets: tuple[str, ...]            # canonical families ('1x2', 'o/u')
    edge_floor: float | None            # None = the registry's selection-aware floor
    odds_floor: float | None            # None = the registry's per-market floor
    odds_max: float | None = None
    edge_ceiling: float | None = None
    selections: tuple[str, ...] | None = None
    # Sharp bots only: price cap = outlier_mult x max(1/(p - own edge floor), own odds floor), the
    # generator's own window (pick_triggers._window, pick_generator OUTLIER-CAP). A price that far
    # above fair value is a stale or mis-mapped quote — and under the stacked floor it is the ONLY
    # kind of price a sharp pick could clear at, so the cap matters more here, not less.
    outlier_mult: float | None = None
    source: str = ""


def _canon(market: str | None) -> str | None:
    from workers.automation.coolbet_placer import _canon_market
    return _canon_market(market) if market else None


def bot_rules() -> dict[str, BotRule]:
    """Every placeable bot's own rule, read from the SAME constants its generator uses. Not a copy:
    change a generator's floor and this changes with it."""
    from workers.automation.bot_configs import ALL_CONFIGS
    from workers.jobs import pick_triggers as pt
    from workers.jobs import pick_trigger_matcher as pm
    from workers.jobs import ou35_model_shadow as ou35
    from workers.automation.anchor_sanity import OUTLIER_MULT

    # Defensive by construction: one malformed config must not raise out of here, because every
    # placer calls this per pick — a bot whose rule cannot be built is simply absent, and absent
    # means refused (fail closed, quietly, per bot).
    out: dict[str, BotRule] = {}
    for c in ALL_CONFIGS:
        fams = {_canon(m) for m in c.markets} - {None}
        if not fams:
            continue
        sharp = c.prob_source == "sharp_devig"
        out[c.bot_name] = BotRule(
            bot=c.bot_name,
            anchor="sharp" if sharp else "model",
            markets=tuple(sorted(fams)),
            edge_floor=c.edge_floor, odds_floor=c.odds_floor,
            edge_ceiling=c.edge_ceiling,
            selections=tuple(s.lower() for s in c.selections) if c.selections else None,
            outlier_mult=float(OUTLIER_MULT) if sharp else None,
            source="workers/automation/bot_configs.py",
        )
    strat = {s: (mkt, key) for s, mkt, key, _sides in pt._SHARP_STRATEGIES}
    for (_book, market, strategy), bot in pm.BOOK_MARKET_BOTS.items():
        key = (strat.get(strategy) or (None, None))[1]
        fam = _canon(market)
        if key not in pt._SHARP_MIN_EDGE_BY_MARKET or key not in pt._SHARP_MIN_ODDS_BY_MARKET or fam is None:
            continue
        out[bot] = BotRule(
            bot=bot, anchor="sharp", markets=(fam,),
            edge_floor=pt._SHARP_MIN_EDGE_BY_MARKET[key],
            odds_floor=pt._SHARP_MIN_ODDS_BY_MARKET[key],
            odds_max=pt._SHARP_MAX_ODDS_BY_STRATEGY.get(strategy),
            # [[#162]] W7.6: the generator (sharp_engine via pick_triggers.sharp_rule) applies this
            # per-market edge ceiling; the placement re-check applies the same one.
            edge_ceiling=getattr(pt, "_SHARP_MAX_EDGE_BY_MARKET", {}).get(key),
            outlier_mult=float(OUTLIER_MULT),
            source="workers/jobs/pick_triggers.py",
        )
    # [[#191]] the OWN bot (paper; real money only if the owner switches it on). Its generator gates on EV >= 3%
    # over the v2 anchor with the sharp engine's 8% ceiling; the placement re-check uses the SAME 0.03 in
    # probability points, which is STRICTER than EV 3% at every price > 1.0 — conservative for real money.
    from workers.jobs import own_bots as _own
    from workers.automation.sharp_engine import SHARP_EDGE_CEILING as _CEIL
    out[_own.OWN_BOT] = BotRule(
        bot=_own.OWN_BOT, anchor="sharp", markets=("1x2",),
        edge_floor=0.03, odds_floor=None, edge_ceiling=_CEIL,
        outlier_mult=float(OUTLIER_MULT), source="workers/jobs/own_bots.py",
    )
    out[ou35.BOT_NAME] = BotRule(
        bot=ou35.BOT_NAME, anchor="model", markets=("o/u",),
        edge_floor=ou35.EDGE_FLOOR, odds_floor=None,
        source="workers/jobs/ou35_model_shadow.py",
    )
    return out


def edge_pp(prob, odds) -> float | None:
    """THE edge unit: probability points, `p − 1/odds`. None when either input is unusable."""
    try:
        p, o = float(prob), float(odds)
    except (TypeError, ValueError):
        return None
    if not (0.0 < p < 1.0) or o <= 1.0:
        return None
    return p - 1.0 / o


@dataclass(frozen=True)
class Floor:
    edge_min: float
    edge_max: float | None
    odds_min: float
    odds_max: float | None
    market_floor_applied: bool
    own_edge: float = 0.0     # the bot's own (generator) floors — the outlier cap is built on these
    own_odds: float = 1.0


def placement_floor(bot: str, market: str | None, selection: str | None,
                    odds: float | None, rules: dict[str, BotRule] | None = None) -> Floor | None:
    """The effective real-money floor for one pick at one price, or None when the bot is unknown or
    does not trade this market (callers treat None as refuse)."""
    from workers.automation.coolbet_placer import _min_odds_for, min_edge_for_pick
    rule = (rules if rules is not None else bot_rules()).get(bot)
    fam = _canon(market)
    if rule is None or fam is None or fam not in rule.markets:
        return None
    reg_edge = float(min_edge_for_pick(market, selection, odds))
    reg_odds = float(_min_odds_for(market))
    own_edge = reg_edge if rule.edge_floor is None else float(rule.edge_floor)
    own_odds = reg_odds if rule.odds_floor is None else float(rule.odds_floor)
    stacked = bot not in MARKET_FLOOR_OPT_OUT
    return Floor(
        edge_min=max(own_edge, reg_edge) if stacked else own_edge,
        edge_max=None if rule.edge_ceiling is None else float(rule.edge_ceiling),
        odds_min=max(own_odds, reg_odds) if stacked else own_odds,
        odds_max=None if rule.odds_max is None else float(rule.odds_max),
        market_floor_applied=stacked,
        own_edge=own_edge, own_odds=own_odds,
    )


def pick_clears(bot: str, market: str | None, selection: str | None, odds, prob,
                rules: dict[str, BotRule] | None = None) -> tuple[bool, str]:
    """(ok, reason) for placing `bot`'s pick at `odds` given its probability `prob`. Meeting a floor
    passes it (`>=`). Every refusal carries the first failing condition, in gate order."""
    rules = rules if rules is not None else bot_rules()
    rule = rules.get(bot)
    if rule is None:
        return False, f"unknown bot {bot!r}: no placement rule (fail closed)"
    sel = (selection or "").strip().lower()
    if rule.selections is not None and sel not in rule.selections:
        return False, f"selection {sel!r} not in {bot}'s selections {list(rule.selections)}"
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return False, f"no usable price ({odds!r})"
    f = placement_floor(bot, market, selection, o, rules)
    if f is None:
        return False, f"{bot} does not trade market {market!r}"
    # A window whose floor meets or passes its ceiling is treated as EMPTY: a one-point window would
    # make a bot placeable on a knife-edge the rule was never designed for.
    if f.odds_max is not None and f.odds_min >= f.odds_max:
        return False, f"empty odds window: floor {f.odds_min:.2f} >= ceiling {f.odds_max:.2f}"
    if f.edge_max is not None and f.edge_min >= f.edge_max:
        return False, f"empty edge window: floor {f.edge_min:.4f} >= ceiling {f.edge_max:.4f}"
    e = edge_pp(prob, o)
    if e is None:
        return False, f"edge not computable (prob={prob!r}, odds={o})"
    if o < f.odds_min:
        return False, f"odds {o:.2f} < floor {f.odds_min:.2f}"
    if f.odds_max is not None and o > f.odds_max:
        return False, f"odds {o:.2f} > ceiling {f.odds_max:.2f}"
    rule_ = rules[bot]
    if rule_.outlier_mult is not None:
        p = float(prob)
        if p <= f.own_edge:
            return False, f"prob {p:.4f} at or below the bot's own floor {f.own_edge:.4f}: no outlier window"
        cap = max(1.0 / (p - f.own_edge), f.own_odds) * rule_.outlier_mult
        if o > cap:
            return False, f"odds {o:.2f} > outlier cap {cap:.2f} (stale or mis-mapped quote)"
    # Float-safe comparison: a pick exactly ON the floor passes (EDGE-FLOOR-ONE-PREDICATE).
    if round(e, 10) < round(f.edge_min, 10):
        return False, f"edge {e:.4f} < floor {f.edge_min:.4f}" + ("" if f.market_floor_applied else " (own floor)")
    if f.edge_max is not None and round(e, 10) > round(f.edge_max, 10):
        return False, f"edge {e:.4f} > ceiling {f.edge_max:.4f} (phantom-price guard)"
    return True, "clears"


def min_odds_to_clear(bot: str, market: str | None, selection: str | None, prob,
                      rules: dict[str, BotRule] | None = None) -> float | None:
    """The lowest price at which `pick_clears` passes for this probability, or None if none does.
    Scans the selection-aware floor at the odds floor (the only price-dependent step in
    `min_edge_for_pick` is odds >= the 1x2 odds floor, which the odds floor already implies)."""
    rules = rules if rules is not None else bot_rules()
    f0 = placement_floor(bot, market, selection, None, rules)
    if f0 is None:
        return None
    f = placement_floor(bot, market, selection, f0.odds_min, rules)
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return None
    if not (0.0 < p < 1.0) or p <= f.edge_min:
        return None
    m = max(f.odds_min, 1.0 / (p - f.edge_min))
    ok, _ = pick_clears(bot, market, selection, m, p, rules)
    if not ok:
        # Float noise can leave 1/(p - floor) a hair under the floor; one ulp-scale nudge.
        m2 = m * (1 + 1e-12)
        ok, _ = pick_clears(bot, market, selection, m2, p, rules)
        m = m2
    return m if ok else None
