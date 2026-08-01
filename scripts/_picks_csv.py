"""
Shared CSV helper for competitor + OddsIntel per-pick exports.

Every audit_vs_*.py writes an aggregate ledger/comparison_<source>.json. That's
enough for the landing head-to-head hero, but a visitor who wants to VERIFY
the aggregate has no way to see the underlying rows. This helper lets each
audit + the standalone OddsIntel dump write ledger/picks_<source>.csv with
a stable, minimal schema that the landing links to via
"Verify · view raw picks ↗".

Columns (all rows):
    source              : "deepbetting" | "signalodds" | "forebet" | "tipstrr" |
                          "winnerodds" | "oddsintel"
    kickoff_date        : YYYY-MM-DD (best available; empty if source doesn't
                          expose one, e.g. Tipstrr monthly aggregates)
    league              : short name if source publishes it, else empty
    home_team, away_team: strings; empty for sources that don't publish teams
                          per-pick (DeepBetting, Tipstrr)
    market              : "1x2" | "over_under_25" | source-specific string
    pick                : "home"|"draw"|"away"|"over"|"under" (or raw label)
    odds                : float; empty if not published
    result              : "won" | "lost" | "void" | "" (empty if unresolved)
    pnl_per_unit        : PnL per 1-unit stake (odds-1 on win, -1 on loss,
                          0 on void). Empty when odds or result is missing.
    ref_url             : deep link back to the source if publicly reachable
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

COLUMNS = [
    "source",
    "kickoff_date",
    "league",
    "home_team",
    "away_team",
    "market",
    "pick",
    "odds",
    "result",
    "pnl_per_unit",
    "ref_url",
]


def compute_pnl(odds: float | None, result: str | None) -> str:
    if odds is None or result not in ("won", "lost", "void"):
        return ""
    if result == "won":
        return f"{odds - 1:.4f}"
    if result == "lost":
        return "-1.0000"
    return "0.0000"


def write_picks_csv(path: Path, rows: Iterable[Mapping]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in COLUMNS})
            n += 1
    return n
