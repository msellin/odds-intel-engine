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

# Only store OU markets our bots and pipeline actually use. AF returns 27+
# variants (OU 0.75 → OU 8.5); the other 23 add ~17K rows/hour with zero
# downstream value and ~70% of daily odds_snapshots storage growth.
ALLOWED_OU_MARKETS: frozenset[str] = frozenset({
    "over_under_15",
    "over_under_25",
    "over_under_35",
    "over_under_45",
    # PINNACLE-1H-OU-COLLECTION-GAP ([[#104]], 2026-09-23). `is_ou_market` is a
    # PREFIX test, so `over_under_1h_*` counts as an O/U market — and this
    # allowlist (May 2026) predates first-half capture (2026-09-05). Every AF
    # first-half O/U row from every book was dropped here, silently, from the
    # day it was switched on; the only rows we ever held came from the one-off
    # 7-day backfill, which bypassed this filter. Only the two half-goal lines
    # we measure are allowed: 0.75/1.0/1.25 push or half-win and have no settler.
    "over_under_1h_05",
    "over_under_1h_15",
})

# Drop Asian Handicap lines beyond ±3.0. AF returns up to 50 lines per
# bookmaker per match (-6.5 → +7.5). We only bet competitive games where
# the handicap is within 3 goals; extreme lines add ~15K rows/hour with
# no bot ever selecting them.
MAX_AH_LINE: float = 3.0


def is_ou_market(market: str | None) -> bool:
    return bool(market) and market.startswith("over_under_")


def ou_line_from_label(market: str | None) -> float | None:
    """The goal line an over_under_* label encodes. Every writer builds the label as
    str(line) with the dot removed ("over_under_25" = 2.5, "over_under_225" = 2.25,
    "over_under_025" = 0.25, "over_under_1h_05" = 0.5): first digit, a point, the rest."""
    if not market or not market.startswith("over_under_"):
        return None
    digits = market.rsplit("_", 1)[1]
    if not digits.isdigit() or len(digits) < 2:
        return None
    return float(digits[0] + "." + digits[1:])


def ou_line_matches(market: str | None, line) -> bool:
    """False when an over_under_* row carries a handicap_line that is NOT its label's line.
    OU-LINE-LABEL (#121, 2026-09-24): 408 1xBet rows (2026-09-17..20) were stored as
    over_under_25 with handicap_line 0.25 — a quarter-line market under the 2.5 name
    (e.g. over 1.02 / under 10.9), which every O/U consumer read as the 2.5 line."""
    expected = ou_line_from_label(market)
    if expected is None or line is None:
        return True
    try:
        return abs(float(line) - expected) < 1e-9
    except (TypeError, ValueError):
        return False


def filter_garbage_ou_rows(rows: list[dict]) -> list[dict]:
    """
    Drop OU rows from blacklisted bookmakers, drop both sides of any (over, under)
    pair whose implied-sum < MIN_OU_IMPLIED_SUM, drop OU markets not in
    ALLOWED_OU_MARKETS, and drop AH lines beyond ±MAX_AH_LINE.
    Preserves 1X2 / BTTS / double_chance rows untouched.

    Rows are expected to have keys: bookmaker, market, selection, odds.
    """
    if not rows:
        return rows

    clean: list[dict] = []
    ou_pair_index: dict[tuple[str, str], dict[str, dict]] = {}

    for r in rows:
        market = r.get("market") or ""

        # Asian Handicap: pass through but drop extreme lines
        if market == "asian_handicap":
            line = r.get("handicap_line")
            try:
                if line is not None and abs(float(line)) > MAX_AH_LINE:
                    continue
            except (TypeError, ValueError):
                pass
            clean.append(r)
            continue

        if market.startswith("over_under_") and not ou_line_matches(market, r.get("handicap_line")):
            continue  # OU-LINE-LABEL: a different line filed under this market's name
        if not is_ou_market(market):
            clean.append(r)  # 1x2, btts, double_chance pass through unchanged
            continue

        # Drop OU markets we never use (AF returns 27+ variants)
        if market not in ALLOWED_OU_MARKETS:
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
