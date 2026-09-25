"""The price a pick could actually have been taken at — ONE producer for both bases ([[#159]]).

Two columns on `simulated_bets` and `shadow_bets`, both "latest stored quote PER BOOK at or
before pick_time, then MAX across books", differing only in WHICH books:

  odds_at_pick_live       OWN basis — ACCESSIBLE_BOOKMAKERS, the books the operator can stake
                          at from Estonia (Coolbet / Epicbet / Tonybet / Unibet-Site). Shown on
                          /admin/bots as "at our books". (STALE-ODDS-HISTORY-RESTATE, mig 291.)
  odds_at_pick_available  PUBLIC basis — every PUBLISHABLE book (daily_pipeline_v2.
                          is_publishable_book, the [[#005]] set). A reader is not bound by our
                          licensing, so /performance, the hero and dashboard_cache price here:
                          "the best price available when the pick was made (all books)".
                          Floored at odds_at_pick_live: that is a real quote at pick time from a
                          SUBSET of the same books, and odds_snapshots retention trims intraday
                          rows after ~7 days (ANALYSIS_GOTCHAS §59), so a re-computation today
                          can miss a quote the live pass saw.

WHY THIS AND NOT `odds_at_pick`. Before 2026-09-02 (STALE-BEST-ODDS) the pipeline recorded
MAX() over the fixture's whole snapshot history, so `odds_at_pick` on those rows is a
high-water mark nobody could take. The writers were fixed that day (latest quote per book);
these two columns give every row — old and new — a price that was on offer at pick time,
without rewriting `odds_at_pick` / `pnl` / `bankroll_after` (live staking inputs, #074).

WHEN IT RUNS. At pick time: `store_bet` calls `price_legs('simulated_bets', ids=[new_id])`
right after the insert. And every 30 min (`job_backfill_live_prices`) over settled rows and
anything picked in the last 2 days, so a shadow pick written by any other writer is priced
within half an hour — from quotes at or before its pick_time, i.e. the same answer.
Only NULL columns are touched, so a price, once recorded, is never recomputed.

STALENESS. A book's latest quote at or before pick_time only counts if it is not a dead feed:
within ODDS_MAX_LAG_HOURS of the freshest book on the same leg and ODDS_MAX_AGE_HOURS of
pick_time — the rule the pipeline itself prices with. (The pre-#159 live backfill had no such
guard; rows it priced keep their value — only NULL columns are ever filled.)

MARKET VOCABULARY. The bet tables and odds_snapshots spell O/U differently ('o/u' +
'under 2.5' vs 'over_under_25' + 'under'); the CTE normalises both (OU-LIVE-PRICE-BLIND,
ANALYSIS_GOTCHAS #3).
"""
from __future__ import annotations

TABLES = ("simulated_bets", "shadow_bets")

_LEGS = """
    SELECT t.id, t.match_id, t.pick_time,
           CASE
             WHEN LOWER(t.market) IN ('o/u', 'ou') AND t.selection ~ '[0-9]'
               THEN 'over_under_' || REPLACE(
                      REGEXP_REPLACE(t.selection, '^[^0-9]*', ''), '.', '')
             ELSE LOWER(t.market)
           END AS market,
           CASE
             WHEN LOWER(t.market) IN ('o/u', 'ou')
               OR LOWER(t.market) LIKE 'over_under%%'
               THEN LOWER(REGEXP_REPLACE(t.selection, '[[:space:]]*[0-9.]+[[:space:]]*$', ''))
             ELSE LOWER(t.selection)
           END AS selection
      FROM {table} t
     WHERE t.pick_time IS NOT NULL
       AND t.{column} IS NULL
       {prematch}
       {scope}
"""

# IN-PLAY legs are never priced here. "Latest quote at or before pick_time" for a pick made at
# minute 35 finds the PRE-MATCH board — a different market (ANALYSIS_GOTCHAS §14). The in-play
# writers record their own on-screen price at insert; a pre-#159 run of the live backfill had no
# such guard. (Found on the first #159 run: the in-play shadow bots read +86% "all books" ROI.)
_PREMATCH = {
    "simulated_bets": ("AND t.match_minute_at_pick IS NULL AND t.xg_source IS NULL "
                       "AND NOT EXISTS (SELECT 1 FROM bots bb WHERE bb.id = t.bot_id AND bb.name LIKE 'inplay\\_%%')"),
    "shadow_bets": "AND t.inplay_minute IS NULL",
}

_BEST = """
WITH b AS ({legs}),
q AS (
    SELECT DISTINCT ON (b.id, o.bookmaker) b.id, b.market, b.pick_time, o.bookmaker, o.odds, o.timestamp
      FROM b
      JOIN odds_snapshots o
        ON  o.match_id  = b.match_id
       AND  LOWER(o.market)    = b.market
       AND  LOWER(o.selection) = b.selection
       AND  o.is_closing = false
       AND  o.is_live IS NOT TRUE
       AND  o.odds > 1
       AND  {book_filter}
       AND  o.timestamp <= b.pick_time          -- the quote each book showed when the pick was made
     ORDER BY b.id, o.bookmaker, o.timestamp DESC
),
fresh AS (
    -- Same staleness rule the pipeline prices with (ODDS-NO-MAX-AGE, daily_pipeline_v2
    -- _load_today_from_db): a book whose latest quote trails the freshest book on the same
    -- leg by more than ODDS_MAX_LAG_HOURS is a dead feed, not a price on offer; the absolute
    -- ceiling is a backstop. Plus BLACKLISTED_OU_SOURCES on over/under lines.
    SELECT q.*,
           EXTRACT(epoch FROM (MAX(q.timestamp) OVER (PARTITION BY q.id) - q.timestamp)) / 3600.0 AS lag_h,
           EXTRACT(epoch FROM (q.pick_time - q.timestamp)) / 3600.0 AS age_h
      FROM q
),
best AS (
    SELECT id, MAX(odds) AS best FROM fresh
     WHERE lag_h <= %(max_lag_h)s AND age_h <= %(max_age_h)s
       AND NOT (market LIKE 'over_under%%' AND bookmaker = ANY(%(ou_blacklist)s))
     GROUP BY id
)
UPDATE {table} t
   SET {column} = {value}
  FROM best
 WHERE t.id = best.id
"""

# A leg with no publishable quote but an our-books quote (retention-trimmed history): the
# our-books price IS a publishable price available at pick time.
_FLOOR = """
UPDATE {table} t SET odds_at_pick_available = t.odds_at_pick_live
 WHERE t.odds_at_pick_available IS NULL AND t.odds_at_pick_live > 1 {prematch} {scope}
"""


PUBLIC_BASES = ("available", "our_books", "recorded", "inplay")


def public_price(row: dict) -> tuple[float, str]:
    """The PUBLIC price of one simulated_bets / shadow_bets leg and its basis — the exact
    rule of bot_ledger.odds_public / public_basis (migration 433): odds_at_pick_available,
    else odds_at_pick_live, else the recorded odds_at_pick ('recorded' = no quote stored at
    pick time; counted as n_public_recorded, flagged on the row as pnl_price_basis).
    Settlement computes simulated_bets.pnl at this price (FLAT-STAKES-EVERYWHERE, #155), so
    the stored P&L is the published one. Pure — no DB.
    [[#157]] An IN-PLAY leg (match_minute_at_pick / xg_source set) is priced at its recorded
    in-play odds, basis 'inplay' — a pre-match quote is a different market (§14); migration 446
    applies the same rule in bot_ledger."""
    if row.get("match_minute_at_pick") is not None or row.get("xg_source") is not None:
        return float(row["odds_at_pick"]), "inplay"
    for col, basis in (("odds_at_pick_available", "available"), ("odds_at_pick_live", "our_books")):
        v = row.get(col)
        if v is not None and float(v) > 1:
            return float(v), basis
    return float(row["odds_at_pick"]), "recorded"


def _scope(ids: list | None, settled_only: bool, recent_days: int | None) -> tuple[str, dict]:
    if ids is not None:
        return "AND t.id = ANY(%(ids)s::uuid[])", {"ids": [str(i) for i in ids]}
    if settled_only and recent_days:
        return ("AND (t.result IN ('won','lost') OR t.pick_time >= now() - make_interval(days => %(days)s))",
                {"days": int(recent_days)})
    if settled_only:
        return "AND t.result IN ('won','lost')", {}
    return "", {}


def price_legs(table: str, ids: list | None = None, settled_only: bool = True,
               recent_days: int | None = 2, conn=None, commit: bool = True) -> dict[str, int]:
    """Fill odds_at_pick_live then odds_at_pick_available where NULL. Commits.
    `ids` = just these rows (the at-pick-time call). `commit=False` (dry run) leaves the
    transaction open on the caller's `conn`. Returns rows updated per statement."""
    if table not in TABLES:
        raise ValueError(table)
    from workers.jobs.daily_pipeline_v2 import (
        ACCESSIBLE_BOOKMAKERS, ODDS_MAX_AGE_HOURS, ODDS_MAX_LAG_HOURS, _NON_OFFERS)
    from workers.utils.odds_quality import BLACKLISTED_OU_SOURCES
    scope, params = _scope(ids, settled_only, recent_days)
    params = {**params, "books": sorted(ACCESSIBLE_BOOKMAKERS), "non_offers": sorted(_NON_OFFERS),
              "max_lag_h": ODDS_MAX_LAG_HOURS, "max_age_h": ODDS_MAX_AGE_HOURS,
              "ou_blacklist": sorted(BLACKLISTED_OU_SOURCES)}
    stmts = [
        ("odds_at_pick_live", _BEST.format(
            legs=_LEGS.format(table=table, column="odds_at_pick_live", scope=scope, prematch=_PREMATCH[table]),
            book_filter="o.bookmaker = ANY(%(books)s)", table=table,
            column="odds_at_pick_live", value="best.best")),
        ("odds_at_pick_available", _BEST.format(
            legs=_LEGS.format(table=table, column="odds_at_pick_available", scope=scope, prematch=_PREMATCH[table]),
            book_filter="o.bookmaker <> ALL(%(non_offers)s)", table=table,
            column="odds_at_pick_available", value="GREATEST(best.best, t.odds_at_pick_live)")),
        ("odds_at_pick_available_floor", _FLOOR.format(table=table, scope=scope, prematch=_PREMATCH[table])),
    ]
    out: dict[str, int] = {}

    def _run(c) -> None:
        try:
            with c.cursor() as cur:
                for name, sql in stmts:
                    cur.execute(sql, params)
                    out[name] = cur.rowcount
            if commit:
                c.commit()
        except Exception:
            c.rollback()
            raise

    if conn is not None:
        _run(conn)
    else:
        from workers.api_clients.db import get_conn
        with get_conn() as c:
            _run(c)
    return out
