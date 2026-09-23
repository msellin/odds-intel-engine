"""CANDIDATE FUNNEL writer ([[#082]], migration 384).

One shared writer for every candidate source, so the table has one shape and
one retention rule. Callers hand over plain dicts; this dedupes on the primary
key within the batch (the LAST decision wins — a candidate accepted and then
dropped by a later gate in the same run ends as the drop), upserts, and prunes
rows older than RETENTION_DAYS.

Never raises: losing a funnel batch must not cost a pick or a run.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

RETENTION_DAYS = 90
NEAR_FLOOR_PP = 0.05   # write a floor-rejection only if within 5pp of the floor
_COLS = ("source", "bot", "match_id", "market", "selection", "bookmaker", "odds",
         "fair_prob", "fair_source", "raw_prob", "threshold", "step", "quote_age_min")


def _clean(v):
    """Plain Python value for psycopg2. numpy floats (every model probability is
    one) are written by psycopg2 as the literal text `np.float64(0.11)`, which
    Postgres rejects — and one bad row aborts the whole batch. Found by review
    before the first live run; same trap `_sanitize_for_json` exists for.
    NaN / Inf become NULL."""
    if v is None or isinstance(v, (str, bool)):
        return v
    try:
        import math
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if (math.isnan(f) or math.isinf(f)) else f


def record(rows: list[dict]) -> int:
    """Upsert funnel rows. Returns rows written (0 on any failure)."""
    if not rows:
        return 0
    latest: dict = {}
    for r in rows:
        if r.get("odds") is None or r.get("match_id") is None:
            continue
        key = (r["source"], r["bot"], str(r["match_id"]), r["market"], r["selection"])
        latest[key] = r
    if not latest:
        return 0
    try:
        from psycopg2.extras import execute_values
        from workers.api_clients.db import get_conn
        _txt = {"source", "bot", "match_id", "market", "selection", "bookmaker",
                "fair_source", "step"}
        vals = [tuple((str(r[c]) if r.get(c) is not None else None) if c in _txt
                      else _clean(r.get(c)) for c in _COLS)
                for r in latest.values()]
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, f"""
                    INSERT INTO candidate_funnel (day, {', '.join(_COLS)})
                    SELECT (now() AT TIME ZONE 'UTC')::date, v.* FROM (VALUES %s)
                        AS v({', '.join(_COLS)})
                    ON CONFLICT (day, source, bot, match_id, market, selection) DO UPDATE SET
                        bookmaker = EXCLUDED.bookmaker, odds = EXCLUDED.odds,
                        fair_prob = EXCLUDED.fair_prob, fair_source = EXCLUDED.fair_source,
                        raw_prob = EXCLUDED.raw_prob, threshold = EXCLUDED.threshold,
                        step = EXCLUDED.step, quote_age_min = EXCLUDED.quote_age_min,
                        last_seen = now(), n_seen = candidate_funnel.n_seen + 1""",
                    vals, template="(%s::text,%s::text,%s::uuid,%s::text,%s::text,%s::text,"
                                   "%s::numeric,%s::numeric,%s::text,%s::numeric,%s::numeric,"
                                   "%s::text,%s::numeric)",
                    page_size=1000)
                cur.execute("DELETE FROM candidate_funnel WHERE day < (now() AT TIME ZONE 'UTC')::date - %s",
                            (RETENTION_DAYS,))
            conn.commit()
        return len(vals)
    except Exception as e:  # noqa: BLE001
        log.warning("candidate_funnel.record failed (non-fatal): %s", e)
        return 0
