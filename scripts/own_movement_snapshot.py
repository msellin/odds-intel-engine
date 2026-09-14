#!/usr/bin/env python3
"""Freeze the pre-kickoff PRICE PATH for the own-book line-movement study.

WHY THIS SCRIPT EXISTS AT ALL. `prune_old_simple` keeps only three rows per
`(match, bookmaker, market, selection, handicap_line)` series after 7 days:
`is_opening`, `is_closing`, and the latest pre-kickoff row. Measured 2026-09-14,
rows-per-series at our three bettable books collapses from 5-45 inside the
window to **1.0** outside it (ANALYSIS_GOTCHAS §59a). So the intra-day price
path — the entire subject of a line-movement study — exists for roughly SEVEN
DAYS and is then destroyed. Any movement analysis that is not frozen to disk
first is not reproducible tomorrow, and re-running it next week would silently
measure a different (degenerate) universe.

This writes one parquet of every pre-kickoff quote in the window for the books
and markets the study uses, so the analysis is reproducible after the source
rows are gone.

Usage:
    python3 scripts/own_movement_snapshot.py --from 2026-09-05 --out data/own_movement_2026_09_14.parquet
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from workers.api_clients.db import execute_query

OWN_BOOKS = ["Coolbet", "Epicbet", "Unibet-Site"]

# Candidate anchors. 'Unibet' (AF) / 'Unibet-Kambi' / 'Max' / 'Avg' /
# 'Betfair Exchange' / 'BetWin' / 'Betfred' are EXCLUDED as phantom or
# aggregate feeds — see the OWN-path audit and DATA_SOURCES.md.
ANCHORS = ["Pinnacle", "Bet365", "1xBet", "Marathonbet", "Betfair",
           "William Hill", "Betano", "BetVictor", "SBO"]

MARKETS = ["1x2", "over_under_25", "over_under_35", "btts", "asian_handicap"]


def fetch(date_from: str) -> pd.DataFrame:
    rows = execute_query(
        """
        SELECT o.match_id, o.bookmaker, o.market, o.selection,
               o.handicap_line, o.odds::float AS odds, o.timestamp,
               o.is_opening, o.is_closing,
               m.date AS kickoff, m.status, m.league_id,
               m.score_home, m.score_away
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = ANY(%s)
           AND o.market = ANY(%s)
           AND m.date >= %s::date
           AND m.date < now()
           AND o.timestamp < m.date          -- real pre-kickoff bound (gotcha 37)
           AND o.is_live IS NOT TRUE
           AND o.odds > 1.0 AND o.odds < 100.0
        """,
        (OWN_BOOKS + ANCHORS, MARKETS, date_from),
    )
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-09-05")
    ap.add_argument("--out", default="data/own_movement_2026_09_14.parquet")
    a = ap.parse_args()

    df = fetch(a.date_from)
    if df.empty:
        print("no rows")
        return 2
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    df["handicap_line"] = df["handicap_line"].astype("float64")
    df.to_parquet(a.out, index=False)

    print(f"wrote {a.out}: {len(df):,} rows, "
          f"{df.match_id.nunique():,} fixtures, "
          f"{df.kickoff.min().date()} .. {df.kickoff.max().date()}")
    print(df.groupby("bookmaker").size().sort_values(ascending=False).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
