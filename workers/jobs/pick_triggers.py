"""
BOOK-AGNOSTIC-EDGE-ENGINE Stage A — compute per-fixture trigger windows.
See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.

For every UPCOMING fixture with a full Pinnacle line, write a book-INDEPENDENT
trigger window per (market, selection) into `pick_triggers`:

    min_odds = max( 1/(P_sharp − edge_floor), odds_floor )
    max_odds = min_odds × OUTLIER_MULT

`P_sharp` is the Shin-de-vigged Pinnacle price (stored in the `cal_prob` column).
Stage B (per book) matches each book's swept odds against this window.

Markets: 1x2 + O/U 2.5. Idempotent upsert on (match_id, market, selection, strategy).

ONE anchor since #162 W7.2 (2026-09-26): `sharp_*` (see _emit_sharp_anchor). The
MODEL anchor (`model_1x2` / `model_ou25`, fair value = our calibrated model
probability) was deleted: its four per-book bots were removed from
`pick_trigger_matcher.BOOK_MARKET_BOTS` in the OWN Phase 5 cull (2026-09-15, all
retired), so every model window written after that was read by nobody.
`_fit_calibrator('1x2')` stays — `pick_generator`'s prob_source='predictions' path
(bot_unified_gate_1x2_paper_v1, active) still uses it.

Never places, never touches money. Run:  python3 -m workers.jobs.pick_triggers
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Re-exported from `anchor_sanity`, which owns it so BOTH sharp engines can
# import it without `pick_generator` having to reference this module (smoke
# PICK-GENERATOR forbids that — it must derive windows, not read them).
from workers.automation.anchor_sanity import OUTLIER_MULT  # noqa: F401

# SHARP anchor: (strategy, market = pick_triggers.market AND odds-snapshot market,
#                placer floor-key, selections in de-vig order)
# Fair value = Shin-de-vigged Pinnacle price (workers.model.devig.devig). The
# model-anchored twins this was once compared against were deleted in #162 W7.2
# (retired bots, no reader).
_SHARP_ANCHOR_BOOK = "Pinnacle"
# Sentinel: not a model bundle. Kept verbatim after the O/U sharp strategies moved Shin -> power
# (#162 W7.6): research scripts key "sharp-anchored row" on this exact string (floor_grid_sweep
# _SHARP_MV); the method a pick used is carried by its bot's rule_version instead.
_SHARP_MODEL_VERSION = "pinnacle_shin_devig"

# TRIGGER-CALIBRATOR-REVISION (2026-09-11). The window's `model_version` records
# the PREDICTION bundle, which is not enough to tell two eras of trigger picks
# apart: the bundle never changed, but on 2026-09-11 the CALIBRATION applied on
# top of it did — from one curve pooled over home/draw/away (which
# under-estimated HOME by 10-15pp and made the bot fire only on longshots) to
# per-selection fits.
#
# So every pick before that date came from a materially different model, and
# pooling the two eras in an evaluation would average a known-biased sample with
# a corrected one and report neither. Stamping the calibrator revision into
# `model_version` makes them separable with a string match, which is what the
# 3-5 day CLV re-read after the fix depends on.
#
# BUMP THIS whenever _fit_calibrator's shape changes — not when the underlying
# prediction bundle changes, which model_version already carries.
_CALIBRATOR_REV = "selcal1"


def _stamp_cal(model_version: str | None) -> str:
    """Tag a prediction bundle with the calibrator revision that shaped it."""
    return f"{model_version or 'unknown'}+{_CALIBRATOR_REV}"
_SHARP_STRATEGIES = [
    ("sharp_1x2", "1x2", "1x2", ["home", "draw", "away"]),
    ("sharp_ou25", "over_under_25", "o/u", ["over", "under"]),
    # SHARP-TIGHT-INSTRUMENT-2026-09-15 — the one configuration two independent
    # research rounds agreed was worth OBSERVING, and neither thought was worth
    # a euro. See dev/active/own-sharp-tight-preregistration.md.
    #
    # It exists because the original sweep could not express it. That sweep
    # graded 70,200 cells of a constant EXPECTED-ROI floor (`P x odds - 1`),
    # while the live gate here is a constant PROBABILITY-DIFFERENCE floor
    # (`P - 1/odds`). Since `roi_edge = prob_edge x odds`, a constant
    # probability floor is a CURVE in odds — no cell of a constant-ROI grid can
    # express it, and adding grid dimensions cannot fix a missing functional
    # form. "None of 70,200 configurations clears the bar" meant none in that
    # grid (ANALYSIS_GOTCHAS §42).
    #
    # Swept properly it is the only family that survives: prob-edge >= 2 pct,
    # odds <= 2.50, pooled across our placeable books — n=225, ROI +17.07 pct,
    # CI [+4.18, +29.95], no losing fold, OOS +23.40 pct.
    #
    # AND IT IS ALMOST CERTAINLY LUCK. It is a twelve-day effect: +0.99 pct
    # (n=79) before 2026-09-02 against +25.76 pct (n=146) after, and Coolbet
    # alone on a constant pool over 37 days jumps the same way (+0.99 -> +40.02,
    # n=46). Not an alignment artefact — tight and loose quote gaps agree within
    # each era. Decisively, margin-corrected OWN-BOOK CLV on the same legs reads
    # -5.36 to -7.56 pct beside those +19-42 pct ROIs, and a random leg is
    # ~-7.2 pct: the selection buys ~1.9pp of closing-line value, real but
    # nowhere near the 7-8 pct vig it must clear. Per §8, believe the CLV.
    #
    # So this is an INSTRUMENT, not a strategy. Paper only, never placeable.
    # Promotion requires margin-corrected own-book CLV > 0 at n >= 300 — ROI may
    # never promote it, at any value. The pre-registration is binding.
    ("sharp_1x2_tight", "1x2", "1x2_tight", ["home", "draw", "away"]),
]

# Odds CEILING per strategy. `_window` otherwise derives max_odds from the
# outlier multiplier; a strategy listed here is additionally capped.
_SHARP_MAX_ODDS_BY_STRATEGY = {"sharp_1x2_tight": 2.50}

# SHARP floors — deliberately DIFFERENT from the model floors, on principle.
#   * EDGE 3%: a sharp edge (P_sharp − 1/book_odds) is measured against the
#     near-true de-vigged Pinnacle line, so 3% is a REAL 3% overlay. The model
#     floors (13%/8%) would demand a 13% overlay vs Pinnacle — nearly impossible
#     (max observed +6.6%) — so the sharp bots would never fire.
#   * ODDS 1.01 (effectively OFF), NOT the model twin's 2.80/1.80. These are
#     EXPERIMENTAL paper bots whose whole job is to OBSERVE where the sharp anchor
#     finds value — a pre-set odds floor pre-judges which bands are bad before we
#     have any settled data. So we capture the full sharp-edge distribution across
#     ALL odds (favourites and longshots alike) and set a data-driven odds floor
#     per band LATER, once picks settle. Zero risk (paper). The 1.01 only rejects
#     degenerate ≤1.0 prices. Owner decision 2026-09-09. See docs/SYSTEM_MAP.md and
#     docs/BETTING_GATE_DECISIONS.md (sharp-anchor note).
_SHARP_MIN_EDGE_BY_MARKET = {"1x2": 0.03, "o/u": 0.03, "1x2_tight": 0.02}
_SHARP_MIN_ODDS_BY_MARKET = {"1x2": 1.01, "o/u": 1.01, "1x2_tight": 1.01}
# EDGE CEILING per floor key (#162 W7.6, 2026-09-26) — the sharp engine's ONE ceiling, which the
# merged generator bots have carried since SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES (2026-09-20) and the
# per-book bots never had: a +10% overlay on a de-vigged Pinnacle line is a mis-mapped price. The
# TIGHT instrument is deliberately absent: its gate is pre-registered (p - 1/odds >= 2 pct, odds <=
# 2.50, dev/active/own-sharp-tight-preregistration.md) and a ceiling would change it.
from workers.automation.sharp_engine import SHARP_EDGE_CEILING as _CEIL  # noqa: E402
_SHARP_MAX_EDGE_BY_MARKET = {"1x2": _CEIL, "o/u": _CEIL}


def sharp_rule(strategy: str, book: str, bot_name: str):
    """The sharp engine's rule for one Stage-A sharp strategy priced at one book (#162 W7.6). The
    strategy tables above are the source; the book-quote cap is the matcher's
    FRESHNESS_MAX_AGE_MIN (a strategy without one is not runnable — unknown freshness is stale).
    None when the strategy is not a sharp strategy."""
    from workers.automation.sharp_engine import SharpRule, PICK_EACH_BOOK
    from workers.jobs.pick_trigger_matcher import FRESHNESS_MAX_AGE_MIN
    spec = next((x for x in _SHARP_STRATEGIES if x[0] == strategy), None)
    if spec is None or strategy not in FRESHNESS_MAX_AGE_MIN:
        return None
    from workers.automation.coolbet_placer import _min_edge_for, _min_odds_for
    _strategy, _market, key, sides = spec
    return SharpRule(
        bot_name=bot_name, books=(book,), selections=tuple(sides),
        # an undeclared key falls back to the placer's floor (as Stage A always did), never a guess
        edge_floor=float(_SHARP_MIN_EDGE_BY_MARKET.get(key, _min_edge_for(key))),
        edge_ceiling=_SHARP_MAX_EDGE_BY_MARKET.get(key),
        odds_floor=float(_SHARP_MIN_ODDS_BY_MARKET.get(key, _min_odds_for(key))),
        odds_ceiling=_SHARP_MAX_ODDS_BY_STRATEGY.get(strategy),
        book_max_age_min=float(FRESHNESS_MAX_AGE_MIN[strategy]),
        pick=PICK_EACH_BOOK,
    )


def _window(cal: float, edge_floor: float, odds_floor: float):
    """(min_odds, max_odds) for a calibrated prob, or None if no odds can clear
    the edge (cal ≤ edge_floor → you'd need infinite odds)."""
    if cal is None or cal <= edge_floor or cal >= 1.0:
        return None
    min_odds = max(1.0 / (cal - edge_floor), odds_floor)
    return min_odds, min_odds * OUTLIER_MULT


def _fit_calibrator(kind: str):
    """Isotonic P(outcome) from raw model prob, fit on settled matches.

    TRIGGER-CALIBRATOR-POOLED-BIAS (2026-09-11) — this was fit POOLED across
    home/draw/away and that is a systematic, one-directional error, not a
    granularity nicety. The model's reliability differs sharply by outcome, on
    113k settled rows each:

        market      n        actual hit rate   avg predicted
        1x2_away    113,424  0.3110            0.3019
        1x2_draw    113,434  0.2452            0.3554   <- over-predicted +11pp
        1x2_home    113,434  0.4438            0.3424   <- under-predicted -10pp

    One monotone function cannot say both "0.35 means 24.5%" (draw) and "0.34
    means 44.4%" (home), so a pooled fit splits the difference and is wrong for
    every selection. Measured against per-selection fits:

        raw    pooled   home(true)   error
        0.25   0.2784   0.3764       -9.8pp
        0.45   0.3669   0.5186      -15.2pp

    HOME — the one selection the real-money bot bets — was under-estimated by
    ~10-15pp everywhere. Since edge = cal_prob - 1/odds, that shifts the whole
    window: the emitter demands a far higher price before a home pick clears, so
    the bot fires only on longshots. That is exactly the recorded symptom in
    SYSTEM_MAP ("selects longshots - do not promote") and the likeliest cause of
    these bots' negative CLV. The same fit over-estimates draws, firing on draws
    that have no edge.

    It also explains the quantisation seen in pick_triggers (1,326 rows -> 125
    distinct cal_prob, one value shared by 216 fixtures): `pooled(0.25)` is
    0.2784, the exact shared value. NB granularity itself was NOT the problem —
    per-selection fits have the same ~33 steps. The bias was.

    Returns a callable taking (praw, selection) that dispatches to the
    per-selection fit. None if too little history or sklearn is missing.

    #162 W7.2 (2026-09-26): 1x2 ONLY. Its one caller is pick_generator's
    prob_source='predictions' path (bot_unified_gate_1x2_paper_v1). The O/U
    ('ou25' / 'ou35') branch was deleted with its only consumers — the model_ou25
    trigger strategy and bot_trigger_ou_model_v1 (retired 2026-09-14).
    """
    from workers.api_clients.db import execute_query
    if kind != "1x2":
        log.warning("pick_triggers: no calibrator for %r (1x2 only since #162 W7.2) — "
                    "returning None rather than guessing a calibration", kind)
        return None
    if kind == "1x2":
        rows = execute_query(
            """SELECT p.market AS mk,
                      p.model_probability::float AS praw,
                      (CASE WHEN replace(p.market,'1x2_','') = m.result::text THEN 1 ELSE 0 END) AS y
                 FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.market IN ('1x2_home','1x2_draw','1x2_away')
                  AND p.source = 'ensemble'   -- #162 W2.2: fit on the production model only
                  AND m.status='finished' AND m.result IS NOT NULL
                  AND p.model_probability IS NOT NULL"""
        )
        try:
            from sklearn.isotonic import IsotonicRegression
        except Exception as e:  # noqa: BLE001
            log.warning("pick_triggers: sklearn unavailable (%s) — cannot calibrate 1x2", e)
            return None
        fits: dict[str, object] = {}
        for sel in ("home", "draw", "away"):
            sub = [r for r in rows if r["mk"] == f"1x2_{sel}"]
            if len(sub) < 500:
                log.warning("pick_triggers: only %d settled rows for 1x2_%s — "
                            "refusing to calibrate it rather than falling back "
                            "to the pooled fit that caused the home bias",
                            len(sub), sel)
                return None
            fits[sel] = IsotonicRegression(out_of_bounds="clip").fit(
                [r["praw"] for r in sub], [r["y"] for r in sub])
        def _cal_1x2(praw: float, selection: str):
            f = fits.get((selection or "").strip().lower())
            return float(f.predict([praw])[0]) if f is not None else None
        return _cal_1x2


def _emit_sharp_anchor(counters: dict) -> None:
    """Write SHARP-anchor trigger windows: fair value = de-vigged Pinnacle.

    #162 W7.6 (2026-09-26): these windows are now a RECORD, not an input — the matcher's sharp bots
    are decided at match time by the sharp engine (workers/automation/sharp_engine.py) from the
    Pinnacle line as it stands then, not from a window frozen here at :05. The rows are kept for the
    research scripts that read them (floor sweeps, export_bot_config), and they are computed by the
    SAME engine functions (anchor_fair = devig.fair_prob behind the shared W8.3 age rule; window =
    max(1/(cal - edge_floor), odds_floor) .. x OUTLIER_MULT, odds-capped), so a window can never
    disagree with the engine. `cal_prob` holds P_sharp. Never raises (best-effort sibling)."""
    from datetime import datetime, timezone
    from workers.api_clients.db import execute_write
    from workers.automation import sharp_engine as se

    for strategy, market, floor_key, sides in _SHARP_STRATEGIES:
        rule = sharp_rule(strategy, _SHARP_ANCHOR_BOOK, strategy)
        if rule is None:
            continue
        edge_floor, odds_floor = rule.edge_floor, rule.odds_floor
        counters["strategies"] += 1
        lines, now_ts = se.load_lines((market,), ())
        for (mid, _mk), line in lines.items():
            probs = se.anchor_fair(rule, line["quotes"].get(_SHARP_ANCHOR_BOOK), tuple(sides),
                                   now_ts, line["ko"])
            if probs is None:
                # SHARP-ANCHOR-MAX-AGE (#162 W8.3) or an incomplete line — the ONE staleness rule
                counters["skipped_stale_anchor"] = counters.get("skipped_stale_anchor", 0) + 1
                continue
            kickoff = datetime.fromtimestamp(line["ko"], tz=timezone.utc)
            for sel, p_sharp in zip(sides, probs):
                win = se.window(p_sharp, rule)
                if win is None:
                    counters["skipped_no_edge"] += 1
                    continue
                min_odds, max_odds = win
                execute_write(
                    """INSERT INTO pick_triggers
                          (match_id, market, selection, strategy, cal_prob,
                           edge_floor, odds_floor, min_odds, max_odds, model_version, kickoff_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (match_id, market, selection, strategy)
                       DO UPDATE SET cal_prob=EXCLUDED.cal_prob,
                                     edge_floor=EXCLUDED.edge_floor,
                                     odds_floor=EXCLUDED.odds_floor,
                                     min_odds=EXCLUDED.min_odds,
                                     max_odds=EXCLUDED.max_odds,
                                     model_version=EXCLUDED.model_version,
                                     kickoff_at=EXCLUDED.kickoff_at,
                                     computed_at=NOW()""",
                    [mid, market, sel, strategy, p_sharp, edge_floor, odds_floor,
                     min_odds, max_odds, _stamp_cal(_SHARP_MODEL_VERSION), kickoff],
                )
                counters["written"] += 1


def compute_triggers() -> dict:
    """Write sharp-anchor trigger windows for all upcoming fixtures. Never raises."""
    counters = {"strategies": 0, "written": 0, "skipped_no_edge": 0, "cleaned": 0}
    try:
        from workers.api_clients.db import execute_write

        # housekeeping: drop windows for fixtures already kicked off > 1 day ago
        try:
            execute_write("DELETE FROM pick_triggers WHERE kickoff_at < NOW() - INTERVAL '1 day'")
        except Exception:
            pass

        # #162 W7.2: the MODEL-anchor loop (model_1x2 / model_ou25) that used to run
        # here is deleted — no matcher bot reads those strategies any more.

        # SHARP anchor (Pinnacle de-vig), best-effort
        try:
            _emit_sharp_anchor(counters)
        except Exception as e:  # noqa: BLE001
            log.warning("pick_triggers sharp-anchor raised (non-fatal): %s", e)

        log.info("pick_triggers: %s", counters)
    except Exception as e:  # noqa: BLE001
        log.warning("pick_triggers compute_triggers raised (non-fatal): %s", e)
    return counters


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(compute_triggers(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
