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
Never places, never touches money. Run:  python3 -m workers.jobs.pick_triggers
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

OUTLIER_MULT = 1.6  # a book price above min_odds×this is likely stale, not a gift

# (strategy, placer market, placer floor-key, odds-snapshot market, {selection: prediction market})
_STRATEGIES = [
    ("model_1x2", "1x2", "1x2",
     {"home": "1x2_home", "draw": "1x2_draw", "away": "1x2_away"}),
    ("model_ou25", "over_under_25", "o/u",
     {"over": "over25", "under": "under25"}),
]


def _window(cal: float, edge_floor: float, odds_floor: float):
    """(min_odds, max_odds) for a calibrated prob, or None if no odds can clear
    the edge (cal ≤ edge_floor → you'd need infinite odds)."""
    if cal is None or cal <= edge_floor or cal >= 1.0:
        return None
    min_odds = max(1.0 / (cal - edge_floor), odds_floor)
    return min_odds, min_odds * OUTLIER_MULT


def _fit_calibrator(kind: str):
    """Isotonic P(outcome) from raw model prob, fit on settled matches. kind is
    '1x2' (pooled over home/draw/away) or 'ou25' (over 2.5). Returns a callable
    or None if too little history / sklearn missing."""
    from workers.api_clients.db import execute_query
    if kind == "1x2":
        rows = execute_query(
            """SELECT p.model_probability::float AS praw,
                      (CASE WHEN replace(p.market,'1x2_','') = m.result::text THEN 1 ELSE 0 END) AS y
                 FROM predictions p JOIN matches m ON m.id = p.match_id
                WHERE p.market IN ('1x2_home','1x2_draw','1x2_away')
                  AND m.status='finished' AND m.result IS NOT NULL"""
        )
    else:  # ou25
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


def compute_triggers() -> dict:
    """Write trigger windows for all upcoming predicted fixtures. Never raises."""
    counters = {"strategies": 0, "written": 0, "skipped_no_edge": 0, "cleaned": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        from workers.automation.coolbet_placer import _min_edge_for, _min_odds_for

        # housekeeping: drop windows for fixtures already kicked off > 1 day ago
        try:
            execute_write("DELETE FROM pick_triggers WHERE kickoff_at < NOW() - INTERVAL '1 day'")
        except Exception:
            pass

        cal_1x2 = _fit_calibrator("1x2")
        cal_ou = _fit_calibrator("ou25")

        for strategy, market, floor_key, sel_preds in _STRATEGIES:
            edge_floor = float(_min_edge_for(floor_key))
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
                    cal = cal_1x2(r["praw"]) if cal_1x2 else None
                else:  # over/under 2.5 — calibrate 'over', derive 'under' as 1-over
                    if not cal_ou:
                        cal = None
                    else:
                        cal_over = cal_ou(r["praw"] if sel == "over" else (1.0 - r["praw"]))
                        cal = cal_over
                if cal is None:
                    continue
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
