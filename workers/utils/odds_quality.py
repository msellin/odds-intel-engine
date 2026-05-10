"""
ODDS-QUALITY-CLEANUP — shared blacklist + sanity gate for OU markets.

Why this exists: three bookmaker sources ship clearly broken Over/Under data
(api-football synthetic = 100% invalid pairs; William Hill = 88% Under-favored
on OU 1.5, line-shifted; api-football-live = in-play odds leaking into
pre-match aggregation). 1X2 and BTTS from the same sources are clean and
must be preserved.

Used at every OU write path (fetch_odds, store_odds) and surfaced as a SQL
predicate at the read path (daily_pipeline_v2._load_today_from_db).
"""

# Sources whose OU rows must never reach odds_snapshots or any aggregator.
BLACKLISTED_OU_SOURCES: frozenset[str] = frozenset({
    "api-football",
    "api-football-live",
    "William Hill",
})

# Implied-sum floor for a valid OU (over, under) pair.
# Any market has overround ≥ 2% in practice — 1.02 catches every broken feed
# (avg sum on api-football OU 1.5 is 0.63) without ever rejecting a real one.
MIN_OU_IMPLIED_SUM: float = 1.02


def is_ou_market(market: str | None) -> bool:
    return bool(market) and market.startswith("over_under_")


def filter_garbage_ou_rows(rows: list[dict]) -> list[dict]:
    """
    Drop OU rows from blacklisted bookmakers and drop both sides of any (over, under)
    pair whose implied-sum < MIN_OU_IMPLIED_SUM (impossible market).
    Preserves 1X2 / BTTS / asian_handicap rows untouched.

    Rows are expected to have keys: bookmaker, market, selection, odds.
    """
    if not rows:
        return rows

    clean: list[dict] = []
    ou_pair_index: dict[tuple[str, str], dict[str, dict]] = {}

    for r in rows:
        market = r.get("market") or ""
        if not is_ou_market(market):
            clean.append(r)
            continue

        bookmaker = r.get("bookmaker") or ""
        if bookmaker in BLACKLISTED_OU_SOURCES:
            continue

        sel = (r.get("selection") or "").lower()
        if sel not in ("over", "under"):
            clean.append(r)
            continue

        bucket = ou_pair_index.setdefault((bookmaker, market), {})
        bucket[sel] = r

    for (_bm, _mkt), pair in ou_pair_index.items():
        over = pair.get("over")
        under = pair.get("under")
        if over and under:
            try:
                o, u = float(over["odds"]), float(under["odds"])
                if o > 1.0 and u > 1.0 and (1.0 / o + 1.0 / u) < MIN_OU_IMPLIED_SUM:
                    continue
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            clean.append(over)
            clean.append(under)
        elif over:
            clean.append(over)
        elif under:
            clean.append(under)

    return clean
