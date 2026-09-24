"""OBSERVATORY METRICS ([[#121]] phase 3 pilot, 2026-09-24) — three daily metrics that tell
us, without anyone asking, when something about our data or our bots CHANGED.

    clv_coverage    per ledger: share of legs on matches of the last 7 days that got ANY CLV
                    (Pinnacle close, ≥5-book consensus, or thin consensus). A silent CLV
                    outage — like the 106,726-row defect that sat unmoved for four days —
                    shows up the next morning as a drop.
    price_fidelity  per book: median distance (probability points, max side) between the
                    book's last pre-kickoff 1X2 and Pinnacle's, on fixtures that kicked
                    off in the last 24 h, both quotes within 60 min of kickoff. A parser
                    break, a margin change or a matcher regression moves it.
    frozen_share    per book: share of fixtures where the book rewrote ONE price >= 6 times
                    in the final 12 h while Pinnacle moved > 8% (a dead feed).
    last_quote_lag_p90  per book: p90 minutes from its last pre-kickoff quote to kickoff.
    bot_clv_7d      per bot: mean clv_sharp of its legs scored in the last 7 days.

Each value is written to obs_metric_values (migration 400), then compared with the same
metric's last 28 days: robust z = (v − median) / (1.4826·MAD); |z| ≥ 3 with >= 7 baseline
days and n >= the metric's minimum → a `metric_shift` finding in data_quality_findings.
Levels never alert. No ROI metrics (they do not converge at our volumes, GOTCHAS §60).

    python3 -m workers.jobs.observatory_metrics --dry-run
"""
from __future__ import annotations

import json
import logging
from datetime import date
from statistics import median

log = logging.getLogger(__name__)
Z_ALERT = 3.0
MIN_BASELINE_DAYS = 7
MIN_N = {"clv_coverage": 50, "price_fidelity": 20, "bot_clv_7d": 30,
         "frozen_share": 20, "last_quote_lag_p90": 20}


def robust_z(value: float, baseline: list[float]) -> float | None:
    """Pure. None when the baseline is too short or flat."""
    if len(baseline) < MIN_BASELINE_DAYS:
        return None
    med = median(baseline)
    mad = median(abs(x - med) for x in baseline)
    if mad == 0:
        return None
    return (value - med) / (1.4826 * mad)


def _clv_coverage(q) -> list[tuple]:
    rows = q("""SELECT l.ledger, count(*) n,
                       count(*) FILTER (WHERE l.status = 'ok' OR l.cons_status = 'ok' OR l.cons_thin_status = 'ok_thin') covered,
                       count(*) FILTER (WHERE l.status = 'ok') pin, count(*) FILTER (WHERE l.cons_status = 'ok') cons
                  FROM leg_clv_sharp l JOIN matches m ON m.id = l.match_id
                 -- by MATCH date, not computed_at: the table was backfilled with all of
                 -- history on 2026-09-23, so computed_at said nothing about recent legs
                 WHERE m.date BETWEEN now() - interval '7 days' AND now()
                 GROUP BY l.ledger""") or []
    return [("clv_coverage", f"ledger={r['ledger']}", r["covered"] / r["n"] if r["n"] else None, r["n"],
             {"pinnacle": r["pin"], "consensus": r["cons"]}) for r in rows]


def _price_fidelity(q) -> list[tuple]:
    from workers.model.devig import devig
    rows = q("""WITH last AS (
                  SELECT DISTINCT ON (o.match_id, o.bookmaker, o.selection)
                         o.match_id, o.bookmaker, o.selection, o.odds::float odds
                    FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                   WHERE m.date BETWEEN now() - interval '24 hours' AND now()
                     AND o.market = '1x2' AND COALESCE(o.is_live, false) = false
                     AND o.timestamp BETWEEN m.date - interval '60 minutes' AND m.date
                   ORDER BY o.match_id, o.bookmaker, o.selection, o.timestamp DESC)
                SELECT match_id::text mid, bookmaker, selection, odds FROM last""") or []
    by: dict = {}
    for r in rows:
        by.setdefault(r["mid"], {}).setdefault(r["bookmaker"], {})[r["selection"]] = r["odds"]
    gaps: dict = {}
    for books in by.values():
        pin = books.get("Pinnacle")
        if not pin or len(pin) < 3:
            continue
        p = devig([pin["home"], pin["draw"], pin["away"]])
        for b, t in books.items():
            if b == "Pinnacle" or len(t) < 3:
                continue
            x = devig([t["home"], t["draw"], t["away"]])
            if p and x:
                gaps.setdefault(b, []).append(max(abs(a - c) for a, c in zip(x, p)))
    return [("price_fidelity", f"book={b}|market=1x2", median(g), len(g), None) for b, g in gaps.items() if g]


FROZEN_MIN_SNAPSHOTS = 6
FROZEN_PIN_MOVE = 0.08


def _frozen_quotes(q) -> list[tuple]:
    """#121 phase 1 (#13, 2026-09-24): per book, the share of fixtures (kicked off in the last
    24 h, Pinnacle's home price moved > 8% in the final 12 h) on which the book wrote >= 6
    snapshots of its 1X2 home price with ONE distinct value — a feed that keeps rewriting a
    dead price. DQ brainstorm baseline: SBO 13.5%, William Hill / Betfair ~1.3%."""
    rows = q("""WITH w AS (
                  SELECT o.match_id, o.bookmaker, o.odds::float odds, o.timestamp
                    FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                   WHERE m.date BETWEEN now() - interval '24 hours' AND now()
                     AND o.market = '1x2' AND o.selection = 'home' AND COALESCE(o.is_live, false) = false
                     AND o.timestamp BETWEEN m.date - interval '12 hours' AND m.date),
                pin AS (
                  SELECT match_id, max(odds) / NULLIF(min(odds), 0) - 1 AS move FROM w
                   WHERE bookmaker = 'Pinnacle' GROUP BY match_id),
                bk AS (
                  SELECT match_id, bookmaker, count(*) n, count(DISTINCT odds) d FROM w
                   WHERE bookmaker <> 'Pinnacle' GROUP BY 1, 2)
                SELECT bk.bookmaker, count(*) fixtures,
                       count(*) FILTER (WHERE bk.n >= %s AND bk.d = 1) frozen
                  FROM bk JOIN pin ON pin.match_id = bk.match_id
                 WHERE pin.move > %s
                 GROUP BY 1""", (FROZEN_MIN_SNAPSHOTS, FROZEN_PIN_MOVE)) or []
    return [("frozen_share", f"book={r['bookmaker']}|market=1x2", r["frozen"] / r["fixtures"], r["fixtures"],
             {"frozen": r["frozen"]}) for r in rows if r["fixtures"]]


def _last_quote_lag(q) -> list[tuple]:
    """#13: p90 minutes between a book's LAST pre-kickoff 1X2 quote and kickoff (fixtures of
    the last 24 h). A long lag means the book's 'closing' price is stale — measured Coolbet
    > 4.1 h on 10% of fixtures, which makes own-book CLV stale there."""
    rows = q("""SELECT o.bookmaker,
                       percentile_cont(0.9) WITHIN GROUP (ORDER BY lag) AS p90, count(*) n
                  FROM (SELECT o.bookmaker, o.match_id,
                               extract(epoch FROM (m.date - max(o.timestamp))) / 60 AS lag
                          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                         WHERE m.date BETWEEN now() - interval '24 hours' AND now()
                           AND o.market = '1x2' AND COALESCE(o.is_live, false) = false
                           AND o.timestamp <= m.date
                         GROUP BY o.bookmaker, o.match_id, m.date) o
                 GROUP BY 1""") or []
    return [("last_quote_lag_p90", f"book={r['bookmaker']}|market=1x2", float(r["p90"]), r["n"], None)
            for r in rows if r["n"]]


def _bot_clv(q) -> list[tuple]:
    rows = q("""SELECT COALESCE(b.name, s.bot_id::text) bot, avg(l.clv_sharp)::float v, count(*) n
                  FROM leg_clv_sharp l JOIN shadow_bets s ON s.id = l.leg_id
                  LEFT JOIN bots b ON b.id = s.bot_id
                 WHERE l.ledger = 'shadow_bets' AND l.status = 'ok' AND l.computed_at > now() - interval '7 days'
                   -- in-play bots bet DURING the match: a pre-match close is not their yardstick
                   -- (bot_inplay_slowstate_afctl_v1 read −50 per cent, an artefact, 2026-09-24)
                   AND COALESCE(b.name, '') NOT ILIKE '%%inplay%%'
                 GROUP BY 1""") or []
    return [("bot_clv_7d", f"bot={r['bot']}", r["v"], r["n"], None) for r in rows]


def run(*, dry_run: bool = False) -> dict:
    from workers.api_clients.db import execute_query, execute_write
    from workers.utils.board_guard import record_finding
    today = date.today()
    values = []
    for fn in (_clv_coverage, _price_fidelity, _frozen_quotes, _last_quote_lag, _bot_clv):
        try:
            values += fn(execute_query)
        except Exception as e:  # noqa: BLE001 — one metric must not stop the others
            log.warning("observatory metric %s failed: %s", fn.__name__, e)
    c = {"values": len(values), "shifts": 0}
    for key, scope, v, n, meta in values:
        if v is None:
            continue
        try:
            base = [float(r["value"]) for r in (execute_query(
                """SELECT value FROM obs_metric_values WHERE metric_key = %s AND scope = %s
                     AND ts >= %s::date - 28 AND ts < %s::date AND value IS NOT NULL""",
                (key, scope, today, today)) or [])]
        except Exception:  # noqa: BLE001 — table not migrated yet / first run
            base = []
        z = robust_z(float(v), base)
        if dry_run:
            log.info("%-15s %-40s %.4f n=%s z=%s", key, scope, v, n, None if z is None else round(z, 2))
            continue
        execute_write("""INSERT INTO obs_metric_values (metric_key, scope, ts, value, n, meta)
                         VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                         ON CONFLICT (metric_key, scope, ts) DO UPDATE SET value = EXCLUDED.value,
                           n = EXCLUDED.n, meta = EXCLUDED.meta""",
                      (key, scope, today, v, n, json.dumps(meta) if meta else None))
        if z is not None and abs(z) >= Z_ALERT and (n or 0) >= MIN_N.get(key, 0):
            c["shifts"] += 1
            record_finding("metric_shift", None, scope, {"metric": key, "value": v, "n": n, "z": round(z, 2),
                                                          "baseline_median": median(base), "days": len(base)})
    log.info("observatory-metrics: %s", c)
    return c


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    print(run(dry_run=ap.parse_args().dry_run))
