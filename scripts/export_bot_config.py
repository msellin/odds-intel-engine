"""#139 UNIFIED-BOT-MODEL phase 1 — export every bot's config into `bot_config`.

WHY. A bot's configuration lived in seven places (the `BotConfig` lists in
`bot_configs`, `pick_trigger_matcher.BOOK_MARKET_BOTS` + `pick_triggers` floors,
`daily_pipeline_v2.BOTS_CONFIG`, the forward-test constants, stand-alone paper modules,
the in-play collector, the placer whitelist) and no page showed any of it
(docs/BOTS_AUDIT_2026_09_24.md: seven book sets for twenty bots, none visible). This
script reads the objects the running code actually uses — it IMPORTS them, it does not
re-type them — and writes one row per bot into `bot_config` (migration 410), which the
`bot_scoreboard` / `bot_capabilities` views and /admin/bots read.

Every gate is `{name, value, source}` where `source` is `file:line` (located at run time
by searching the file, so it cannot drift from the code) or the env var that sets it.

One row per bot in `bots` (active AND retired) plus the forward-test control
`control_junk_anchor`, which has no `bots` row. A bot the code no longer describes gets
`family = 'unknown'` and a note saying why — it is never silently missing.

Families and their admissible metric are the contract table in
docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md.

Usage:
    python3 scripts/export_bot_config.py --dry-run   # print rows as JSON, write nothing
    python3 scripts/export_bot_config.py             # upsert into bot_config
Scheduled daily 03:40 UTC as `export_bot_config` (workers/scheduler.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("export_bot_config")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── contract: families and their ONE admissible verdict metric ───────────────
# [[#159]] 2026-09-25 (owner): ONE CLV for every bot — the SHARP-ANCHOR close (fresh Shin-
# de-vigged Pinnacle close, else a >=5-book consensus close; bot_performance / bot_scoreboard
# clv_anchor_*). Was clv_pinnacle (legacy clv_pinnacle_devig: no close-age limit, priced at the
# recorded odds, circular before mid-July — ANALYSIS_GOTCHAS §83) for the model bots and clv_mc
# (the bet book's OWN close, margin-corrected) for the rest; clv_mc is negative by construction
# for rules that pick a soft book's mispriced line (§85), so it stays only as a labelled
# secondary. In-play keeps lift: no closing line exists in play (§14).
FAMILY_METRIC: dict[str, str | None] = {
    "model_sim": "clv_anchor",
    "model_shadow": "clv_anchor",
    "sharp_trigger": "clv_anchor",
    "sharp_generator": "clv_anchor",
    "inplay": "lift",                 # hit rate − de-vigged implied; no CLV, no min-odds
    "forward_test": "clv_anchor",     # pre-registered; stopping rule amended to sharp anchor (#156)
    "control": "clv_anchor",          # the noise floor the forward test is read against
    "unknown": None,
}
FAMILIES = tuple(FAMILY_METRIC)

CONTROL_NAME = "control_junk_anchor"


# ── source locator ───────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def _lines(rel: str) -> tuple[str, ...]:
    try:
        return tuple((ROOT / rel).read_text().splitlines())
    except OSError:
        return ()


def src(rel: str, pattern: str) -> str:
    """'rel:line' of the first line matching `pattern` (regex). A miss means the code
    moved under the exporter: it is logged loudly and returned WITHOUT a line number,
    which smoke UNIFIED-BOT-VIEWS-CONTRACT rejects — never a silent guess."""
    rx = re.compile(pattern)
    for i, line in enumerate(_lines(rel), 1):
        if rx.search(line):
            return f"{rel}:{i}"
    log.warning("export_bot_config: source pattern %r not found in %s", pattern, rel)
    return rel


def _src_value(rel: str, pattern: str) -> tuple[float | None, str]:
    """(value, 'rel:line') for a function-local numeric constant `NAME = 1.23` that
    cannot be imported. Logged when missing, like src()."""
    rx = re.compile(pattern + r"\s*=\s*([0-9.]+)")
    for i, line in enumerate(_lines(rel), 1):
        m = rx.search(line)
        if m:
            return float(m.group(1)), f"{rel}:{i}"
    log.warning("export_bot_config: constant %r not found in %s", pattern, rel)
    return None, rel


def gate(name: str, value, source: str) -> dict:
    if isinstance(value, (tuple, set, frozenset)):
        value = sorted(value) if isinstance(value, (set, frozenset)) else list(value)
    return {"name": name, "value": value, "source": source}


def _row(name: str, family: str, **kw) -> dict:
    row = {
        "bot_name": name, "family": family, "description": None, "ledger": None,
        "writer_job": None, "cadence": None, "markets": None, "prob_source": None,
        "edge_floor": None, "edge_floor_source": None, "odds_min": None, "odds_max": None,
        "gates": [], "books": None, "books_source": None, "anchor": None,
        "placeable": False, "published": False, "telegram": False,
        "admissible_metric": FAMILY_METRIC[family],
    }
    row.update(kw)
    return row


# ── resolvers, one per code path that produces bots (audit B3) ───────────────
F_PIPE = "workers/jobs/daily_pipeline_v2.py"
F_BOTCFG = "workers/automation/bot_configs.py"
F_PICKGEN = "workers/automation/pick_generator.py"
F_ROUTER = "workers/automation/best_price_router.py"
F_PLACER = "workers/automation/coolbet_placer.py"
F_GATE = "workers/automation/placement_gate.py"
F_TRIG = "workers/jobs/pick_triggers.py"
F_MATCH = "workers/jobs/pick_trigger_matcher.py"
F_FT = "scripts/publish_picks_forward_test.py"
F_OU35 = "workers/jobs/ou35_model_shadow.py"
F_INPLAY = "workers/jobs/inplay_collector.py"
F_SIGNAL = "workers/automation/coolbet_signaler.py"
F_REG = "workers/registry/bot_registry.py"
F_PFLOOR = "workers/automation/placement_floor.py"


def _placement_floor_gate(bot: str) -> dict | None:
    """{selection: {edge_min, odds_min, odds_max, edge_max}} of the real-money rule, or None when
    the bot has no placement rule (placement_floor refuses it)."""
    from workers.automation import placement_floor as pf
    rules = pf.bot_rules()
    rule = rules.get(bot)
    if rule is None:
        return None
    market = rule.markets[0]
    sels = rule.selections or (("home", "draw", "away") if market == "1x2" else ("over", "under"))
    out = {"market": market, "edge_unit": pf.EDGE_UNIT}
    for s in sels:
        f0 = pf.placement_floor(bot, market, s, None, rules)
        f = pf.placement_floor(bot, market, s, f0.odds_min, rules)
        out[s] = {"edge_min": round(f.edge_min, 4), "odds_min": f.odds_min,
                  "odds_max": f.odds_max, "edge_max": f.edge_max}
    return out


def _pipeline_rows(db: dict) -> dict[str, dict]:
    """(a) daily_pipeline_v2.BOTS_CONFIG — simulated_bets writers."""
    from workers.jobs import daily_pipeline_v2 as p
    non_offers = sorted(p._NON_OFFERS)   # AttributeError if renamed — never an empty list
    lag_g = gate("odds_max_lag_hours", p.ODDS_MAX_LAG_HOURS,
                 f"{src(F_PIPE, r'^ODDS_MAX_LAG_HOURS')} (env ODDS_MAX_LAG_HOURS)")
    age_g = gate("odds_max_age_hours", p.ODDS_MAX_AGE_HOURS,
                 f"{src(F_PIPE, r'^ODDS_MAX_AGE_HOURS')} (env ODDS_MAX_AGE_HOURS)")
    books_src = src(F_PIPE, r"^def is_publishable_book")
    out = {}
    for name, cfg in p.BOTS_CONFIG.items():
        line = src(F_PIPE, rf'^\s*"{re.escape(name)}":\s*\{{')
        def _fmt(th: dict) -> str | None:
            return "; ".join(
                f"T{tier} " + " ".join(f"{k} {v:g}" for k, v in sorted(d.items()))
                for tier, d in sorted(th.items())) or None
        floor = _fmt(cfg.get("edge_thresholds") or {})
        lo, hi = (cfg.get("odds_range") or (None, None))
        strategies = cfg.get("strategies") or []
        if strategies:
            # Per-profile bots keep their real floors / odds bands in `strategies`; the
            # top-level edge_thresholds is {} and odds_range the (1.0, 99.0) placeholder.
            floor = " | ".join(f"{s.get('alias', '?')}: {_fmt(s.get('edge_thresholds') or {})}"
                               for s in strategies)
            bands = [s["odds_range"] for s in strategies if s.get("odds_range")]
            if bands:
                lo, hi = min(b[0] for b in bands), max(b[1] for b in bands)
        gates = [gate("odds_range", [lo, hi], line), lag_g, age_g]
        if strategies:
            gates.append(gate("strategies", strategies, line))
        for key in ("min_prob", "selection_filter", "league_filter", "tier_filter",
                    "markets", "is_active",
                    # #155 2026-09-25: an EV bot's floor is p x odds - 1, not pp — the board
                    # must say "EV >= 5%", and prob_source says WHICH model priced it.
                    "edge_unit", "prob_source", "ou_prob_source", "require_pinnacle",
                    "one_per_match"):
            if key in cfg:
                gates.append(gate(key, cfg[key], line))
        gates.append(gate("books_excluded (publishable deny-list)", non_offers,
                          src(F_PIPE, r"^_NON_OFFERS")))
        b = db.get(name) or {}
        live = b.get("retired_at") is None and b.get("is_active", True)
        # [[#155]] ONE STATUS DECIDES DISTRIBUTION: TESTING / BETA / CALIBRATED (not VIP, not
        # retired) is sent to /picks AND the public channel; the same rule as bot_distribution.
        from workers.utils.bot_status import sends_public
        telegram = bool(live and sends_public(b.get("maturity_label"), b.get("retired_at"), b.get("vip")))
        if telegram:
            gates.append(gate("public_telegram_if_status_sends", True,
                              src(F_SIGNAL, r"group_sends_public")))
        out[name] = _row(
            name, "model_sim",
            description=cfg.get("description"),
            ledger="simulated_bets",
            writer_job="morning_pipeline + betting_refresh_interval (daily_pipeline_v2)",
            cadence="04:00 UTC + hourly :05/:35",
            markets=list(cfg.get("markets") or []),
            # #152: bots priced by the new models say so (used as is, no calibrate_prob)
            prob_source={"combined_ou": "combined O/U model ou_comb_v1 (ou_model_predictions; Pinnacle where priced)",
                         "combined_1x2": "combined 1X2 model r1x2_comb_v1 (rating_1x2_predictions)",
                         "rating_1x2": "1X2 rating model r1x2_d8plus_v1 (rating_1x2_predictions)",
                         }.get(cfg.get("ou_prob_source") or cfg.get("prob_source"),
                               "calibrated model probability (calibrated_prob)"),
            edge_floor=floor, edge_floor_source=line,
            odds_min=lo, odds_max=hi, gates=gates,
            books=["*"], books_source=f"publishable = every book except _NON_OFFERS ({books_src})",
            anchor="model",
            published=telegram,   # [[#155]] /picks and Telegram are the same status decision
            telegram=telegram,
        )
    return out


def _pipeline_shadow_pass_rows() -> dict[str, dict]:
    """(a') the pipeline's own shadow passes (daily_pipeline_v2 _run_*_shadow_pass /
    _run_coolbet_value_pass). Every bot here is RETIRED — the generation was stopped or the
    pass skips retired bots — but the config is still in code, so it is exported, not
    'unknown'."""
    from workers.jobs import daily_pipeline_v2 as p
    acc = sorted(p.ACCESSIBLE_BOOKMAKERS)
    acc_src = f"ACCESSIBLE_BOOKMAKERS ({src(F_PIPE, r'^ACCESSIBLE_BOOKMAKERS')})"
    ls_min, ls_src = p._LINESHOP_TRUE_EDGE_MIN, src(F_PIPE, r"^_LINESHOP_TRUE_EDGE_MIN")
    tiers_g = gate("league_tiers", p._LINESHOP_TIERS, src(F_PIPE, r"^_LINESHOP_TIERS"))
    common = dict(ledger="shadow_bets", cadence="pipeline shadow cohorts",
                  books=acc, books_source=acc_src)
    out: dict[str, dict] = {}
    for cfg in p._SWEEP_SHADOW_CONFIGS:
        line = src(F_PIPE, rf'"name":\s*"{re.escape(cfg["name"])}"')
        out[cfg["name"]] = _row(
            cfg["name"], "model_shadow", **common,
            description="Config-sweep shadow bot (CONFIG-SWEEP-2026-08-19): model edge on one (market, selection, tier, odds band).",
            writer_job="daily_pipeline_v2._run_sweep_shadow_pass", markets=[cfg["market"]],
            prob_source="ensemble model probability", anchor="model",
            edge_floor=f"{cfg['edge_min']:g}", edge_floor_source=line,
            odds_min=cfg["odds_min"], odds_max=cfg["odds_max"],
            gates=[gate(k, cfg[k], line) for k in
                   ("selection", "tier_filter", "edge_min", "odds_min", "odds_max", "min_prob", "require_pinnacle")])
    for cfg in p._PIN_OU_SHADOW_CONFIGS:
        line = src(F_PIPE, rf'"name":\s*"{re.escape(cfg["name"])}"')
        out[cfg["name"]] = _row(
            cfg["name"], "sharp_generator", **common,
            description="Pinnacle O/U line-shop shadow bot (retired, migration 313 — line-shop loses OOS).",
            writer_job="daily_pipeline_v2._run_pin_ou_shadow_pass (generation stopped)", markets=[cfg["market"]],
            prob_source="de-vigged Pinnacle", anchor="sharp",
            edge_floor=f"{ls_min:g}", edge_floor_source=ls_src,
            gates=[gate("edge_floor", ls_min, ls_src), tiers_g])
    for cfg in p._PIN_1X2_SHADOW_CONFIGS:
        line = src(F_PIPE, rf'"name":\s*"{re.escape(cfg["name"])}"')
        out[cfg["name"]] = _row(
            cfg["name"], "sharp_generator", **common,
            description="Pinnacle 1x2 line-shop shadow bot (retired, migration 313 — line-shop loses OOS).",
            writer_job="daily_pipeline_v2._run_pin_1x2_shadow_pass (generation stopped)", markets=["1x2"],
            prob_source="de-vigged Pinnacle", anchor="sharp",
            edge_floor=f"{cfg['edge_min']:g}", edge_floor_source=ls_src,
            gates=[gate("edge_floor", cfg["edge_min"], ls_src), gate("selection", cfg["selection"], line), tiers_g])
    # no-pin: the pass's gates are function-local constants, read from the source.
    thr, thr_src = _src_value(F_PIPE, r"^\s+_EDGE_THRESHOLD")
    omin, omin_src = _src_value(F_PIPE, r"^\s+_ODDS_MIN")
    omax, omax_src = _src_value(F_PIPE, r"^\s+_ODDS_MAX")
    for name, sel in (("bot_no_pin_shadow_v1", None), ("bot_no_pin_home_v1", "home")):
        g = [gate("edge_floor", thr, thr_src), gate("odds_min", omin, omin_src), gate("odds_max", omax, omax_src),
             gate("requires_no_pinnacle_price", True, src(F_PIPE, r"^def _run_no_pin_shadow_pass"))]
        if sel:
            g.append(gate("selection", sel, src(F_PIPE, r"home_bot_id = _active_bots")))
        out[name] = _row(
            name, "model_shadow", **common,
            description="No-Pinnacle shadow bot: model 1x2 picks on fixtures Pinnacle does not price (skips itself when retired).",
            writer_job="daily_pipeline_v2._run_no_pin_shadow_pass", markets=["1x2"],
            prob_source="ensemble model probability", anchor="model",
            edge_floor=f"{thr:g}" if thr is not None else None, edge_floor_source=thr_src,
            odds_min=omin, odds_max=omax, gates=g)
    cv = p._COOLBET_VALUE_BOT
    out[cv] = _row(
        cv, "sharp_generator", ledger="shadow_bets", cadence="pipeline shadow cohorts",
        description="Coolbet line-shop bot vs de-vigged Pinnacle (retired 2026-09-08, generation stopped).",
        writer_job="daily_pipeline_v2._run_coolbet_value_pass (generation stopped)",
        markets=list(p._COOLBET_MARKETS), prob_source="de-vigged Pinnacle", anchor="sharp",
        edge_floor=f"{ls_min:g}", edge_floor_source=ls_src,
        odds_min=p._COOLBET_ODDS_MIN, odds_max=p._COOLBET_ODDS_MAX,
        gates=[gate("edge_floor", ls_min, ls_src),
               gate("odds_range", [p._COOLBET_ODDS_MIN, p._COOLBET_ODDS_MAX], src(F_PIPE, r"^_COOLBET_ODDS_MIN")),
               tiers_g],
        books=["Coolbet"], books_source=src(F_PIPE, r"^_COOLBET_VALUE_BOT"))
    return out


def _generator_rows() -> dict[str, dict]:
    """(b) pick_generator BotConfig lists (bot_configs.CONFIGS + TRIGGER_CONFIGS)."""
    from workers.automation import bot_configs as bc
    from workers.automation import coolbet_placer as cp
    from workers.automation import best_price_router as br
    from workers.automation import pick_generator as pg
    from workers.automation.anchor_sanity import OUTLIER_MULT
    books_src = src(F_ROUTER, r"^PLACEABLE_BOOKS")
    mirror_jobs = {"bot_coolbet_1x2_model_v1": "coolbet_model_1x2_shadow",
                   "bot_coolbet_ou_model_v1": "coolbet_model_ou_shadow"}
    out = {}
    for c in bc.ALL_CONFIGS:
        line = src(F_BOTCFG, rf'bot_name="{re.escape(c.bot_name)}"')
        sharp = c.prob_source == "sharp_devig"
        fam = "sharp_generator" if sharp else "model_shadow"
        m0 = c.markets[0] if c.markets else None
        if c.edge_floor is not None:
            floor, floor_src = f"{c.edge_floor:g}", line
        elif cp._canon_market(m0) == "1x2":
            floor = (f"selection-aware: home at odds>={cp._min_odds_for('1x2'):g} -> "
                     f"{cp._MODEL_1X2_HOME_FLOOR:g}, else {cp._min_edge_for('1x2'):g}")
            floor_src = f"{src(F_PLACER, r'^def min_edge_for_pick')} (env COOLBET_MODEL_1X2_EDGE_FLOOR)"
        else:
            floor = f"{cp._min_edge_for(m0):g}"
            floor_src = src(F_PLACER, r"^_MIN_EDGE_BY_MARKET")
        odds_min = c.odds_floor if c.odds_floor is not None else cp._min_odds_for(m0)
        odds_src = line if c.odds_floor is not None else \
            f"{src(F_PLACER, r'^_MIN_ODDS_BY_MARKET')} (env COOLBET_MIN_ODDS)"
        gates = [
            gate("edge_floor", floor, floor_src),
            gate("odds_floor", odds_min, odds_src),
            gate("prob_source", c.prob_source, line),
            gate("shadow_cohort", c.shadow_cohort, line),
            gate("book_quote_max_age_min", br.ODDS_FRESH_MAX_MIN, src(F_ROUTER, r"^ODDS_FRESH_MAX_MIN")),
        ]
        if sharp:
            gates.append(gate("outlier_cap_max_odds_mult", OUTLIER_MULT,
                              src(F_PICKGEN, r"max_odds = max\(1\.0 / \(cal_prob - ef\)")))
        if c.prob_source not in ("predictions", "sharp_devig"):
            # mirrors pick_generator's own condition: the 1.25x own-book anchor check runs
            # for prob_source='pipeline' only (predictions bots are NOT checked).
            gates.append(gate("own_outlier_mult_vs_anchor", pg._OWN_OUTLIER_MULT,
                              src(F_PICKGEN, r'if cfg\.prob_source not in \("predictions", "sharp_devig"\)')))
        if c.selections is not None:
            gates.append(gate("selections", c.selections, line))
        if c.edge_ceiling is not None:
            gates.append(gate("edge_ceiling", c.edge_ceiling, src(F_BOTCFG, r"^_SHARP_EDGE_CEILING")))
        if c.prob_source == "pipeline":
            gates.append(gate("source_bots", c.source_bots, line))
        if c.lookahead_hours is not None:
            gates.append(gate("lookahead_hours", c.lookahead_hours, line))
        writer = "pick_generator.on_odds_written (every Coolbet / Unibet-Site sweep)"
        if c.bot_name in mirror_jobs:
            writer += f" + {mirror_jobs[c.bot_name]}"
        out[c.bot_name] = _row(
            c.bot_name, fam,
            description=c.notes or None, ledger="shadow_bets",
            writer_job=writer, cadence="per sweep" + (" + :10/:40" if c.bot_name in mirror_jobs else ""),
            markets=list(c.markets),
            prob_source={"sharp_devig": "Shin-de-vigged Pinnacle",
                         "pipeline": "simulated_bets.calibrated_prob of calibrated bots",
                         "predictions": "predictions + per-selection calibrator"}.get(c.prob_source, c.prob_source),
            edge_floor=floor, edge_floor_source=floor_src,
            odds_min=odds_min, odds_max=None, gates=gates,
            books=list(c.books), books_source=f"PLACEABLE_BOOKS ({books_src})",
            anchor="sharp" if sharp else "model",
        )
    return out


def _trigger_rows() -> dict[str, dict]:
    """(c) pick_triggers (Stage A) -> pick_trigger_matcher (Stage B) per-book bots."""
    from workers.jobs import pick_triggers as pt
    from workers.jobs import pick_trigger_matcher as pm
    from workers.automation.anchor_sanity import OUTLIER_MULT
    strat = {s: (mkt, key) for s, mkt, key, _sides in pt._SHARP_STRATEGIES}
    by_bot: dict[str, dict] = {}
    for (book, market, strategy), bot in pm.BOOK_MARKET_BOTS.items():
        d = by_bot.setdefault(bot, {"books": [], "markets": set(), "strategies": set()})
        d["books"].append(book)
        d["markets"].add(market)
        d["strategies"].add(strategy)
    out = {}
    for bot, d in by_bot.items():
        strategies = sorted(d["strategies"])
        s0 = strategies[0]
        key = strat.get(s0, (None, None))[1]
        floor = pt._SHARP_MIN_EDGE_BY_MARKET.get(key)
        omin = pt._SHARP_MIN_ODDS_BY_MARKET.get(key)
        omax = pt._SHARP_MAX_ODDS_BY_STRATEGY.get(s0)
        fresh = pm.FRESHNESS_MAX_AGE_MIN.get(s0)
        gates = [
            gate("edge_floor", floor, src(F_TRIG, r"^_SHARP_MIN_EDGE_BY_MARKET")),
            gate("odds_floor", omin, src(F_TRIG, r"^_SHARP_MIN_ODDS_BY_MARKET")),
            gate("outlier_cap_max_odds_mult", OUTLIER_MULT,
                 src("workers/automation/anchor_sanity.py", r"^OUTLIER_MULT")),
            gate("book_quote_max_age_min", fresh, src(F_MATCH, r"^FRESHNESS_MAX_AGE_MIN")),
            gate("strategy", strategies, src(F_MATCH, r"^BOOK_MARKET_BOTS")),
        ]
        if omax is not None:
            gates.append(gate("odds_ceiling", omax, src(F_TRIG, r"^_SHARP_MAX_ODDS_BY_STRATEGY")))
        out[bot] = _row(
            bot, "sharp_trigger",
            ledger="shadow_bets",
            writer_job="pick_triggers (Stage A) -> pick_trigger_matcher (Stage B)",
            cadence="Stage A hourly :05, Stage B :15/:45",
            markets=sorted(d["markets"]),
            prob_source=f"Shin-de-vigged {pt._SHARP_ANCHOR_BOOK}",
            edge_floor=f"{floor:g}" if floor is not None else None,
            edge_floor_source=src(F_TRIG, r"^_SHARP_MIN_EDGE_BY_MARKET"),
            odds_min=omin, odds_max=omax, gates=gates,
            books=sorted(set(d["books"])),
            books_source=f"per-book hardcode ({src(F_MATCH, r'^BOOK_MARKET_BOTS')})",
            anchor="sharp",
        )
    return out


def _paper_module_rows() -> dict[str, dict]:
    """(d) stand-alone paper modules."""
    out = {}
    from workers.jobs import ou35_model_shadow as o
    line = src(F_OU35, r"^EDGE_FLOOR")
    out[o.BOT_NAME] = _row(
        o.BOT_NAME, "model_shadow",
        description="Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic fit on raw over35 predictions).",
        ledger="shadow_bets", writer_job="ou35_model_shadow", cadence=":10/:40",
        markets=["over_under_35"], prob_source="own isotonic fit on raw over35 predictions",
        edge_floor=f"{o.EDGE_FLOOR:g}", edge_floor_source=f"{line} (env OU35_MODEL_EDGE_FLOOR)",
        gates=[gate("edge_floor", o.EDGE_FLOOR, f"{line} (env OU35_MODEL_EDGE_FLOOR)")],  # no odds floor in code
        books=["Coolbet"], books_source=f"SQL hardcode ({src(F_OU35, r'bookmaker = .Coolbet.')})",
        anchor="model",
    )
    for mod, fname, env, markets, mkt_pat in (
            ("corners_paper_bot", "workers/jobs/corners_paper_bot.py", "CORNERS_PAPER_EDGE_FLOOR",
             ["corners_ou_*"], r"corners_ou_\(\\d\+\)"),
            ("team_total_paper_bot", "workers/jobs/team_total_paper_bot.py", "TEAM_TOTAL_PAPER_EDGE_FLOOR",
             ["team_total_home_*", "team_total_away_*"], r"^_MARKET_RE"),
            ("first_half_1x2_paper_bot", "workers/jobs/first_half_1x2_paper_bot.py", "FH_1X2_PAPER_EDGE_FLOOR",
             ["1x2_1h"], r"o\.market = '1x2_1h'")):
        m = __import__(f"workers.jobs.{mod}", fromlist=["BOT_NAME"])
        floor = m.EDGE_FLOOR
        fl_src = f"{src(fname, r'^EDGE_FLOOR')} (env {env})"
        extra = [gate("markets", markets, src(fname, mkt_pat))]
        if hasattr(m, "VERIFY_MIN_EDGE"):
            extra.append(gate("live_price_verify_min_edge", m.VERIFY_MIN_EDGE, src(fname, r"^VERIFY_MIN_EDGE")))
        out[m.BOT_NAME] = _row(
            m.BOT_NAME, "sharp_generator",
            description=f"Stand-alone paper module {mod} ({getattr(m, 'RULE_VERSION', '?')}): best own-book price vs de-vigged Pinnacle.",
            ledger="shadow_bets", writer_job=f"{mod} (pick 08/12/16/20 UTC, settle hourly)",
            cadence="4x daily", prob_source="de-vigged Pinnacle",
            edge_floor=f"{floor:g}" if floor is not None else None, edge_floor_source=fl_src,
            gates=[gate("edge_floor", floor, fl_src),
                   gate("rule_version", getattr(m, "RULE_VERSION", None), src(fname, r"^RULE_VERSION")),
                   gate("retirement_check", "none — name-only _bot_id lookup (owner call 2026-09-18)",
                        src(fname, r"def _bot_id"))] + extra,
            markets=markets,
            books=list(m.PLACEMENT_BOOKS), books_source=src(fname, r"^PLACEMENT_BOOKS"),
            anchor="sharp",
        )
    # #149 O/U sharp-outlier bots (workers/jobs/ou_sharp_outlier.py) — simulated_bets, paper.
    from workers.jobs import ou_sharp_outlier as ou
    fo = "workers/jobs/ou_sharp_outlier.py"
    common = dict(ledger="simulated_bets", writer_job="ou_sharp_outlier", cadence=":14/:44",
                  markets=list(ou.LINES), prob_source="power-de-vigged Pinnacle (latest, <= 3 h old)",
                  edge_floor=f"EV {ou.EV_MIN:g}..{ou.EV_CAP:g}", edge_floor_source=src(fo, r"^EV_MIN, EV_CAP"),
                  odds_min=ou.ODDS_LO, odds_max=ou.ODDS_HI, books=["every publishable book"],
                  books_source=src(fo, r"is_publishable_book"), anchor="sharp")
    out[ou.BOT_EARLY] = _row(ou.BOT_EARLY, "sharp_generator",
        description="O/U EARLY (#149): soft-book O/U quote beats Pinnacle's fair price by EV 5-15%, >= 12 h before kickoff.",
        gates=[gate("ev_vs_pinnacle", f"{ou.EV_MIN:g}..{ou.EV_CAP:g}", src(fo, r"^EV_MIN, EV_CAP")),
               gate("early_hours", ou.EARLY_MIN_H, src(fo, r"^EARLY_MIN_H"))], **common)
    out[ou.BOT_2ANCHOR] = _row(ou.BOT_2ANCHOR, "sharp_generator",
        description="O/U TWO-ANCHOR (#149): as O/U EARLY without the 12 h rule; also beats the other books' consensus by >= 2% EV.",
        gates=[gate("ev_vs_pinnacle", f"{ou.EV_MIN:g}..{ou.EV_CAP:g}", src(fo, r"^EV_MIN, EV_CAP")),
               gate("ev_vs_consensus", ou.CONS_EV_MIN, src(fo, r"^CONS_EV_MIN"))], **common)
    return out


def _inplay_rows() -> dict[str, dict]:
    """(e) the in-play slow-state rig (VPS collector service, not the scheduler)."""
    from workers.jobs import inplay_collector as ic
    cap_src = src(F_INPLAY, r"^PRICE_CAP")
    common = dict(ledger="shadow_bets",
                  writer_job="oddsintel-inplay-collector.service (inplay_collector.py)",
                  cadence="continuous", markets=["over_under_25", "1x2"], anchor="none",
                  edge_floor=None, edge_floor_source="none — locked triggers",
                  odds_max=ic.PRICE_CAP)
    trig = gate("triggers", "T1 0-0 at 35-54' -> under 2.5; T2 two-goal lead at 70-89' -> leader",
                src(F_INPLAY, r"T1_00_under25"))
    return {
        ic.BOT_LIVE: _row(ic.BOT_LIVE, "inplay", **common,
                          description="In-play slow-state rig, LIVE arm (Epicbet on-screen price).",
                          prob_source="book's own de-vigged probability",
                          gates=[trig, gate("price_cap", ic.PRICE_CAP, cap_src)],
                          books=[ic.BOOK], books_source=src(F_INPLAY, r"^BOOK =")),
        ic.BOT_CONTROL: _row(ic.BOT_CONTROL, "inplay", **common,
                             description="In-play slow-state rig, CONTROL arm (API-Football live aggregate).",
                             prob_source="API-Football live aggregate",
                             gates=[trig, gate("price_cap", ic.PRICE_CAP, cap_src)],
                             books=["api-football-live"], books_source=src(F_INPLAY, r"^BOT_CONTROL")),
    }


def _forward_test_rows(db: dict) -> dict[str, dict]:
    """(f) the pre-registered forward test — picks_forward_test, split into bots by the
    same arm/market/grade mapping picks_public_all uses (migration 402)."""
    import scripts.publish_picks_forward_test as ft
    ex = list(ft.EXCLUDED_BOOKS)
    ex_src = src(F_FT, r"^EXCLUDED_BOOKS")
    live_gates = [
        gate("rule_version", ft.RULE_VERSION, src(F_FT, r"^RULE_VERSION")),
        gate("min_edge_multiplicative", ft.MIN_EDGE, src(F_FT, r"^MIN_EDGE")),
        gate("max_odds", ft.MAX_ODDS, src(F_FT, r"^MAX_ODDS")),
        gate("anchor_bet_quote_align_min", ft.ALIGN_MIN, src(F_FT, r"^ALIGN_MIN")),
        gate("max_price_ratio_vs_anchor", ft.MAX_RATIO, src(F_FT, r"^MAX_RATIO")),
        gate("max_anchor_overround", ft.MAX_ANCHOR_OVERROUND, src(F_FT, r"^MAX_ANCHOR_OVERROUND")),
        gate("lookahead_h", ft.LOOKAHEAD_H, src(F_FT, r"^LOOKAHEAD_H")),
        gate("min_lead_min", ft.MIN_LEAD_MIN, src(F_FT, r"^MIN_LEAD_MIN")),
        gate("books_excluded", ex, ex_src),
    ]
    cons_gates = [
        gate("rule_version", ft.CONSENSUS_RULE_VERSION, src(F_FT, r"^CONSENSUS_RULE_VERSION")),
        gate("min_edge_multiplicative", ft.MIN_EDGE, src(F_FT, r"^MIN_EDGE")),
        gate("devig_methods_all_must_clear", ft.CREDIBLE_DEVIG_METHODS, src(F_FT, r"^CREDIBLE_DEVIG_METHODS")),
        gate("consensus_min_books", ft.CONSENSUS_MIN_BOOKS, src(F_FT, r"^CONSENSUS_MIN_BOOKS")),
        gate("consensus_max_edge", ft.CONSENSUS_MAX_EDGE, src(F_FT, r"^CONSENSUS_MAX_EDGE")),
        gate("weak_max_edge (grade D above)", ft.WEAK_MAX_EDGE, src(F_FT, r"^WEAK_MAX_EDGE")),
        gate("max_odds", ft.MAX_ODDS, src(F_FT, r"^MAX_ODDS")),
        gate("books_excluded", ex, ex_src),
    ]
    common = dict(ledger="picks_forward_test", writer_job="publish_picks_forward_test",
                  cadence=":05/:35", books=["*"],
                  books_source=f"every book except EXCLUDED_BOOKS ({ex_src})",
                  edge_floor=f"{ft.MIN_EDGE:g} (multiplicative P x odds - 1)",
                  edge_floor_source=src(F_FT, r"^MIN_EDGE"))
    live_arm = "live" in ft.PUBLISHED_ARMS
    cons_arm = ft.CONSENSUS_ARM in ft.PUBLISHED_ARMS

    def _pub(name: str, arm_published: bool) -> bool:
        # [[#155]] a published arm's bot is sent when its STATUS sends (bot_distribution).
        from workers.utils.bot_status import sends_public
        b = db.get(name) or {}
        return bool(arm_published and sends_public(b.get("maturity_label"), b.get("retired_at"), b.get("vip")))

    rows = {
        "bot_sharp_1x2_v1": _row("bot_sharp_1x2_v1", "forward_test", **common,
                                 description="Forward test, arm='live', market 1x2 (sharp anchor).",
                                 markets=["1x2"], prob_source="Shin-de-vigged Pinnacle",
                                 odds_max=ft.MAX_ODDS, gates=live_gates, anchor="sharp",
                                 published=_pub("bot_sharp_1x2_v1", live_arm),
                                 telegram=_pub("bot_sharp_1x2_v1", live_arm)),
        "bot_sharp_ou_v1": _row("bot_sharp_ou_v1", "forward_test", **common,
                                description="Forward test, arm='live', market over_under_25 (sharp anchor).",
                                markets=["over_under_25"], prob_source="Shin-de-vigged Pinnacle",
                                odds_max=ft.MAX_ODDS, gates=live_gates, anchor="sharp",
                                published=_pub("bot_sharp_ou_v1", live_arm),
                                telegram=_pub("bot_sharp_ou_v1", live_arm)),
    }
    for g, name in (("B", "bot_consensus_b_v1"), ("C", "bot_consensus_c_v1"), ("D", "bot_consensus_d_v1")):
        # [[#155]] sent iff the grade's bot's STATUS sends — grade D's bot is EXPERIMENTAL
        # (recorded, never sent; the scheduler skips the send, picks_public_all hides unsent rows).
        from workers.utils.bot_status import sends_public as _sends
        _b = db.get(name) or {}
        sent = _sends(_b.get("maturity_label"), _b.get("retired_at"), _b.get("vip"))
        extra = [gate("grade", g, src(F_FT, r"^GRADE_PANEL"))]
        omin = omax = None
        if g == "B":
            omin, omax = ft.STRONG_ODDS_MIN, ft.STRONG_ODDS_MAX
            extra.append(gate("odds_band", [omin, omax], src(F_FT, r"^STRONG_ODDS_MIN")))
        if not sent:
            extra.append(gate("send", "recorded, not sent", src("workers/scheduler.py", r"arm_bot_sends\(c, CONSENSUS_ARM")))
        rows[name] = _row(name, "forward_test", **common,
                          description=f"Forward test, arm='consensus_anchor', grade {g}.",
                          markets=list(ft.MARKETS), prob_source="de-vigged multi-book consensus",
                          odds_min=omin, odds_max=omax if omax is not None else ft.MAX_ODDS,
                          gates=cons_gates + extra, anchor="consensus",
                          published=_pub(name, cons_arm and sent),
                          telegram=_pub(name, cons_arm and sent))
    # [[#161]] twin arms — each is its parent's gates PLUS one, recorded, never published.
    def _swap_rv(gates, rv, rv_const):
        return ([g for g in gates if g["name"] != "rule_version"]
                + [gate("rule_version", rv, src(F_FT, rf"^{rv_const}"))])
    never_sent = gate("send", "recorded, never sent (not in PUBLISHED_ARMS)", src(F_FT, r"^TWIN_ARMS"))
    rows["bot_sharp_aligned_v1"] = _row(
        "bot_sharp_aligned_v1", "forward_test", **common,
        description=f"Twin of the sharp arm (arm='{ft.ALIGNED_ARM}'): live v4 + own-book quote "
                    f"within {ft.OWN_BOOK_ALIGN_MIN:g} min of the anchor. Recorded, never published.",
        markets=list(ft.MARKETS), prob_source="Shin-de-vigged Pinnacle", odds_max=ft.MAX_ODDS,
        gates=_swap_rv(live_gates, ft.ALIGNED_RULE_VERSION, "ALIGNED_RULE_VERSION") + [
            gate("own_direct_books", ft.OWN_DIRECT_BOOKS, src(F_FT, r"^OWN_DIRECT_BOOKS")),
            gate("own_book_quote_anchor_max_gap_min", ft.OWN_BOOK_ALIGN_MIN,
                 src(F_FT, r"^OWN_BOOK_ALIGN_MIN")), never_sent],
        anchor="sharp", published=False, telegram=False)
    rows["bot_consensus_pinconf_v1"] = _row(
        "bot_consensus_pinconf_v1", "forward_test", **common,
        description=f"Twin of the consensus arm (arm='{ft.PINCONF_ARM}'): consensus v2 + EV >= "
                    f"{ft.PINCONF_MIN_EV:g} vs a fresh tight Pinnacle where one exists. "
                    f"Recorded, never published.",
        markets=list(ft.MARKETS), prob_source="de-vigged multi-book consensus (+ Pinnacle confirm)",
        odds_max=ft.MAX_ODDS,
        gates=_swap_rv(cons_gates, ft.PINCONF_RULE_VERSION, "PINCONF_RULE_VERSION") + [
            gate("min_ev_vs_pinnacle_tight", ft.PINCONF_MIN_EV, src(F_FT, r"^PINCONF_MIN_EV")),
            never_sent],
        anchor="consensus", published=False, telegram=False)
    rows[CONTROL_NAME] = _row(
        CONTROL_NAME, "control", ledger="picks_forward_test (arm='junk_anchor')",
        description="Negative control: shuffled anchor over the same unfiltered pool; never published.",
        writer_job="publish_picks_forward_test (junk_anchor_arm)", cadence=":05/:35",
        markets=list(ft.MARKETS), prob_source="shuffled (junk) anchor",
        edge_floor=f"{ft.MIN_EDGE:g}", edge_floor_source=src(F_FT, r"^MIN_EDGE"),
        odds_max=ft.MAX_ODDS, gates=[gate("arm", "junk_anchor", src(F_FT, r"^def junk_anchor_arm"))],
        books=["*"], books_source=f"every book except EXCLUDED_BOOKS ({ex_src})", anchor="junk",
    )
    return rows


# Retired bots whose config was deleted from code but whose lineage the code still records
# (comments / successors). Family only — everything else stays NULL, with a note.
_LINEAGE = {
    "bot_v10_all": ("model_sim", "split into bot_v10_1x2 + bot_v10_ou (migration 375)"),
    "bot_sharp_forward_test_v1": ("forward_test", "split by market into bot_sharp_1x2_v1 + bot_sharp_ou_v1 (migration 402); its rows are owned by the halves"),
    "bot_consensus_anchor_v1": ("forward_test", "split by grade into bot_consensus_{b,c,d}_v1 (migration 380); its rows are owned by the grades"),
    "bot_coolbet_trigger_1x2_v1": ("model_shadow", "model-anchored per-book trigger, removed from BOOK_MARKET_BOTS (OWN Phase 5 cull)"),
    "bot_coolbet_trigger_ou_v1": ("model_shadow", "model-anchored per-book trigger, removed from BOOK_MARKET_BOTS (OWN Phase 5 cull)"),
    "bot_unibet_trigger_1x2_v1": ("model_shadow", "model-anchored per-book trigger, removed from BOOK_MARKET_BOTS (OWN Phase 5 cull)"),
    "bot_unibet_trigger_ou_v1": ("model_shadow", "model-anchored per-book trigger, removed from BOOK_MARKET_BOTS (OWN Phase 5 cull)"),
}


def build_rows(db_bots: list[dict] | None = None) -> list[dict]:
    """One row per bot. `db_bots` = rows of `bots` (name, is_active, retired_at,
    maturity_label, show_on_picks, description); when None, the active set falls back to
    bot_registry.active_names() so the export is testable without a DB."""
    from workers.automation.placement_gate import placement_path_reason
    if db_bots is None:
        from workers.registry.bot_registry import active_names
        db_bots = [{"name": n, "is_active": True, "retired_at": None} for n in sorted(active_names())]
    db = {b["name"]: b for b in db_bots}

    resolved: dict[str, dict] = {}
    # later resolvers never overwrite earlier ones; order = most specific first
    for part in (_forward_test_rows(db), _trigger_rows(), _generator_rows(),
                 _paper_module_rows(), _inplay_rows(), _pipeline_rows(db),
                 _pipeline_shadow_pass_rows()):
        for k, v in part.items():
            resolved.setdefault(k, v)

    rows = []
    # + registry bots whose `bots` row is still in a pending migration (the smoke/migrate
    # race SYSTEM-MAP-REGISTRY-NOT-DRIFTED also forgives) — a new bot is exported from day 0.
    from workers.registry.bot_registry import active_names as _registry_active
    names = sorted(set(db) | {CONTROL_NAME} | _registry_active())
    for name in names:
        b = db.get(name) or {}
        if name in resolved:
            r = resolved[name]
        elif name.startswith("inplay_"):
            r = _row(name, "inplay", description="Retired in-play bot (InplayBot, simulated_bets with match minute); config no longer in code.")
        elif name in _LINEAGE:
            fam, why = _LINEAGE[name]
            r = _row(name, fam, description=f"Retired; config no longer in code — {why}.")
        else:
            r = _row(name, "unknown",
                     description="No resolvable config in running code (retired and deleted, or never code-defined). "
                                 + (f"DB description: {b.get('description')}" if b.get("description") else ""))
        r = dict(r)
        # #139 (owner decision 4, 2026-09-24): `placeable` = the bot HAS a placement path,
        # by the code rule placement_gate.placement_path_reason (shadow_bets, pre-match,
        # priced at a placer book, not a publish-only test) — no longer membership of a
        # hand-listed name set. Whether it may actually bet is coolbet_placer_bots.
        active = b.get("is_active", True) is not False and b.get("retired_at") is None
        r["placeable"] = active and placement_path_reason(r.get("family"), r.get("ledger"), r.get("books")) is None
        if r["placeable"]:
            r["gates"] = list(r["gates"]) + [gate("placement_path", True, src(F_GATE, r"^def placement_path_reason"))]
            # [[#162]] W4.3: the floor REAL money applies (placement_floor.pick_clears — the stricter of
            # this bot's own floor and the market floor), per selection, so the admin can show it.
            _pfl = _placement_floor_gate(name)
            if _pfl is not None:
                r["gates"].append(gate("placement_floor", _pfl, src(F_PFLOOR, r"^def pick_clears")))
        if b.get("retired_at") is not None:
            r["published"] = False
            r["telegram"] = False
        if not r.get("description") and b.get("description"):
            r["description"] = b["description"]
        rows.append(r)
    return rows


def _load_db_bots() -> list[dict]:
    from workers.api_clients.db import execute_query
    return execute_query(
        "SELECT name, is_active, retired_at, maturity_label, show_on_picks, vip, description FROM bots")


_COLS = ("bot_name", "family", "description", "ledger", "writer_job", "cadence", "markets",
         "prob_source", "edge_floor", "edge_floor_source", "odds_min", "odds_max", "gates",
         "books", "books_source", "anchor", "placeable", "published", "telegram",
         "admissible_metric")


def _write(cur, rows: list[dict]) -> None:
    from psycopg2.extras import Json
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS if c != "bot_name")
    sql = (f"INSERT INTO bot_config ({', '.join(_COLS)}, exported_at) "
           f"VALUES ({', '.join(['%s'] * len(_COLS))}, now()) "
           f"ON CONFLICT (bot_name) DO UPDATE SET {sets}, exported_at = now()")
    for r in rows:
        cur.execute(sql, [Json(r[c]) if c == "gates" else r[c] for c in _COLS])


def store(rows: list[dict]) -> int:
    """Upsert every row in one transaction. Rows for bots that vanished are kept (a
    retired bot's config stays readable); exported_at shows how fresh each row is."""
    from workers.api_clients.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            _write(cur, rows)
        conn.commit()
    return len(rows)


def run() -> dict:
    """Scheduler entry: export and upsert. Returns {'stored': n} for pipeline_runs."""
    rows = build_rows(_load_db_bots())
    return {"stored": store(rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print rows as JSON, write nothing")
    args = ap.parse_args()
    if args.dry_run:
        import contextlib
        try:
            with contextlib.redirect_stdout(sys.stderr):   # keep stdout pure JSON
                db_bots = _load_db_bots()
        except Exception as e:  # noqa: BLE001 — dry-run must work without a DB
            print(f"# DB unavailable ({e}); falling back to bot_registry.active_names()", file=sys.stderr)
            db_bots = None
        print(json.dumps(build_rows(db_bots), indent=2, default=str))
        return
    print(json.dumps(run()))


if __name__ == "__main__":
    main()
