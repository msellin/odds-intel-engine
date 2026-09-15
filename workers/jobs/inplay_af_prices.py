"""INPLAY-AF-PRICES (2026-09-15, OWN Phase 1b) — API-Football's live aggregate,
shaped for the in-play rig's CONTROL arm, plus the AF-fixture → matches.id map.

The control arm prices the same trigger at the same instant off AF's live odds
(median 40 s stale, INPLAY_BOOK_COMPARISON) so the live-minus-control gap
measures the value of the fresh board. AF's prices are never used for the live
arm. Parsing reuses `api_football.parse_live_odds` — the one place that knows
AF puts the O/U line in `handicap` rather than in `value` (the gotcha the
overnight fidelity probe hit).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def af_live_prices() -> dict[str, dict[str, dict[str, float]]]:
    """{af_fixture_id: {market: {selection: odds}}} for every live fixture AF
    quotes right now. Markets are '1x2' and 'over_under_<line>'. Never raises."""
    try:
        from workers.api_clients.api_football import get_live_odds, parse_live_odds
        parsed = parse_live_odds(get_live_odds() or [])
    except Exception as e:  # noqa: BLE001
        log.debug("af_live_prices failed: %s", e)
        return {}
    out: dict[str, dict[str, dict[str, float]]] = {}
    for fid, rows in parsed.items():
        mk = out.setdefault(str(fid), {})
        for r in rows:
            try:
                mk.setdefault(r["market"], {})[r["selection"]] = float(r["odds"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


_MATCH_CACHE: dict[str, tuple[str | None, float]] = {}
_CACHE_TTL_S = 600.0


def af_fixture_to_match_id(af_fixture_id: str | None) -> str | None:
    """matches.id for an API-Football fixture id, cached 10 min. None if unknown."""
    if not af_fixture_id:
        return None
    key = str(af_fixture_id)
    hit = _MATCH_CACHE.get(key)
    now = time.time()
    if hit and now - hit[1] < _CACHE_TTL_S:
        return hit[0]
    mid = None
    try:
        from workers.api_clients.db import execute_query
        r = execute_query("SELECT id FROM matches WHERE api_football_id = %s LIMIT 1", (int(key),))
        mid = str(r[0]["id"]) if r else None
    except Exception as e:  # noqa: BLE001
        log.debug("af_fixture_to_match_id(%s) failed: %s", key, e)
    _MATCH_CACHE[key] = (mid, now)
    return mid
