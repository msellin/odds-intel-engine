"""
BOOK-AGNOSTIC-EDGE-ENGINE Stage A — compute per-fixture trigger windows.
See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.

For every UPCOMING fixture the model predicts, write a book-INDEPENDENT trigger
window per (market, selection) into `pick_triggers`:

    min_odds = max( 1/(cal_prob − edge_floor), odds_floor )
    max_odds = min_odds × OUTLIER_MULT

`cal_prob` is the CALIBRATED model probability (isotonic on settled history —
the raw predictions are over-confident). The edge/odds floors are sourced from
the placer (`coolbet_placer._min_edge_for` / `_min_odds_for`) so the trigger and
the placement gate can never drift apart. Stage B (per book) matches each book's
swept odds against this window.

Markets: 1x2 + O/U 2.5 (the only markets we bet today — this also keeps the sweep
scope tight). Idempotent upsert on (match_id, market, selection, strategy).

Two ANCHORS, written side by side (the `strategy` column distinguishes them, and
Stage B routes each to its own paper bot):
  * model_* — fair value = our calibrated model probability (cal_prob).
  * sharp_* — fair value = Shin-de-vigged Pinnacle price (P_sharp). Same window
    math, same floors; only the reference number differs. See _emit_sharp_anchor.

Never places, never touches money. Run:  python3 -m workers.jobs.pick_triggers
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

OUTLIER_MULT = 1.6  # a book price above min_odds×this is likely stale, not a gift

# MODEL anchor: (strategy, placer market, placer floor-key, {selection: prediction market})
# Fair value = OUR calibrated model probability.
_STRATEGIES = [
    ("model_1x2", "1x2", "1x2",
     {"home": "1x2_home", "draw": "1x2_draw", "away": "1x2_away"}),
    ("model_ou25", "over_under_25", "o/u",
     {"over": "over25", "under": "under25"}),
]

# SHARP anchor: (strategy, market = pick_triggers.market AND odds-snapshot market,
#                placer floor-key, selections in de-vig order)
# Fair value = Shin-de-vigged Pinnacle price (workers.model.devig.devig). Same
# window math and SAME floors as the model anchor, so model-vs-sharp is a clean
# same-gate comparison — only the anchor differs. Scope mirrors _STRATEGIES
# (1x2 + O/U 2.5) so each sharp bot has a model-anchored twin.
_SHARP_ANCHOR_BOOK = "Pinnacle"
_SHARP_MODEL_VERSION = "pinnacle_shin_devig"  # sentinel: not a model bundle
_SHARP_STRATEGIES = [
    ("sharp_1x2", "1x2", "1x2", ["home", "draw", "away"]),
    ("sharp_ou25", "over_under_25", "o/u", ["over", "under"]),
]

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
_SHARP_MIN_EDGE_BY_MARKET = {"1x2": 0.03, "o/u": 0.03}
_SHARP_MIN_ODDS_BY_MARKET = {"1x2": 1.01, "o/u": 1.01}


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

    Returns a callable. For '1x2' it takes (praw, selection) and dispatches to
    the per-selection fit; for 'ou25' it takes (praw) as before. None if too
    little history or sklearn is missing.
    """
    from workers.api_clients.db import execute_query
    if kind == "1x2":
        rows = execute_query(
            """SELECT p.market AS mk,
                      p.model_probability::float AS praw,
                      (CASE WHEN replace(p.market,'1x2_','') = m.result::text THEN 1 ELSE 0 END) AS y
                 FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.market IN ('1x2_home','1x2_draw','1x2_away')
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
    # ou25: over/under are complements of one event, so a single fit is sound
    # here — unlike 1x2, where home/draw/away are three DIFFERENT events whose
    # reliability diverges by ~20pp (see above).
    rows = execute_query(
            """SELECT po.model_probability::float AS praw,
                      ((m.score_home + m.score_away) > 2.5)::int AS y
                 FROM matches m
                 JOIN LATERAL (SELECT model_probability FROM predictions
                               WHERE match_id=m.id AND market='over25'
                               ORDER BY model_version DESC LIMIT 1) po ON true
                WHERE m.status='finished' AND m.score_home IS NOT NULL"""
        )
    xs = [r["praw"] for r in rows if r["praw"] is not None]
    ys = [r["y"] for r in rows if r["praw"] is not None]
    if len(xs) < 500:
        return None
    try:
        from sklearn.isotonic import IsotonicRegression
    except Exception as e:  # noqa: BLE001
        log.warning("pick_triggers: sklearn unavailable (%s) — cannot calibrate %s", e, kind)
        return None
    iso = IsotonicRegression(out_of_bounds="clip").fit(xs, ys)
    return lambda p: float(iso.predict([p])[0])


def _emit_sharp_anchor(counters: dict) -> None:
    """Write SHARP-anchor trigger windows: fair value = Shin-de-vigged Pinnacle.

    For each upcoming fixture with a full Pinnacle line on the market, de-vig it
    to P_sharp per selection and write a window with the SAME edge/odds floors as
    the model anchor. `cal_prob` holds P_sharp so Stage B computes
    edge = P_sharp − 1/book_odds unchanged. Never raises (best-effort sibling)."""
    from workers.api_clients.db import execute_query, execute_write
    from workers.automation.coolbet_placer import (
            _min_edge_for, _min_odds_for, min_edge_for_pick,
        )
    from workers.model.devig import devig

    for strategy, market, floor_key, sides in _SHARP_STRATEGIES:
        # SHARP floors: small edge vs a near-true line + a light sanity odds floor
        edge_floor = float(_SHARP_MIN_EDGE_BY_MARKET.get(floor_key, _min_edge_for(floor_key)))
        odds_floor = float(_SHARP_MIN_ODDS_BY_MARKET.get(floor_key, _min_odds_for(floor_key)))
        counters["strategies"] += 1
        # latest pre-match Pinnacle price per (match, selection) for this market
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.selection)
                   o.match_id::text AS mid, o.selection, o.odds::float AS odds,
                   m.date AS kickoff
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.bookmaker = %s AND o.market = %s
               AND o.timestamp <= m.date AND m.date > NOW() AND m.status = 'scheduled'
             ORDER BY o.match_id, o.selection, o.timestamp DESC
            """,
            [_SHARP_ANCHOR_BOOK, market],
        )
        # group into full markets: {mid: {selection: odds}} + kickoff
        by_match: dict[str, dict] = {}
        for r in rows:
            m = by_match.setdefault(r["mid"], {"odds": {}, "kickoff": r["kickoff"]})
            m["odds"][r["selection"]] = r["odds"]
        for mid, m in by_match.items():
            quotes = [m["odds"].get(s) for s in sides]
            if any(q is None or q <= 1.0 for q in quotes):
                continue  # need the complete line to de-vig honestly
            probs = devig(quotes)  # Shin, order matches `sides`
            if probs is None:
                continue
            for sel, p_sharp in zip(sides, probs):
                win = _window(p_sharp, edge_floor, odds_floor)
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
                     min_odds, max_odds, _SHARP_MODEL_VERSION, m["kickoff"]],
                )
                counters["written"] += 1


def compute_triggers() -> dict:
    """Write trigger windows for all upcoming predicted fixtures. Never raises."""
    counters = {"strategies": 0, "written": 0, "skipped_no_edge": 0, "cleaned": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        from workers.automation.coolbet_placer import (
            _min_edge_for, _min_odds_for, min_edge_for_pick,
        )

        # housekeeping: drop windows for fixtures already kicked off > 1 day ago
        try:
            execute_write("DELETE FROM pick_triggers WHERE kickoff_at < NOW() - INTERVAL '1 day'")
        except Exception:
            pass

        cal_1x2 = _fit_calibrator("1x2")
        cal_ou = _fit_calibrator("ou25")

        for strategy, market, floor_key, sel_preds in _STRATEGIES:
            odds_floor = float(_min_odds_for(floor_key))
            counters["strategies"] += 1

            # upcoming matches with predictions for this market's selections
            pred_markets = tuple(sel_preds.values())
            rows = execute_query(
                """
                SELECT DISTINCT ON (p.match_id, p.market)
                       p.match_id::text AS mid, m.date AS kickoff,
                       p.market AS pred_market, p.model_probability::float AS praw,
                       p.model_version AS mv
                  FROM predictions p JOIN matches m ON m.id = p.match_id
                 WHERE p.market = ANY(%s) AND m.date > NOW() AND m.status = 'scheduled'
                 ORDER BY p.match_id, p.market, p.model_version DESC
                """,
                [list(pred_markets)],
            )
            # invert {selection: pred_market} → {pred_market: selection}
            sel_by_pred = {v: k for k, v in sel_preds.items()}
            for r in rows:
                sel = sel_by_pred.get(r["pred_market"])
                if sel is None or r["praw"] is None:
                    continue
                # calibrate
                if strategy == "model_1x2":
                    # TRIGGER-CALIBRATOR-POOLED-BIAS (2026-09-11): the 1x2
                    # calibrator is now PER SELECTION, so it needs to know which
                    # one. Pooled, home was under-estimated ~10-15pp and the
                    # windows only ever fired on longshots.
                    cal = cal_1x2(r["praw"], sel) if cal_1x2 else None
                else:  # over/under 2.5 — calibrate 'over', derive 'under' as 1-over
                    if not cal_ou:
                        cal = None
                    else:
                        cal_over = cal_ou(r["praw"] if sel == "over" else (1.0 - r["praw"]))
                        cal = cal_over
                if cal is None:
                    continue
                # TRIGGER-FLOOR-SELECTION-AWARE (2026-09-11). This used the
                # market-only `_min_edge_for(floor_key)` — 13% for every 1x2
                # selection — while the real-money bot bets home-underdogs at
                # 10%. So the trigger demanded MORE edge than the thing it is
                # meant to shadow, and never emitted the 10-13% band at all.
                #
                # The odds argument is `odds_floor`, and that is exact rather
                # than an approximation: `_window` returns
                # max(1/(cal - floor), odds_floor), so EVERY window this job
                # emits already starts at or above the odds floor. For 1x2 that
                # floor is 2.80, so a HOME window lies entirely inside
                # home-underdog territory — which is precisely the condition
                # `min_edge_for_pick` tests. Draws and aways are unaffected and
                # keep the pooled 13%.
                #
                # Owner 2026-09-11: "keep trigger bots accumulating more picks at
                # lower floor (apply same 10% floor and odds)". These are PAPER
                # bots, so a wider net costs nothing and buys the sample we do
                # not have — their CLV is negative today and the question of
                # whether any slice works needs volume to answer.
                edge_floor = float(min_edge_for_pick(market, sel, odds_floor))
                win = _window(cal, edge_floor, odds_floor)
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
                    [r["mid"], market, sel, strategy, cal, edge_floor, odds_floor,
                     min_odds, max_odds, r["mv"], r["kickoff"]],
                )
                counters["written"] += 1

        # SHARP anchor (Pinnacle de-vig) — sibling strategies, best-effort
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
